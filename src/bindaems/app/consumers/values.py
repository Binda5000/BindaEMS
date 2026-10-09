"""Leistung der Verbraucher (core-Signal oder HA-Entität) und der Baum mit „Sonstiges“.

Home Assistant schreibt in InfluxDB je Einheit ein Measurement (``W``, ``kW``) mit den Tags
``domain`` und ``entity_id`` (ohne Domain). Gelesen wird der jeweils letzte Wert.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from bindaems.app.consumers.service import HA_REF_RE, Consumer
from bindaems.app.core_link import LiveState
from bindaems.app.history.influx import InfluxQueryError, InfluxReader, quote_ident, quote_str
from bindaems.shared.config import Config

log = structlog.get_logger(__name__)

HaUnit = Literal["W", "kW"]
_UNITS: dict[str | None, HaUnit] = {"W": "W", "kW": "kW"}
_SCALE: dict[str, float] = {"W": 1.0, "kW": 1000.0}
CORE_CANDIDATE_RE = re.compile(r"^load\.[^.]+\.power_w$")
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


class HaValueCache:
    """Letzte Leistungswerte der HA-Entitäten; ``refresh`` holt alle mit einer Abfrage."""

    def __init__(self, reader: InfluxReader, database: str) -> None:
        self._reader = reader
        self._database = database
        self._values: dict[HaRef, float] = {}

    async def refresh(self, refs: Sequence[HaRef]) -> None:
        if not refs:
            self._values = {}
            return
        units = ",".join(quote_ident(unit) for unit in sorted({ref.unit for ref in refs}))
        entities = dict.fromkeys((ref.domain, ref.object_id) for ref in refs)
        condition = " OR ".join(
            f"({quote_ident('domain')}={quote_str(domain)} AND "
            f"{quote_ident('entity_id')}={quote_str(object_id)})"
            for domain, object_id in entities
        )
        # Bezeichner und Werte sind maskiert
        query = (
            f'SELECT last("value") AS "v" FROM {units} WHERE time > now() - 24h AND '  # noqa: S608
            f'({condition}) GROUP BY "domain","entity_id"'
        )
        try:
            series = await self._reader.query(query, database=self._database)
        except InfluxQueryError as exc:
            self._values = {}
            log.warning("HA-Werte der Verbraucher nicht lesbar", error=str(exc))
            return
        values: dict[HaRef, float] = {}
        for item in series:
            unit = _UNITS.get(item.name)
            domain, object_id = item.tags.get("domain"), item.tags.get("entity_id")
            if unit is None or domain is None or object_id is None or "v" not in item.columns:
                continue
            column = item.columns.index("v")
            for row in item.values:
                value = _number(row[column]) if column < len(row) else None
                if value is not None:
                    values[HaRef(domain, object_id, unit)] = value * _SCALE[unit]
        self._values = values

    def power_w(self, ref: HaRef) -> float | None:
        return self._values.get(ref)


@dataclass
class TreeNode:
    id: int | None
    name: str
    color: str | None
    power_w: float | None
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


async def candidates(
    cfg: Config, live: LiveState, reader: InfluxReader | None
) -> dict[str, list[dict[str, str]]]:
    """Mögliche Leistungsquellen für neue Verbraucher."""
    signals = live.state.get("signals") if live.state is not None else None
    core = {name for name in signals or {} if CORE_CANDIDATE_RE.fullmatch(name)}
    if cfg.homeassistant is not None:
        core |= {name for name in cfg.homeassistant.entities if name.endswith(".power_w")}
    ha: set[tuple[str, HaUnit]] = set()
    database = cfg.influxdb.ha_database
    if reader is not None and database is not None:
        try:
            series = await reader.query('SHOW SERIES FROM "W","kW"', database=database)
        except InfluxQueryError as exc:
            log.warning("HA-Leistungsquellen nicht lesbar", error=str(exc))
            series = []
        for item in series:
            column = item.columns.index("key") if "key" in item.columns else 0
            for row in item.values:
                key = row[column] if column < len(row) else None
                entity = _ha_entity(key) if isinstance(key, str) else None
                if entity is not None:
                    ha.add(entity)
    return {
        "core": [{"ref": ref} for ref in sorted(core)],
        "ha": [{"entity_id": entity_id, "unit": unit} for entity_id, unit in sorted(ha)],
    }
