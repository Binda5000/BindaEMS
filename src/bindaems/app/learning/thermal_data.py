"""Daten der Klimaanlage je Viertelstunde lesen (Plan 5c, Präzisierung 4).

Die Zahlen (Ist- und Solltemperatur, Versatz, Leistung der Anlage, PV) kommen als Mittel je
Viertelstunde aus der BindaEMS-Datenbank, die Außentemperatur aus dem Wetterarchiv (5a). Die Bits
der Betriebsart stehen in ``status`` und nur bei Änderung dort; sie werden als Stufenfunktion
gelesen (letzter Wert vor dem Fenster plus alle Änderungen darin), und daraus entsteht der
Zeitanteil „wahr“ je Viertelstunde. Ein Slot ohne Isttemperatur oder ohne Außentemperatur fehlt;
ein Ersatzwert entsteht nie, und was nicht bekannt ist, bleibt ``None``.
"""

from __future__ import annotations

import asyncio
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from sqlalchemy.exc import SQLAlchemyError

from bindaems.app.history.influx import (
    RP_RAW,
    InfluxQueryError,
    InfluxReader,
    Series,
    quote_ident,
    quote_str,
)
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import SLOT, ensure_utc, slots_between

STEP: Final = "15m"
# PV am Gleichstrom (MPPT, ``pv_dc.power_w`` im Sampler): fehlt sie, zählt sie wie im core 0 W
PV_DC_ID: Final = "dc"
# Ab diesem Anteil gilt ein Bit als gesetzt, bis zu ``1 - ON_SHARE`` als nicht gesetzt
ON_SHARE: Final = 0.8
OFF_SHARE: Final = 1 - ON_SHARE
EPSILON: Final = 1e-9

SlotMode = Literal["heizen", "kuehlen", "lueften", "aus"]

# Reihenfolge der Vorgabe im core (``thermal_mode``): Sind mehrere Bits gesetzt, gilt das erste
_MODE_BITS: Final[tuple[tuple[str, SlotMode], ...]] = (
    ("heating", "heizen"),
    ("venting", "lueften"),
    ("cooling", "kuehlen"),
)
_BITS: Final = ("running", *(bit for bit, _ in _MODE_BITS))


class ThermalDataError(Exception):
    """Die Daten der Klimaanlage ließen sich nicht lesen; der Aufrufer behält seinen Stand."""


@dataclass(frozen=True)
class ThermalSlot:
    """Eine Viertelstunde der Klimaanlage (``start`` in UTC)."""

    start: datetime
    actual_c: float
    setpoint_c: float | None
    offset_k: float | None
    power_w: float | None
    running: bool | None
    mode: SlotMode | None
    outdoor_c: float
    pv_w: float | None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _rows(series: Sequence[Series]) -> list[tuple[datetime, dict[str, Any]]]:
    rows: list[tuple[datetime, dict[str, Any]]] = []
    for item in series:
        for row in item.values:
            if not row or isinstance(row[0], bool) or not isinstance(row[0], int):
                continue
            ts = datetime.fromtimestamp(row[0] / 1000, UTC)
            rows.append((ts, dict(zip(item.columns, row, strict=False))))
    rows.sort(key=lambda entry: entry[0])
    return rows


async def _query(reader: InfluxReader, text: str) -> list[Series]:
    try:
        return await reader.query(text)
    except InfluxQueryError as exc:
        raise ThermalDataError(f"Die Daten der Klimaanlage sind nicht lesbar: {exc}") from exc


def _window(start: datetime, end: datetime) -> str:
    return f"time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms"


