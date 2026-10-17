"""Zählerstände aus InfluxDB für den Abgleich und die Nachrechnung (Plan 4a, Präzisierung 8).

Der core schreibt die Zählerstände als Messung ``energy`` (Feld ``kwh``) mit den Tags ``source``,
``id`` und ``direction``. Die Signale der Abrechnung (``slot.counters``) bilden sich so ab:

=============================  ==========  ======  ===========
Signal                         ``source``  ``id``  ``direction``
=============================  ==========  ======  ===========
``grid.energy_import_kwh``     grid        grid    import
``grid.energy_export_kwh``     grid        grid    export
``pv.<X>.energy_kwh``          pv          X       forward
``wallbox.<X>.total_kwh``      wallbox     X       total
=============================  ==========  ======  ===========
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from bindaems.app.history.influx import (
    RP_LONG,
    RP_RAW,
    InfluxReader,
    Series,
    quote_ident,
    quote_str,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import ensure_utc

BUCKET = timedelta(minutes=30)  # ``GROUP BY time(30m)`` der Mitternachtsstände
NEAR = timedelta(minutes=15)  # Abstand zu den Rändern einer Lücke
SOC_ID = "battery"

_ID = r"(?P<id>[^.]+)"
_GRID = re.compile(r"grid\.energy_(?P<direction>import|export)_kwh")
_PV = re.compile(rf"pv\.{_ID}\.energy_kwh")
_WALLBOX = re.compile(rf"wallbox\.{_ID}\.total_kwh")


def counter_tags(signal: str) -> tuple[str, str, str] | None:
    """``(source, id, direction)`` des Zählers in InfluxDB; ``None`` für ein unbekanntes Signal."""
    if match := _GRID.fullmatch(signal):
        return ("grid", "grid", match["direction"])
    if match := _PV.fullmatch(signal):
        return ("pv", match["id"], "forward")
    if match := _WALLBOX.fullmatch(signal):
        return ("wallbox", match["id"], "total")
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _tag_filter(tags: tuple[str, str, str]) -> str:
    source, ident, direction = tags
    return (
        f'("source" = {quote_str(source)} AND "id" = {quote_str(ident)} '
        f'AND "direction" = {quote_str(direction)})'
    )


def _value_column(columns: Sequence[str]) -> tuple[int, int] | None:
    """``(Index von time, Index des Werts)`` einer Reihe mit ``time`` und einer Wertspalte."""
    if "time" not in columns or len(columns) != 2:
        return None
    time_index = columns.index("time")
    return time_index, 1 - time_index


def _first_value(series: Sequence[Series]) -> float | None:
    """Der Wert der ersten Zeile der ersten Reihe mit Zeilen."""
    for item in series:
        indexes = _value_column(item.columns)
        if indexes is None or not item.values:
            continue
        row = item.values[0]
        return _number(row[indexes[1]]) if indexes[1] < len(row) else None
    return None


async def readings_at(
    reader: InfluxReader, signals: Sequence[str], instants: Sequence[datetime]
) -> dict[tuple[str, datetime], float | None]:
    """Zählerstand je Signal und Zeitpunkt: der letzte Wert in ``[Zeitpunkt − 30 min, Zeitpunkt)``.

    Eine einzige Abfrage über den ganzen Zeitraum (``GROUP BY time(30m)``); die Zeitpunkte müssen
    auf einer halben Stunde liegen (Mitternacht in Wien tut das, auch beim Wechsel der Sommerzeit,
    denn der Unterschied zu UTC beträgt ganze Stunden). Fehlt ein Wert, steht ``None`` da.
    """
    result: dict[tuple[str, datetime], float | None] = {
        (signal, instant): None for signal in signals for instant in instants
    }
    tags = {signal: counter_tags(signal) for signal in signals}
    known = {signal: t for signal, t in tags.items() if t is not None}
    if not known or not instants:
        return result
    for instant in instants:
        if epoch_ms(instant) % int(BUCKET.total_seconds() * 1000):
            raise ValueError(f"Zeitpunkt nicht auf einer halben Stunde: {instant.isoformat()}")
    start = min(instants) - BUCKET
    end = max(instants)
    # feste Bezeichner, Tags und Zeiten gequotet bzw. als Zahlen
    query = (
        f'SELECT last("kwh") AS "kwh" FROM {quote_ident(RP_LONG)}.{quote_ident("energy")} '  # noqa: S608
        f"WHERE time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms AND ("
        + " OR ".join(_tag_filter(t) for t in sorted(set(known.values())))
        + ') GROUP BY time(30m),"source","id","direction" fill(none)'
    )
    buckets: dict[tuple[str, str, str], dict[int, float]] = {}
    for series in await reader.query(query):
        key = (
            series.tags.get("source", ""),
            series.tags.get("id", ""),
            series.tags.get("direction", ""),
        )
        indexes = _value_column(series.columns)
        if indexes is None:
            continue
        time_index, value_index = indexes
        for row in series.values:
            stamp = _number(row[time_index]) if time_index < len(row) else None
            value = _number(row[value_index]) if value_index < len(row) else None
            if stamp is not None and value is not None:
                buckets.setdefault(key, {})[int(stamp)] = value
    for signal, key in known.items():
        for instant in instants:
            result[(signal, instant)] = buckets.get(key, {}).get(epoch_ms(instant - BUCKET))
    return result


async def _near(
    reader: InfluxReader,
    rp: str,
    measurement: str,
    field: str,
    filters: str,
    instant: datetime,
    *,
    before: bool,
) -> float | None:
    instant = ensure_utc(instant)
    if before:
        select, window = "last", (instant - NEAR, instant)
    else:
        select, window = "first", (instant, instant + NEAR)
    # feste Bezeichner, Tags gequotet, Zeiten als Zahlen
    query = (
        f"SELECT {select}({quote_ident(field)}) FROM "  # noqa: S608
        f"{quote_ident(rp)}.{quote_ident(measurement)} "
        f"WHERE {filters} AND time >= {epoch_ms(window[0])}ms AND time <= {epoch_ms(window[1])}ms"
    )
    return _first_value(await reader.query(query))


async def reading_near(
    reader: InfluxReader, signal: str, instant: datetime, *, before: bool
) -> float | None:
    """Zählerstand am Rand einer Lücke aus den Rohdaten: bei ``before`` der letzte Wert in
    ``[Zeitpunkt − 15 min, Zeitpunkt]``, sonst der erste in ``[Zeitpunkt, Zeitpunkt + 15 min]``."""
    tags = counter_tags(signal)
    if tags is None:
        return None
    return await _near(reader, RP_RAW, "energy", "kwh", _tag_filter(tags), instant, before=before)


async def soc_near(reader: InfluxReader, instant: datetime, *, before: bool) -> float | None:
    """Ladezustand des Akkus (%) am Rand einer Lücke, wie ``reading_near``."""
    return await _near(
        reader, RP_RAW, "soc", "pct", f'"id" = {quote_str(SOC_ID)}', instant, before=before
    )
