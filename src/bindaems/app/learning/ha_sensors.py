"""HA-Sensoren als Merkmale des Lastmodells (Plan 5a, Entscheidung 7).

Gelesen wird die HA-Datenbank wie bei den Verbrauchern, aber mit beliebigen Einheiten: Zahlen
(``°C``, ``%``) und Zustände, die HA als Zahl schreibt (an/aus, zu Hause/weg), stehen im Feld
``value``. Die Einheiten werden nicht umgerechnet. HA schreibt entweder je Einheit eine Messung
(``°C``) oder je Entität (``sensor.aussentemperatur``, Einheit im Feld ``unit_of_measurement_str``).
``candidates`` merkt sich die Messungen je Einheit, damit ``slot_values`` auch sie abfragt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import structlog

from bindaems.app.consumers.service import HA_REF_RE
from bindaems.app.consumers.values import NOT_CONFIGURED, ha_ref, number
from bindaems.app.forecast.load import MinuteMeans, ha_query, seed_query, slot_mean
from bindaems.app.history.influx import InfluxQueryError, InfluxReader, Series
from bindaems.shared.timeutil import slots_between

log = structlog.get_logger(__name__)

NO_READER: Final = "Home Assistant ist nicht eingerichtet."  # kein Leser: Liste fehlt

# letzter Wert und letzte Einheit je Entität und Messung der letzten 30 Tage
_CANDIDATES_QUERY = (
    'SELECT last("value") AS "v", last("unit_of_measurement_str") AS "u" FROM /.*/ '
    'WHERE time > now() - 30d GROUP BY "domain","entity_id"'
)


@dataclass(frozen=True)
class SensorCandidate:
    entity_id: str
    unit: str | None


def _cell(item: Series, row: list[Any], column: str) -> Any:
    index = item.columns.index(column) if column in item.columns else len(row)
    return row[index] if index < len(row) else None


def _entity_id(item: Series) -> str | None:
    """``sensor.x`` aus den Tags ``domain`` und ``entity_id``; sonst ``None``."""
    domain, object_id = item.tags.get("domain"), item.tags.get("entity_id")
    entity_id = f"{domain}.{object_id}" if domain is not None and object_id is not None else ""
    return entity_id if HA_REF_RE.fullmatch(entity_id) else None


def _minute_means(
    series: Sequence[Series], earlier: Sequence[Series], entity_id: str, units: Sequence[str]
) -> MinuteMeans:
    """Minutenmittel und Wert vor dem Bereich einer Entität aus beiden Schemata (Messung je
    Einheit aus ``units`` oder je Entität), ohne Umrechnung der Einheit."""

    def points(found: Sequence[Series]) -> list[tuple[int, float]]:
        result: list[tuple[int, float]] = []
        for item in found:
            if _entity_id(item) != entity_id or item.name not in (entity_id, *units):
                continue
            for row in item.values:
                value = number(_cell(item, row, "w"))
                if value is not None and isinstance(row[0], int):
                    result.append((row[0], value))
        return result

    minutes = dict(points(series))
    seeds = sorted(points(earlier))
    return MinuteMeans(minutes, min(minutes, default=None), seeds[-1][1] if seeds else None)


class HaSensorReader:
    """Liest Sensoren aus der HA-Datenbank; ohne Datenbank oder Leser bleibt alles leer."""

    def __init__(self, reader: InfluxReader | None, database: str | None) -> None:
        self._reader = reader
        self._database = database
        self._units: tuple[str, ...] = ()  # Messungen je Einheit, die ``candidates`` fand

    async def candidates(self) -> tuple[list[SensorCandidate], str | None]:
        """Entitäten mit Zahlenwert in den letzten 30 Tagen, nach Entität sortiert, und der
        Grund, warum die Liste fehlt (``None``, wenn nichts schiefging)."""
        if self._database is None:
            return [], NOT_CONFIGURED
        if self._reader is None:
            return [], NO_READER
        try:
            series = await self._reader.query(_CANDIDATES_QUERY, database=self._database)
        except InfluxQueryError as exc:
            log.warning("HA-Sensoren nicht lesbar", error=str(exc))
            return [], f"HA-Datenbank „{self._database}“ nicht lesbar: {exc}"
        newest: dict[str, tuple[int, SensorCandidate]] = {}  # je Entität die jüngste Reihe
        units: set[str] = set()
        for item in series:
            entity_id = _entity_id(item)
            if entity_id is None:
                continue
            per_entity = item.name == entity_id
            for row in item.values:
                if number(_cell(item, row, "v")) is None:
                    continue
                recorded = _cell(item, row, "u")
                unit = recorded if per_entity and isinstance(recorded, str) else None
                if not per_entity:
                    unit = item.name  # Messung je Einheit: der Name der Messung ist die Einheit
                    units.add(item.name)
                stamp = row[0] if isinstance(row[0], int) else 0
                if entity_id not in newest or stamp >= newest[entity_id][0]:
                    newest[entity_id] = (stamp, SensorCandidate(entity_id, unit))
        self._units = tuple(sorted(units))
        return [newest[entity_id][1] for entity_id in sorted(newest)], None

    async def slot_values(
        self, entities: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, dict[datetime, float]]:
        """Mittel je Slot in ``[start, end)`` aus den Minutenmitteln (``fill(previous)``, der Wert
        vor dem Bereich gilt bis zum ersten Punkt). Entitäten ohne Werte fehlen im Ergebnis; ist
        die Abfrage nicht möglich, ist das Ergebnis leer."""
        wanted = [e for e in dict.fromkeys(entities) if HA_REF_RE.fullmatch(e)]
        if not wanted or self._database is None or self._reader is None:
            return {}
        refs = [ha_ref(entity_id) for entity_id in wanted]
        try:
            series = await self._reader.query(
                ha_query(refs, start, end, units=self._units), database=self._database
            )
            # HA schreibt nur bei Änderung: der Wert vor dem Bereich gilt bis zum ersten Punkt
            earlier = await self._reader.query(
                seed_query(refs, start, units=self._units), database=self._database
            )
        except InfluxQueryError as exc:
            log.warning("HA-Sensoren nicht lesbar", error=str(exc))
            return {}
        slots = slots_between(start, end)
        result: dict[str, dict[datetime, float]] = {}
        for entity_id in wanted:
            means = _minute_means(series, earlier, entity_id, self._units)
            values = {slot: mean for slot in slots if (mean := slot_mean(means, slot)) is not None}
            if values:
                result[entity_id] = values
        return result