async def _means(
    reader: InfluxReader, fields: Sequence[str], measurement: str, condition: str, window: str
) -> dict[datetime, dict[str, float | None]]:
    """Mittel der Felder je Viertelstunde (Beginn UTC); Felder ohne Wert sind ``None``."""
    # Bezeichner und Werte sind maskiert
    select = ", ".join(f"mean({quote_ident(field)}) AS {quote_ident(field)}" for field in fields)
    text = (
        f"SELECT {select} FROM {quote_ident(RP_RAW)}.{quote_ident(measurement)} "  # noqa: S608
        f"WHERE {condition} AND {window} GROUP BY time({STEP}) fill(none)"
    )
    return {
        ts: {field: _number(cells.get(field)) for field in fields}
        for ts, cells in _rows(await _query(reader, text))
    }


async def _pv(reader: InfluxReader, window: str) -> dict[datetime, float]:
    """PV gesamt je Viertelstunde: die Summe aller Reihen mit ``source=pv`` und ``phase=total``
    (Wechselrichter und ``dc``). Unbekannt bleibt unbekannt: Hat in einer Viertelstunde nicht jeder
    Wechselrichter, der in der Antwort vorkommt, einen Wert, gibt es dafür keinen PV-Wert (eine
    Teilsumme wäre zu klein und ginge als Wert in die Anpassung). ``dc`` fehlt wie im core
    (``Derived.pv_total_w``) als 0 W."""
    text = (
        f'SELECT mean("p_w") AS "p_w" FROM {quote_ident(RP_RAW)}."power" '  # noqa: S608
        f"WHERE \"source\"='pv' AND \"phase\"='total' AND {window} "
        f'GROUP BY time({STEP}),"id" fill(none)'
    )
    ids: set[str] = set()
    parts: dict[datetime, dict[str, float]] = {}
    for item in await _query(reader, text):
        series_id = item.tags.get("id", "")
        ids.add(series_id)
        for ts, cells in _rows([item]):
            value = _number(cells.get("p_w"))
            if value is not None:
                parts.setdefault(ts, {})[series_id] = value
    required = ids - {PV_DC_ID}
    return {ts: sum(values.values()) for ts, values in parts.items() if required <= values.keys()}


@dataclass(frozen=True)
class _Steps:
    """Stufenfunktion eines Bits: ``seed`` vor dem Fenster und die Änderungen darin."""

    seed: bool | None
    times: list[datetime]
    values: list[bool]

    def share(self, start: datetime, end: datetime) -> float | None:
        """Zeitanteil „wahr“ in ``[start, end)``; ``None``, wenn der Anfang unbekannt ist."""
        index = bisect_right(self.times, start) - 1
        state = self.values[index] if index >= 0 else self.seed
        if state is None:
            return None
        true_s = 0.0
        cursor = start
        for ts, value in zip(self.times[index + 1 :], self.values[index + 1 :], strict=True):
            if ts >= end:
                break
            if state:
                true_s += (ts - cursor).total_seconds()
            state, cursor = value, ts
        if state:
            true_s += (end - cursor).total_seconds()
        return true_s / (end - start).total_seconds()


def _bit_of(item_id: str, device: str) -> str | None:
    prefix = f"thermal.{device}."
    bit = item_id.removeprefix(prefix) if item_id.startswith(prefix) else None
    return bit if bit in _BITS else None


async def _steps(
    reader: InfluxReader, device: str, start: datetime, end: datetime
) -> dict[str, _Steps]:
    ids = " OR ".join(f'"id" = {quote_str(f"thermal.{device}.{bit}")}' for bit in _BITS)
    # Werte sind maskiert, Feld und Messung sind Konstanten
    changes = await _query(
        reader,
        f'SELECT "value_bool" FROM "status" WHERE ({ids}) AND {_window(start, end)} '  # noqa: S608
        'GROUP BY "id"',
    )
    earlier = await _query(
        reader,
        f'SELECT last("value_bool") FROM "status" WHERE ({ids}) '  # noqa: S608
        f'AND time < {epoch_ms(start)}ms GROUP BY "id"',
    )
    seeds: dict[str, bool | None] = dict.fromkeys(_BITS)
    for item in earlier:
        bit = _bit_of(item.tags.get("id", ""), device)
        for _, cells in _rows([item]):
            value = cells.get("last")
            if bit is not None and isinstance(value, bool):
                seeds[bit] = value
    events: dict[str, list[tuple[datetime, bool]]] = {bit: [] for bit in _BITS}
    for item in changes:
        bit = _bit_of(item.tags.get("id", ""), device)
        if bit is None:
            continue
        for ts, cells in _rows([item]):
            value = cells.get("value_bool")
            if isinstance(value, bool):
                events[bit].append((ts, value))
    result: dict[str, _Steps] = {}
    for bit in _BITS:
        ordered = sorted(events[bit], key=lambda event: event[0])
        result[bit] = _Steps(seeds[bit], [t for t, _ in ordered], [v for _, v in ordered])
    return result


