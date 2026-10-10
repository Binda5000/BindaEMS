"""Leistung der Verbraucher (core-Signal oder HA-Entität) und der Baum mit „Sonstiges“.

Home Assistant schreibt in InfluxDB je nach ``measurement_attr`` unter der Einheit (``W``,
``kW``; Standard) oder unter der Entität (``sensor.kueche``, Einheit im Feld
``unit_of_measurement_str``). Die Tags ``domain`` und ``entity_id`` (ohne Domain) und das Feld
``value`` sind in beiden Fällen gleich. Gelesen wird der jeweils letzte Wert.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import structlog

from bindaems.app.consumers.service import HA_REF_RE, Consumer
from bindaems.app.core_link import LiveState
from bindaems.app.history.influx import (
    InfluxQueryError,
    InfluxReader,
    Series,
    quote_ident,
    quote_str,
)
from bindaems.shared.config import Config

log = structlog.get_logger(__name__)

HaUnit = Literal["W", "kW"]
_UNITS: dict[str | None, HaUnit] = {"W": "W", "kW": "kW"}
_SCALE: dict[str, float] = {"W": 1.0, "kW": 1000.0}
CORE_CANDIDATE_RE = re.compile(r"^load\.[^.]+\.power_w$")
UNIT_FIELD: Final = "unit_of_measurement_str"
NO_VALUE: Final = "kein Wert in den letzten 24 h"
UNREADABLE: Final = "HA-Datenbank nicht lesbar"
NOT_CONFIGURED: Final = "HA-Datenbank nicht eingerichtet (influxdb.ha_database)"
# Messung je Entität: letzte Einheit je Reihe (ORDER BY … LIMIT 1 liest nur den jüngsten Block)
ENTITY_UNITS_QUERY: Final = (
    'SELECT "unit_of_measurement_str" AS "u" FROM /^[a-z_]+\\.[a-z0-9_]+$/ '
    'WHERE time > now() - 30d GROUP BY "domain","entity_id" ORDER BY time DESC LIMIT 1'
)
_UNESCAPED_COMMA = re.compile(r"(?<!\\),")
_UNESCAPED_EQUALS = re.compile(r"(?<!\\)=")
_ESCAPE = re.compile(r"\\([,= \\])")


@dataclass(frozen=True)
class HaRef:
    domain: str
    object_id: str
    unit: HaUnit

    @classmethod
    def from_consumer(cls, c: Consumer) -> HaRef:
        domain, _, object_id = c.power_ref.partition(".")
        unit = _UNITS.get(c.power_unit)
        if c.source_kind != "ha" or unit is None or not object_id:
            raise ValueError(f"Verbraucher {c.id} liest keine HA-Entität")
        return cls(domain, object_id, unit)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _cell(item: Series, row: list[Any], column: str) -> Any:
    index = item.columns.index(column) if column in item.columns else len(row)
    return row[index] if index < len(row) else None


def _entity(item: Series) -> tuple[str, str] | None:
    domain, object_id = item.tags.get("domain"), item.tags.get("entity_id")
    return (domain, object_id) if domain is not None and object_id is not None else None


class HaValueCache:
    """Letzte Leistungswerte der HA-Entitäten; ``refresh`` holt alle mit einer Abfrage."""

    def __init__(self, reader: InfluxReader, database: str) -> None:
        self._reader = reader
        self._database = database
        # je Entität Wert oder Grund; als Ganzes ersetzt (Endpunkte lesen aus Worker-Threads)
        self._readings: dict[HaRef, tuple[float | None, str | None]] = {}

    async def refresh(self, refs: Sequence[HaRef]) -> None:
        if not refs:
            self._readings = {}
            return
        entities = dict.fromkeys((ref.domain, ref.object_id) for ref in refs)
        # beide Schemata in einer Abfrage: je Einheit (immer W und kW) und je Entität
        names = ["W", "kW", *(f"{domain}.{object_id}" for domain, object_id in entities)]
        measurements = ",".join(quote_ident(name) for name in names)
        condition = " OR ".join(
            f"({quote_ident('domain')}={quote_str(domain)} AND "
            f"{quote_ident('entity_id')}={quote_str(object_id)})"
            for domain, object_id in entities
        )
        # Bezeichner und Werte sind maskiert
        query = (
            f'SELECT last("value") AS "v", last({quote_ident(UNIT_FIELD)}) AS "u" '  # noqa: S608
            f"FROM {measurements} WHERE time > now() - 24h AND ({condition}) "
            'GROUP BY "domain","entity_id"'
        )
        try:
            series = await self._reader.query(query, database=self._database)
        except InfluxQueryError as exc:
            self._readings = dict.fromkeys(refs, (None, UNREADABLE))
            log.warning("HA-Werte der Verbraucher nicht lesbar", error=str(exc))
            return
        found = _readings(series)
        result: dict[HaRef, tuple[float | None, str | None]] = {}
        for ref in refs:
            readings = found.get((ref.domain, ref.object_id), [])
            # ohne mitgeschriebene Einheit gilt die eingestellte
            value = next((v for unit, v in readings if unit in (None, ref.unit)), None)
            if value is not None:
                result[ref] = (value * _SCALE[ref.unit], None)
            elif readings:
                result[ref] = (None, f"HA meldet {readings[0][0]} statt {ref.unit}")
            else:
                result[ref] = (None, NO_VALUE)
        self._readings = result

    def power_w(self, ref: HaRef) -> float | None:
        return self._readings.get(ref, (None, None))[0]

    def note(self, ref: HaRef) -> str | None:
        """Warum ``ref`` keinen Wert hat; ``None``, solange die Entität nicht gelesen wurde."""
        return self._readings.get(ref, (None, None))[1]


def _readings(series: Sequence[Series]) -> dict[tuple[str, str], list[tuple[str | None, float]]]:
    """Letzte Werte je Entität mit der Einheit, die HA dazu meldet (``None``: nicht bekannt)."""
    found: dict[tuple[str, str], list[tuple[str | None, float]]] = {}
    for item in series:
        entity = _entity(item)
        if entity is None:
            continue
        for row in item.values:
            if item.name in _UNITS:  # Messung je Einheit
                unit: str | None = item.name
            elif item.name == ".".join(entity):  # Messung je Entität
                recorded = _cell(item, row, "u")
                unit = recorded if isinstance(recorded, str) else None
            else:
                continue
            value = _number(_cell(item, row, "v"))
            if value is not None:
                found.setdefault(entity, []).append((unit, value))
    return found


@dataclass
class TreeNode:
    id: int | None
    name: str
    color: str | None
    power_w: float | None
    note: str | None  # warum ``power_w`` fehlt
    other_w: float | None
    mismatch: bool
    children: list[TreeNode]


def _settle(node: TreeNode) -> TreeNode:
    """„Sonstiges“ eines Knotens: eigene Leistung minus Summe der Kinder."""
    if not node.children or node.power_w is None:
        return node
    parts = [child.power_w for child in node.children]
    if any(part is None for part in parts):
        return node
    other = node.power_w - sum(part for part in parts if part is not None)
    if other < 0:
        node.other_w, node.mismatch = 0.0, True  # Kinder messen mehr als das Elternelement
    else:
        node.other_w = other
    return node


def build_tree(
    consumers: Sequence[Consumer],
    power: Callable[[Consumer], float | None],
    house_w: float | None,
    note: Callable[[Consumer], str | None] = lambda _: None,
) -> TreeNode:
    """Baum unter der Wurzel „Haus“ mit „Sonstiges“ je Ebene."""
    ids = {consumer.id for consumer in consumers}
    children: dict[int | None, list[Consumer]] = {}
    for consumer in consumers:
        parent = consumer.parent_id if consumer.parent_id in ids else None
        children.setdefault(parent, []).append(consumer)

    def node(consumer: Consumer) -> TreeNode:
        return _settle(
            TreeNode(
                id=consumer.id,
                name=consumer.name,
                color=consumer.color,
                power_w=power(consumer),
                note=note(consumer),
                other_w=None,
                mismatch=False,
                children=[node(child) for child in children.get(consumer.id, [])],
            )
        )

    root = TreeNode(
        id=None,
        name="Haus",
        color=None,
        power_w=house_w,
        note=None,
        other_w=None,
        mismatch=False,
        children=[node(consumer) for consumer in children.get(None, [])],
    )
    return _settle(root)


def consumer_power(consumer: Consumer, live: LiveState, ha: HaValueCache | None) -> float | None:
    if consumer.source_kind == "core":
        return _number(live.value(consumer.power_ref))
    if ha is None:
        return None
    try:
        return ha.power_w(HaRef.from_consumer(consumer))
    except ValueError:
        return None


def consumer_note(consumer: Consumer, ha: HaValueCache | None) -> str | None:
    """Warum ein Verbraucher mit HA-Entität keinen Wert hat."""
    if consumer.source_kind != "ha":
        return None
    if ha is None:
        return NOT_CONFIGURED
    try:
        return ha.note(HaRef.from_consumer(consumer))
    except ValueError:
        return None


def _unescape(text: str) -> str:
    return _ESCAPE.sub(r"\1", text)


def _ha_entity(key: str) -> tuple[str, HaUnit] | None:
    """``W,domain=sensor,entity_id=kueche`` → (``sensor.kueche``, ``W``)."""
    measurement, *pairs = _UNESCAPED_COMMA.split(key)
    unit = _UNITS.get(_unescape(measurement))
    tags: dict[str, str] = {}
    for pair in pairs:
        parts = _UNESCAPED_EQUALS.split(pair, maxsplit=1)
        if len(parts) == 2:
            tags[_unescape(parts[0])] = _unescape(parts[1])
    entity_id = f"{tags.get('domain', '')}.{tags.get('entity_id', '')}"
    if unit is None or not HA_REF_RE.fullmatch(entity_id):
        return None
    return entity_id, unit


def _unit_entities(series: Sequence[Series]) -> set[tuple[str, HaUnit]]:
    """Messung je Einheit: Entitäten aus den Reihenschlüsseln von ``SHOW SERIES``."""
    found: set[tuple[str, HaUnit]] = set()
    for item in series:
        column = item.columns.index("key") if "key" in item.columns else 0
        for row in item.values:
            key = row[column] if column < len(row) else None
            entity = _ha_entity(key) if isinstance(key, str) else None
            if entity is not None:
                found.add(entity)
    return found


def _entity_units(series: Sequence[Series]) -> set[tuple[str, HaUnit]]:
    """Messung je Entität: Sensoren, deren letzte Einheit W oder kW ist."""
    found: set[tuple[str, HaUnit]] = set()
    for item in series:
        entity = _entity(item)
        entity_id = ".".join(entity) if entity is not None else ""
        if item.name != entity_id or not HA_REF_RE.fullmatch(entity_id):
            continue
        for row in item.values:
            recorded = _cell(item, row, "u")
            unit = _UNITS.get(recorded) if isinstance(recorded, str) else None
            if unit is not None:
                found.add((entity_id, unit))
    return found


async def candidates(cfg: Config, live: LiveState, reader: InfluxReader | None) -> dict[str, Any]:
    """Mögliche Leistungsquellen für neue Verbraucher; ``ha_error`` sagt, warum HA fehlt."""
    signals = live.state.get("signals") if live.state is not None else None
    core = {name for name in signals or {} if CORE_CANDIDATE_RE.fullmatch(name)}
    if cfg.homeassistant is not None:
        core |= {name for name in cfg.homeassistant.entities if name.endswith(".power_w")}
    ha: set[tuple[str, HaUnit]] = set()
    error: str | None = None
    database = cfg.influxdb.ha_database
    if database is None:
        error = NOT_CONFIGURED
    elif reader is not None:
        try:
            ha |= _unit_entities(await reader.query('SHOW SERIES FROM "W","kW"', database=database))
            ha |= _entity_units(await reader.query(ENTITY_UNITS_QUERY, database=database))
        except InfluxQueryError as exc:
            log.warning("HA-Leistungsquellen nicht lesbar", error=str(exc))
            error = f"HA-Datenbank „{database}“ nicht lesbar: {exc}"
    return {
        "core": [{"ref": ref} for ref in sorted(core)],
        "ha": [{"entity_id": entity_id, "unit": unit} for entity_id, unit in sorted(ha)],
        "ha_error": error,
    }
