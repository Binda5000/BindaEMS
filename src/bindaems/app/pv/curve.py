"""Tageskurve je PV-Quelle in 5-Minuten-Schritten (Leistung in W).

core-Signale kommen als Mittel von ``p_w`` aus RP ``long`` (jeder Tag abrufbar), HA-Entitäten als
Mittel von ``value`` aus der HA-Datenbank, umgerechnet nach W. Der Tag ist
``[lokale Mitternacht, nächste lokale Mitternacht)``, an Tagen der Zeitumstellung also 23 oder
25 Stunden. Schritte ohne Wert (auch ``unavailable``) fehlen in der Kurve.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

import structlog

from bindaems.app.consumers.energy import (
    NO_SERIES,
    UNREADABLE,
    Point,
    core_power_points,
    core_tags,
    ha_mean_query,
    ha_points,
    time_window,
)
from bindaems.app.consumers.values import NOT_CONFIGURED, HaRef, ha_selection
from bindaems.app.history.influx import RP_LONG, InfluxQueryError, InfluxReader
from bindaems.app.pv.service import PvSource
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import local_day_slots

log = structlog.get_logger(__name__)

CURVE_STEP_S: Final = 300


class DayCurves(dict[int, list[Point]]):
    """Punkte (ms, W) je Quelle; ``notes`` nennt, warum eine Kurve leer bleibt."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: dict[int, str] = {}

    def fail(self, source: PvSource, note: str) -> None:
        self[source.id] = []
        self.notes[source.id] = note


async def day_curve(
    reader: InfluxReader,
    ha_database: str | None,
    sources: Sequence[PvSource],
    day: date,
) -> DayCurves:
    """Kurve von ``day`` je Quelle (Schlüssel: Quell-ID, jede Quelle kommt vor)."""
    start = epoch_ms(local_day_slots(day)[0])
    end = epoch_ms(local_day_slots(day + timedelta(days=1))[0])
    window = time_window(start, end)
    curves = DayCurves()
    core = [(s, core_tags(s.power_ref)) for s in sources if s.source_kind == "core"]
    for source, tags in core:
        if tags is None:
            curves.fail(source, NO_SERIES)
    wanted = [(s, tags) for s, tags in core if tags is not None]
    if wanted:
        await _core(reader, wanted, window, curves)
    has = [s for s in sources if s.source_kind == "ha"]
    if has and ha_database is None:
        for source in has:
            curves.fail(source, NOT_CONFIGURED)
    elif has and ha_database is not None:
        await _ha(reader, ha_database, has, window, curves)
    return curves


async def _core(
    reader: InfluxReader,
    wanted: Sequence[tuple[PvSource, tuple[str, str]]],
    window: str,
    curves: DayCurves,
) -> None:
    try:
        found = await core_power_points(
            reader, RP_LONG, [tags for _, tags in wanted], window, CURVE_STEP_S
        )
    except InfluxQueryError as exc:
        log.warning("PV-Tageskurve (core) nicht lesbar", error=str(exc))
        for source, _ in wanted:
            curves.fail(source, UNREADABLE)
        return
    for source, tags in wanted:
        curves[source.id] = sorted(found.get(tags, []))


async def _ha(
    reader: InfluxReader,
    database: str,
    sources: Sequence[PvSource],
    window: str,
    curves: DayCurves,
) -> None:
    refs = {s.id: HaRef.from_source(s) for s in sources}
    measurements, condition = ha_selection(list(refs.values()))
    query = ha_mean_query(measurements, condition, window, CURVE_STEP_S)
    try:
        series = await reader.query(query, database=database)
    except InfluxQueryError as exc:
        log.warning("PV-Tageskurve (HA) nicht lesbar", error=str(exc))
        for source in sources:
            curves.fail(source, UNREADABLE)
        return
    for source in sources:
        curves[source.id] = sorted(ha_points(series, refs[source.id]))