def _flag(share: float | None) -> bool | None:
    """Wahr ab 80 %, falsch bei höchstens 20 %, dazwischen und ohne Anteil unbekannt."""
    if share is None:
        return None
    if share >= ON_SHARE - EPSILON:
        return True
    if share <= OFF_SHARE + EPSILON:
        return False
    return None


def _mode(running: bool | None, shares: dict[str, float | None]) -> SlotMode | None:
    """``aus`` bei falschem ``running``; sonst das erste gesetzte Bit nach der Reihenfolge des
    core. Ist ein Bit davor unbekannt oder nur zum Teil gesetzt, könnte es vorgehen: ``None``."""
    if running is None:
        return None
    if not running:
        return "aus"
    for bit, mode in _MODE_BITS:
        flag = _flag(shares[bit])
        if flag is None:
            return None
        if flag:
            return mode
    return None


async def read_thermal_slots(
    reader: InfluxReader,
    weather: WeatherArchive,
    device: str,
    start: datetime,
    end: datetime,
) -> list[ThermalSlot]:
    """Die Viertelstunden der Klimaanlage ``device`` in ``[start, end)``, nach Beginn (UTC)
    sortiert. Nur ganze Viertelstunden im Fenster zählen; solche ohne Isttemperatur oder ohne
    Außentemperatur fehlen. Ein Fehler beim Lesen wirft ``ThermalDataError``."""
    grid = [
        slot
        for slot in slots_between(ensure_utc(start), ensure_utc(end))
        if slot + SLOT <= ensure_utc(end)
    ]
    if not grid:
        return []
    first, stop = grid[0], grid[-1] + SLOT
    window = _window(first, stop)
    device_id = quote_str(device)
    own = await _means(
        reader, ("actual_c", "setpoint_c", "offset_k"), "thermal", f'"id" = {device_id}', window
    )
    power = await _means(
        reader,
        ("p_w",),
        "power",
        f'"source"=\'thermal\' AND "id" = {device_id} AND "phase"=\'total\'',
        window,
    )
    pv = await _pv(reader, window)
    steps = await _steps(reader, device, first, stop)
    try:
        outdoor = await asyncio.to_thread(weather.temperatures, first, stop)
    except SQLAlchemyError as exc:
        raise ThermalDataError(f"Das Wetterarchiv ist nicht lesbar: {exc}") from exc

    slots: list[ThermalSlot] = []
    for slot in grid:
        cells = own.get(slot, {})
        actual_c, outdoor_c = cells.get("actual_c"), outdoor.get(slot)
        if actual_c is None or outdoor_c is None or not math.isfinite(outdoor_c):
            continue
        shares = {bit: steps[bit].share(slot, slot + SLOT) for bit in _BITS}
        running = _flag(shares["running"])
        slots.append(
            ThermalSlot(
                start=slot,
                actual_c=actual_c,
                setpoint_c=cells.get("setpoint_c"),
                offset_k=cells.get("offset_k"),
                power_w=power.get(slot, {}).get("p_w"),
                running=running,
                mode=_mode(running, shares),
                outdoor_c=float(outdoor_c),
                pv_w=pv.get(slot),
            )
        )
    return slots
