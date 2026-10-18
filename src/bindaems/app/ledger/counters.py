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

WINDOW = timedelta(minutes=30)  # so weit sucht ``readings_at`` vor und nach dem Zeitpunkt
ALONE = timedelta(minutes=2)  # ein Wert ohne Gegenstück gilt nur so nah am Zeitpunkt
NEAR = timedelta(minutes=15)  # Abstand zu den Rändern einer Lücke
SOC_ID = "battery"

_ID = r"(?P<id>[^.]+)"
_GRID = re.compile(r"grid\.energy_(?P<direction>import|export)_kwh")
_PV = re.compile(rf"pv\.{_ID}\.energy_kwh")
_WALLBOX = re.compile(rf"wallbox\.{_ID}\.total_kwh")
_COLUMN = re.compile(r"[ab]\d+")  # Spalten von ``readings_at``: davor bzw. danach, Zeitpunkt n


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


def _instant_reading(
    instant_ms: int, before: tuple[int, float] | None, after: tuple[int, float] | None
) -> float | None:
    """Zählerstand zum Zeitpunkt aus dem letzten Wert davor und dem ersten ab dann (je ``(ms,
    kWh)``): linear dazwischen; ist der Zähler dazwischen zurückgesetzt, gilt der Wert davor. Fehlt
    einer, gilt der andere, wenn er höchstens ``ALONE`` entfernt ist."""
    if before is not None and after is not None:
        (time_before, value_before), (time_after, value_after) = before, after
        if value_after < value_before or time_after <= time_before:
            return value_before
        share = (instant_ms - time_before) / (time_after - time_before)
        return value_before + (value_after - value_before) * share
    alone = before or after
    if alone is not None and abs(alone[0] - instant_ms) <= ALONE.total_seconds() * 1000:
        return alone[1]
    return None


async def readings_at(
    reader: InfluxReader, signals: Sequence[str], instants: Sequence[datetime]
) -> dict[tuple[str, datetime], float | None]:
    """Zählerstand je Signal und Zeitpunkt, linear interpoliert aus dem letzten Wert in
    ``[Zeitpunkt − 30 min, Zeitpunkt)`` und dem ersten in ``[Zeitpunkt, Zeitpunkt + 30 min)``
    (der core schreibt die Zähler nur jede Minute; Regeln in ``_instant_reading``).

    Eine Anfrage für alle Signale und Zeitpunkte: je Zeitpunkt zwei Anweisungen, deren Spalte
    (``b<n>`` davor, ``a<n>`` danach) den Zeitpunkt ``n`` nennt. Ohne ``GROUP BY time`` liefern
    ``last`` und ``first`` die Zeit des Werts selbst. Fehlt ein Wert, steht ``None`` da.
    """
    result: dict[tuple[str, datetime], float | None] = {
        (signal, instant): None for signal in signals for instant in instants
    }
    tags = {signal: counter_tags(signal) for signal in signals}
    known = {signal: t for signal, t in tags.items() if t is not None}
    if not known or not instants:
        return result
    # nur nach ``source`` gefiltert, damit die Anfrage kurz bleibt; die Reihen wählt der Abgleich
    sources = " OR ".join(
        f'"source" = {quote_str(s)}' for s in sorted({t[0] for t in known.values()})
    )
    statements: list[str] = []
    for n, instant in enumerate(instants):
        for column, select, low, high in (
            (f"b{n}", "last", instant - WINDOW, instant),
            (f"a{n}", "first", instant, instant + WINDOW),
        ):
            # feste Bezeichner, Tags gequotet, Zeiten als Zahlen
            statements.append(
                f'SELECT {select}("kwh") AS "{column}" '  # noqa: S608
                f"FROM {quote_ident(RP_LONG)}.{quote_ident('energy')} "
                f"WHERE time >= {epoch_ms(low)}ms AND time < {epoch_ms(high)}ms AND ({sources}) "
                'GROUP BY "source","id","direction"'
            )
    found: dict[tuple[tuple[str, str, str], str], tuple[int, float]] = {}
    for series in await reader.query(";".join(statements)):
        key = (
            series.tags.get("source", ""),
            series.tags.get("id", ""),
            series.tags.get("direction", ""),
        )
        indexes = _value_column(series.columns)
        if indexes is None or not _COLUMN.fullmatch(series.columns[indexes[1]]):
            continue
        time_index, value_index = indexes
        for row in series.values:
            stamp = _number(row[time_index]) if time_index < len(row) else None
            value = _number(row[value_index]) if value_index < len(row) else None
            if stamp is not None and value is not None:
                found[(key, series.columns[value_index])] = (int(stamp), value)
    for signal, key in known.items():
        for n, instant in enumerate(instants):
            result[(signal, instant)] = _instant_reading(
                epoch_ms(instant), found.get((key, f"b{n}")), found.get((key, f"a{n}"))
            )
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
