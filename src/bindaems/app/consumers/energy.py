"""Energie der Verbraucher seit Mitternacht (Wien), integriert aus dem Leistungsverlauf.

- **core-Signale** ``load|pv|wallbox.<id>.power_w`` schreibt der core als Messung ``power``
  (``phase=total``) alle 2 bis 10 s, die Hauslast als ``source=derived, id=house_load``. Gelesen
  werden Minutenmittel; jedes gilt für seine Minute, fehlende Minuten zählen nicht (Lücke).
- **HA-Entitäten** schreibt Home Assistant nur bei Änderung. Ein Wert gilt bis zum nächsten,
  der letzte vor Mitternacht ab Mitternacht.

Alle Werte einer Art kommen mit einer Abfrage; ``refresh`` ersetzt das Ergebnis als Ganzes,
``window_energy`` rechnet beliebige Zeiträume (auch vergangene Tage) und ändert den Cache nicht.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Final, NamedTuple

import structlog

from bindaems.app.consumers.source import PowerSource
from bindaems.app.consumers.values import Energy, HaRef, HaValueCache, ha_selection, number
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
CORE_POWER_RE = re.compile(
    r"^(?:(?P<source>load|pv|wallbox)\.(?P<id>[^.]+)|(?P<dc>pv_dc))\.power_w$"
)
HOUSE: Final = ("derived", "house_load")
PV_TOTAL: Final = ("derived", "pv_total")
NO_SERIES: Final = "Signal wird nicht aufgezeichnet"
NO_HISTORY: Final = "kein Verlauf seit Mitternacht"
UNREADABLE: Final = "Verlauf nicht lesbar"
_SCALE: dict[str, float] = {"W": 1.0, "kW": 1000.0}

Key = tuple[str, str, str | None]  # Quelle, Signal oder Entität, Einheit
Point = tuple[int, float]  # Millisekunden, Watt


def core_tags(ref: str) -> tuple[str, str] | None:
    """Tags ``(source, id)`` der Reihe eines core-Leistungssignals; ``None``: nicht aufgezeichnet.

    ``pv_dc.power_w`` (DC-gekoppelte PV) schreibt der core als ``source=pv, id=dc``."""
    found = CORE_POWER_RE.fullmatch(ref)
    if found is None:
        return None
    if found["dc"] is not None:
        return ("pv", "dc")
    return (found["source"], found["id"])


def source_energy(
    source: PowerSource, ha: HaValueCache | None, energy: EnergyCache | None
) -> Energy:
    """Energie seit Mitternacht einer Quelle (kWh, Grund); ein HA-Sensor mit falscher Einheit
    hat keine, denn sein Verlauf hätte dieselbe falsche Einheit."""
    if energy is None:
        return (None, None)
    if source.source_kind == "ha" and ha is not None and source.power_unit is not None:
        ref = HaRef.from_source(source)
        if ha.wrong_unit(ref):
            return (None, ha.note(ref))
    return (energy.energy_kwh(source), energy.note(source))


class WindowEnergy(NamedTuple):
    """Ergebnis von ``EnergyCache.window_energy``."""

    per_source: dict[int, tuple[float | None, str | None]]
    house_kwh: float | None
    pv_total_kwh: float | None
    pv_total_note: str | None = None  # Grund ohne Wert: NO_HISTORY oder UNREADABLE


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


def _key(source: PowerSource) -> Key:
    return (source.source_kind, source.power_ref, source.power_unit)


def _points(
    item: Series, scale: float, unit_column: bool = False, rejected: set[str] | None = None
) -> list[Point]:
    """Punkte der Spalte ``w``; mit ``unit_column`` bestimmt die Einheit in Spalte ``u``
    (``W`` oder ``kW``) den Faktor je Zeile. Ohne Einheit in der Zeile gilt ``scale``; eine
    andere Einheit (z. B. ``kWh``, ``%``) ist keine Leistung: die Zeile entfällt und die Einheit
    kommt in ``rejected``."""
    column = item.columns.index("w") if "w" in item.columns else 1
    unit = item.columns.index("u") if unit_column and "u" in item.columns else None
    points: list[Point] = []
    for row in item.values:
        value = number(row[column]) if column < len(row) else None
        if value is not None and isinstance(row[0], int):
            recorded = row[unit] if unit is not None and unit < len(row) else None
            if isinstance(recorded, str):
                if recorded not in _SCALE:
                    if rejected is not None:
                        rejected.add(recorded)
                    continue
                factor = _SCALE[recorded]
            else:
                factor = scale
            points.append((row[0], value * factor))
    return points


def time_window(start_ms: int, end_ms: int) -> str:
    """``WHERE``-Bedingung für ``[start_ms, end_ms)``."""
    return f"time >= {start_ms}ms AND time < {end_ms}ms"


async def core_power_points(
    reader: InfluxReader,
    retention: str,
    tags: Sequence[tuple[str, str]],
    time_condition: str,
    step_s: int,
) -> dict[tuple[str, str], list[Point]]:
    """Mittel von ``p_w`` je ``step_s`` für core-Reihen ``(source, id)`` (Messung ``power``,
    ``phase=total``) aus ``retention``; Schritte ohne Wert fehlen."""
    condition = " OR ".join(
        f'("source"={quote_str(source)} AND "id"={quote_str(id_)})'
        for source, id_ in dict.fromkeys(tags)
    )
    # Bezeichner und Werte sind maskiert
    query = (
        f'SELECT mean("p_w") AS "w" FROM {quote_ident(retention)}."power" '  # noqa: S608
        f"WHERE \"phase\"='total' AND ({condition}) AND {time_condition} "
        f'GROUP BY time({step_s}s),"source","id" fill(none)'
    )
    found: dict[tuple[str, str], list[Point]] = {}
    for item in await reader.query(query):
        source, id_ = item.tags.get("source"), item.tags.get("id")
        if source is not None and id_ is not None:
            found.setdefault((source, id_), []).extend(_points(item, 1.0))
    return found


def ha_mean_query(measurements: str, condition: str, time_condition: str, step_s: int) -> str:
    """Mittel von ``value`` je ``step_s`` für die Entitäten aus ``ha_selection``."""
    return (
        f'SELECT mean("value") AS "w" FROM {measurements} '  # noqa: S608
        f"WHERE {time_condition} AND ({condition}) "
        f'GROUP BY time({step_s}s),"domain","entity_id" fill(none)'
    )


@dataclass(frozen=True)
class _Window:
    start_ms: int
    end_ms: int

    def condition(self) -> str:
        return time_window(self.start_ms, self.end_ms)


class EnergyCache:
    """Energie je Quelle, der Hauslast und der PV gesamt seit Mitternacht.

    ``retention`` ist die Aufbewahrung, aus der die core-Minutenmittel kommen (``raw`` oder
    ``long``)."""

    def __init__(
        self,
        reader: InfluxReader,
        clock: Clock,
        ha_database: str | None,
        *,
        retention: str = RP_RAW,
    ) -> None:
        self._reader = reader
        self._retention = retention
        self._clock = clock
        self._ha_database = ha_database
        # je Verbraucherquelle kWh oder Grund; als Ganzes ersetzt (Endpunkte lesen aus Threads)
        self._energy: dict[Key, tuple[float | None, str | None]] = {}
        self._house: float | None = None
        self._pv_total: float | None = None
        self._since: datetime | None = None

    @property
    def since(self) -> datetime | None:
        """Beginn des Zeitraums der letzten Berechnung (``None``: noch keine)."""
        return self._since

    def energy_kwh(self, source: PowerSource) -> float | None:
        return self._energy.get(_key(source), (None, None))[0]

    def note(self, source: PowerSource) -> str | None:
        """Warum ``source`` keine Energie hat; ``None``, solange nichts berechnet wurde."""
        return self._energy.get(_key(source), (None, None))[1]

    def house_kwh(self) -> float | None:
        return self._house

    def pv_total_kwh(self) -> float | None:
        return self._pv_total

    async def refresh(self, sources: Iterable[PowerSource]) -> None:
        """Rechnet ``day_start(now) … now`` und ersetzt das Ergebnis als Ganzes."""
        sources = list(sources)
        now = self._clock.now()
        since = day_start(now)
        result = await self.window_energy(sources, since, now)
        self._energy = {_key(source): result.per_source[source.id] for source in sources}
        self._house, self._pv_total, self._since = result.house_kwh, result.pv_total_kwh, since

    async def window_energy(
        self, sources: Iterable[PowerSource], start: datetime, end: datetime
    ) -> WindowEnergy:
        """Energie je Quelle (Schlüssel: ``.id``), der Hauslast und der PV gesamt in
        ``[start, end)``.

        Je Quelle kWh oder der Grund, warum es keine gibt. Der Cache bleibt unberührt; das
        Ergebnis taugt auch für vergangene Tage (``refresh`` ruft mit ``day_start(now) … now``).
        """
        window = _Window(epoch_ms(start), epoch_ms(end))
        core: dict[Key, tuple[str, str]] = {}
        ha: dict[Key, HaRef] = {}
        by_key: dict[Key, tuple[float | None, str | None]] = {}
        sources = list(sources)
        for source in sources:
            key = _key(source)
            if source.source_kind == "core":
                tags = core_tags(source.power_ref)
                if tags is None:
                    by_key[key] = (None, NO_SERIES)
                else:
                    core[key] = tags
            else:
                try:
                    ha[key] = HaRef.from_source(source)
                except ValueError:
                    by_key[key] = (None, NO_SERIES)
        house: float | None = None
        pv_total: float | None = None
        pv_note: str | None = UNREADABLE
        try:
            series = await self._core_points([*core.values(), HOUSE, PV_TOTAL], window)
        except InfluxQueryError as exc:
            log.warning("Energie der Verbraucher nicht lesbar", error=str(exc))
            by_key |= dict.fromkeys(core, (None, UNREADABLE))
        else:
            for key, tags in core.items():
                by_key[key] = self._kwh(series.get(tags), window, BUCKET_S * 1000)
            house = self._kwh(series.get(HOUSE), window, BUCKET_S * 1000)[0]
            pv_total, pv_note = self._kwh(series.get(PV_TOTAL), window, BUCKET_S * 1000)
        if ha:
            by_key |= await self._ha_energy(ha, window)
        return WindowEnergy(
            {source.id: by_key[_key(source)] for source in sources}, house, pv_total, pv_note
        )

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
        return await core_power_points(
            self._reader, self._retention, tags, window.condition(), BUCKET_S
        )

    async def _ha_energy(
        self, refs: dict[Key, HaRef], window: _Window
    ) -> dict[Key, tuple[float | None, str | None]]:
        database = self._ha_database
        if database is None:
            return dict.fromkeys(refs, (None, NO_SERIES))
        measurements, condition = ha_selection(list(refs.values()))
        seed = _Window(window.start_ms - int(HA_SEED.total_seconds() * 1000), window.start_ms)
        # Bezeichner und Werte sind maskiert
        means = ha_mean_query(measurements, condition, window.condition(), BUCKET_S)
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
            points = ha_points(today, ref)
            # der letzte Wert vor Mitternacht gilt ab Mitternacht
            seeds = sorted(ha_points(earlier, ref))
            if seeds:
                points.append((window.start_ms, seeds[-1][1]))
            result[key] = self._kwh(points, window, None)
        return result


def ha_points(
    series: Sequence[Series], ref: HaRef, rejected: set[str] | None = None
) -> list[Point]:
    """Punkte einer Entität aus beiden Schemata (Messung je Einheit oder je Entität).

    Bei der Messung je Entität gilt die Einheit aus Spalte ``u``, falls die Abfrage sie liefert,
    sonst die von ``ref``. Zeilen mit anderer Einheit entfallen; ihre Einheiten kommen in
    ``rejected``."""
    points: list[Point] = []
    for item in series:
        if (item.tags.get("domain"), item.tags.get("entity_id")) != (ref.domain, ref.object_id):
            continue
        scale = _SCALE.get(item.name)
        if scale is None and item.name == f"{ref.domain}.{ref.object_id}":
            scale = _SCALE[ref.unit]
            per_entity = True
        else:
            per_entity = False
        if scale is not None:
            points.extend(_points(item, scale, per_entity, rejected))
    return points
