"""Energie der Verbraucher seit Mitternacht (Wien), integriert aus dem Leistungsverlauf.

- **core-Signale** ``load|pv|wallbox.<id>.power_w`` schreibt der core als Messung ``power``
  (``phase=total``) alle 2 bis 10 s, die Hauslast als ``source=derived, id=house_load``. Gelesen
  werden Minutenmittel; jedes gilt für seine Minute, fehlende Minuten zählen nicht (Lücke).
- **HA-Entitäten** schreibt Home Assistant nur bei Änderung. Ein Wert gilt bis zum nächsten,
  der letzte vor Mitternacht ab Mitternacht.

Alle Werte einer Art kommen mit einer Abfrage; ``refresh`` ersetzt das Ergebnis als Ganzes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final

import structlog

from bindaems.app.consumers.service import Consumer
from bindaems.app.consumers.values import HaRef
from bindaems.app.history.influx import (
    RP_RAW,
    InfluxQueryError,
    InfluxReader,
    Series,
    quote_ident,
    quote_str,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

BUCKET_S: Final = 60
HA_SEED: Final = timedelta(days=7)  # so weit zurück wird der Wert vor Mitternacht gesucht
CORE_POWER_RE = re.compile(r"^(?P<source>load|pv|wallbox)\.(?P<id>[^.]+)\.power_w$")
HOUSE: Final = ("derived", "house_load")
NO_SERIES: Final = "Signal wird nicht aufgezeichnet"
NO_HISTORY: Final = "kein Verlauf seit Mitternacht"
UNREADABLE: Final = "Verlauf nicht lesbar"
_SCALE: dict[str, float] = {"W": 1.0, "kW": 1000.0}

Key = tuple[str, str, str | None]  # Quelle, Signal oder Entität, Einheit
Point = tuple[int, float]  # Millisekunden, Watt


def day_start(now: datetime) -> datetime:
    """Mitternacht in Wien vor ``now`` (UTC)."""
    local = now.astimezone(LOCAL_TZ)
    return datetime.combine(local.date(), time(0), LOCAL_TZ).astimezone(UTC)


def integrate_wh(points: Sequence[Point], end_ms: int, hold_ms: int | None) -> float:
    """Energie in Wh: jeder Wert gilt bis zum nächsten, höchstens ``hold_ms`` und bis zum Ende."""
    total = 0.0
    ordered = sorted(points)
    for index, (start, watts) in enumerate(ordered):
        until = ordered[index + 1][0] if index + 1 < len(ordered) else end_ms
        if hold_ms is not None:
            until = min(until, start + hold_ms)
        until = min(until, end_ms)
        if until > start:
            total += watts * (until - start) / 3_600_000
    return total


def _key(consumer: Consumer) -> Key:
    return (consumer.source_kind, consumer.power_ref, consumer.power_unit)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _points(item: Series, scale: float) -> list[Point]:
    column = item.columns.index("w") if "w" in item.columns else 1
    points: list[Point] = []
    for row in item.values:
        value = _number(row[column]) if column < len(row) else None
        if value is not None and isinstance(row[0], int):
            points.append((row[0], value * scale))
    return points


@dataclass(frozen=True)
class _Window:
    start_ms: int
    end_ms: int

    def condition(self) -> str:
        return f"time >= {self.start_ms}ms AND time < {self.end_ms}ms"


class EnergyCache:
    """Energie je Verbraucher und der Hauslast seit Mitternacht."""

    def __init__(self, reader: InfluxReader, clock: Clock, ha_database: str | None) -> None:
        self._reader = reader
        self._clock = clock
        self._ha_database = ha_database
        # je Verbraucherquelle kWh oder Grund; als Ganzes ersetzt (Endpunkte lesen aus Threads)
        self._energy: dict[Key, tuple[float | None, str | None]] = {}
        self._house: float | None = None
        self._since: datetime | None = None

    @property
    def since(self) -> datetime | None:
        """Beginn des Zeitraums der letzten Berechnung (``None``: noch keine)."""
        return self._since

    def energy_kwh(self, consumer: Consumer) -> float | None:
        return self._energy.get(_key(consumer), (None, None))[0]

    def note(self, consumer: Consumer) -> str | None:
        """Warum ``consumer`` keine Energie hat; ``None``, solange nichts berechnet wurde."""
        return self._energy.get(_key(consumer), (None, None))[1]

    def house_kwh(self) -> float | None:
        return self._house

    async def refresh(self, consumers: Iterable[Consumer]) -> None:
        now = self._clock.now()
        since = day_start(now)
        window = _Window(epoch_ms(since), epoch_ms(now))
        core: dict[Key, tuple[str, str]] = {}
        ha: dict[Key, HaRef] = {}
        result: dict[Key, tuple[float | None, str | None]] = {}
        for consumer in consumers:
            key = _key(consumer)
            if consumer.source_kind == "core":
                found = CORE_POWER_RE.fullmatch(consumer.power_ref)
                if found is None:
                    result[key] = (None, NO_SERIES)
                else:
                    core[key] = (found["source"], found["id"])
            else:
                try:
                    ha[key] = HaRef.from_consumer(consumer)
                except ValueError:
                    result[key] = (None, NO_SERIES)
        house: float | None = None
        try:
            series = await self._core_points([*core.values(), HOUSE], window)
        except InfluxQueryError as exc:
            log.warning("Energie der Verbraucher nicht lesbar", error=str(exc))
            result |= dict.fromkeys(core, (None, UNREADABLE))
        else:
            for key, tags in core.items():
                result[key] = self._kwh(series.get(tags), window, BUCKET_S * 1000)
            house = self._kwh(series.get(HOUSE), window, BUCKET_S * 1000)[0]
        if ha:
            result |= await self._ha_energy(ha, window)
        self._energy, self._house, self._since = result, house, since

    @staticmethod
    def _kwh(
        points: Sequence[Point] | None, window: _Window, hold_ms: int | None
    ) -> tuple[float | None, str | None]:
        if not points:
            return (None, NO_HISTORY)
        return (round(integrate_wh(points, window.end_ms, hold_ms) / 1000, 3), None)  # auf Wh

    async def _core_points(
        self, tags: Sequence[tuple[str, str]], window: _Window
    ) -> dict[tuple[str, str], list[Point]]:
        condition = " OR ".join(
            f'("source"={quote_str(source)} AND "id"={quote_str(id_)})'
            for source, id_ in dict.fromkeys(tags)
        )
        # Bezeichner und Werte sind maskiert
        query = (
            f'SELECT mean("p_w") AS "w" FROM {quote_ident(RP_RAW)}."power" '  # noqa: S608
            f"WHERE \"phase\"='total' AND ({condition}) AND {window.condition()} "
            f'GROUP BY time({BUCKET_S}s),"source","id" fill(none)'
        )
        found: dict[tuple[str, str], list[Point]] = {}
        for item in await self._reader.query(query):
            source, id_ = item.tags.get("source"), item.tags.get("id")
            if source is not None and id_ is not None:
                found.setdefault((source, id_), []).extend(_points(item, 1.0))
        return found

    async def _ha_energy(
        self, refs: dict[Key, HaRef], window: _Window
    ) -> dict[Key, tuple[float | None, str | None]]:
        database = self._ha_database
        if database is None:
            return dict.fromkeys(refs, (None, NO_SERIES))
        entities = dict.fromkeys((ref.domain, ref.object_id) for ref in refs.values())
        names = ["W", "kW", *(f"{domain}.{object_id}" for domain, object_id in entities)]
        measurements = ",".join(quote_ident(name) for name in names)
        condition = " OR ".join(
            f"({quote_ident('domain')}={quote_str(domain)} AND "
            f"{quote_ident('entity_id')}={quote_str(object_id)})"
            for domain, object_id in entities
        )
        seed = _Window(window.start_ms - int(HA_SEED.total_seconds() * 1000), window.start_ms)
        # Bezeichner und Werte sind maskiert
        means = (
            f'SELECT mean("value") AS "w" FROM {measurements} '  # noqa: S608
            f"WHERE {window.condition()} AND ({condition}) "
            f'GROUP BY time({BUCKET_S}s),"domain","entity_id" fill(none)'
        )
        before = (
            f'SELECT last("value") AS "w" FROM {measurements} '  # noqa: S608
            f'WHERE {seed.condition()} AND ({condition}) GROUP BY "domain","entity_id"'
        )
        try:
            today = await self._reader.query(means, database=database)
            earlier = await self._reader.query(before, database=database)
        except InfluxQueryError as exc:
            log.warning("Energie der HA-Verbraucher nicht lesbar", error=str(exc))
            return dict.fromkeys(refs, (None, UNREADABLE))
        result: dict[Key, tuple[float | None, str | None]] = {}
        for key, ref in refs.items():
            points = _ha_points(today, ref)
            # der letzte Wert vor Mitternacht gilt ab Mitternacht
            seeds = sorted(_ha_points(earlier, ref))
            if seeds:
                points.append((window.start_ms, seeds[-1][1]))
            result[key] = self._kwh(points, window, None)
        return result


def _ha_points(series: Sequence[Series], ref: HaRef) -> list[Point]:
    """Punkte einer Entität aus beiden Schemata (Messung je Einheit oder je Entität)."""
    points: list[Point] = []
    for item in series:
        if (item.tags.get("domain"), item.tags.get("entity_id")) != (ref.domain, ref.object_id):
            continue
        scale = _SCALE.get(item.name)
        if scale is None and item.name == f"{ref.domain}.{ref.object_id}":
            scale = _SCALE[ref.unit]
        if scale is not None:
            points.extend(_points(item, scale))
    return points
