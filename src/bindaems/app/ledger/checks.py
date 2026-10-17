"""Abgleich der Abrechnung mit den Zählerständen (Spec 11.4, Plan 4a, Präzisierung 9).

Je lokalem Tag und Signal steht die Summe der Abrechnung neben der Differenz der Zählerstände um
Mitternacht: ``import``, ``export``, ``pv`` und ``wb:<id>`` je Wallbox. Markiert wird eine
Abweichung von mehr als 1 % und mehr als 0,1 kWh, PV nie (DC-gekoppelte PV hat keinen Zähler). Fehlt
ein Zählerstand oder sinkt er (Zählertausch), bleibt der Tag ohne Markierung und trägt den Grund;
Summen über einen Zeitraum lassen solche Tage aus (``counter_kwh`` ist ``None``). Ein Tag mit
0 kWh am Zähler ist nie markiert, zählt aber in den Summen mit.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import Engine, delete, insert, select

from bindaems.app.db.schema import ledger_check
from bindaems.app.history.influx import InfluxReader
from bindaems.app.ledger.counters import readings_at
from bindaems.app.ledger.service import WALLBOX_PREFIX, LedgerService, LedgerSlot
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots

IMPORT: Final = "import"
EXPORT: Final = "export"
PV: Final = "pv"
IMPORT_COUNTER: Final = "grid.energy_import_kwh"
EXPORT_COUNTER: Final = "grid.energy_export_kwh"
DEVIATION_PCT: Final = 1.0  # darüber, und zugleich über MIN_DIFF_KWH, wird markiert
MIN_DIFF_KWH: Final = 0.1
NOTE_MISSING: Final = "Zählerstand fehlt"
NOTE_RESET: Final = "Zähler zurückgesetzt"
NOTE_ZERO: Final = "Zähler zeigt 0 kWh"


@dataclass(frozen=True)
class DayCheck:
    day: date
    signal: str  # ``import``, ``export``, ``pv`` oder ``wb:<id>``
    ledger_kwh: float
    counter_kwh: float | None  # ``None``: nicht bestimmbar, der Grund steht in ``note``
    deviation_pct: float | None  # ``None`` ohne Zählerwert oder bei 0 kWh am Zähler
    flagged: bool
    note: str | None


def _counter_kwh(
    counters: Sequence[str],
    start: Mapping[str, float | None],
    end: Mapping[str, float | None],
) -> tuple[float | None, str | None]:
    """Differenz der Zählerstände (bei mehreren die Summe) oder der Grund, warum es keine gibt."""
    if not counters:
        return None, NOTE_MISSING
    diffs: list[float] = []
    for counter in counters:
        first, last = start.get(counter), end.get(counter)
        if first is None or last is None:
            return None, NOTE_MISSING
        diffs.append(last - first)
    if any(diff < 0 for diff in diffs):
        return None, NOTE_RESET
    return math.fsum(diffs), None


def check_day(
    day: date,
    slots: Sequence[LedgerSlot],
    start_readings: Mapping[str, float | None],
    end_readings: Mapping[str, float | None],
    wallboxes: Sequence[str],
    pv_signals: Sequence[str],
) -> list[DayCheck]:
    """Prüft einen Tag; ``start_readings`` und ``end_readings`` sind die Zählerstände um die
    Mitternacht davor und danach (je Signal, ``None`` wo sie fehlen)."""
    pv_wh = math.fsum(
        wh for slot in slots for name, wh in slot.flows_wh.items() if name.startswith("pv>")
    )
    rows: list[tuple[str, float, Sequence[str], bool]] = [
        (IMPORT, math.fsum(slot.import_wh for slot in slots) / 1000, [IMPORT_COUNTER], True),
        (EXPORT, math.fsum(slot.export_wh for slot in slots) / 1000, [EXPORT_COUNTER], True),
        (PV, pv_wh / 1000, list(pv_signals), False),
    ]
    for key in wallboxes:
        target = f">{WALLBOX_PREFIX}{key}"
        wh = math.fsum(
            wh for slot in slots for name, wh in slot.flows_wh.items() if name.endswith(target)
        )
        rows.append((f"{WALLBOX_PREFIX}{key}", wh / 1000, [f"wallbox.{key}.total_kwh"], True))
    checks: list[DayCheck] = []
    for signal, ledger_kwh, counters, may_flag in rows:
        counter_kwh, note = _counter_kwh(counters, start_readings, end_readings)
        deviation: float | None = None
        if counter_kwh == 0:
            note = NOTE_ZERO
        elif counter_kwh is not None:
            deviation = (ledger_kwh - counter_kwh) / counter_kwh * 100
        flagged = (
            may_flag
            and counter_kwh is not None
            and deviation is not None
            and abs(deviation) > DEVIATION_PCT
            and abs(ledger_kwh - counter_kwh) > MIN_DIFF_KWH
        )
        checks.append(DayCheck(day, signal, ledger_kwh, counter_kwh, deviation, flagged, note))
    return checks


class LedgerChecks:
    def __init__(
        self,
        engine: Engine,
        ledger: LedgerService,
        reader: InfluxReader,
        clock: Clock,
        cfg: Config,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._reader = reader
        self._clock = clock
        self._wallboxes = list(cfg.wallboxes)
        self._pv_signals = [f"pv.{name}.energy_kwh" for name in cfg.victron.instances.pv.values()]
        self._counters = [
            IMPORT_COUNTER,
            EXPORT_COUNTER,
            *self._pv_signals,
            *(f"wallbox.{key}.total_kwh" for key in self._wallboxes),
        ]

    async def run(self, first: date, last: date) -> int:
        """Gleicht die Tage ``first … last`` ab, ersetzt ihre Zeilen und liefert die Zahl der Tage.

        Die Zählerstände aller Mitternächte von ``first`` bis ``last + 1`` kommen mit einer Abfrage;
        scheitert sie, bleiben die alten Zeilen stehen (die Ausnahme geht an den Aufrufer).
        """
        if first > last:
            return 0
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        midnights = [local_day_slots(day)[0] for day in [*days, last + timedelta(days=1)]]
        readings = await readings_at(self._reader, self._counters, midnights)
        return await asyncio.to_thread(self._store, days, midnights, readings)

    def _store(
        self,
        days: Sequence[date],
        midnights: Sequence[datetime],
        readings: Mapping[tuple[str, datetime], float | None],
    ) -> int:
        slots = self._ledger.slots(midnights[0], midnights[-1])
        by_day: dict[date, list[LedgerSlot]] = {}
        for slot in slots:
            by_day.setdefault(slot.slot_start.astimezone(LOCAL_TZ).date(), []).append(slot)
        now = self._clock.now()
        rows: list[dict[str, Any]] = []
        for day, before, after in zip(days, midnights[:-1], midnights[1:], strict=True):
            start = {signal: readings.get((signal, before)) for signal in self._counters}
            end = {signal: readings.get((signal, after)) for signal in self._counters}
            for check in check_day(
                day, by_day.get(day, []), start, end, self._wallboxes, self._pv_signals
            ):
                rows.append(
                    {
                        "day": day.isoformat(),
                        "signal": check.signal,
                        "ledger_kwh": check.ledger_kwh,
                        "counter_kwh": check.counter_kwh,
                        "deviation_pct": check.deviation_pct,
                        "flagged": check.flagged,
                        "note": check.note,
                        "checked_at": now,
                    }
                )
        with self._engine.begin() as conn:
            conn.execute(
                delete(ledger_check)
                .where(ledger_check.c.day >= days[0].isoformat())
                .where(ledger_check.c.day <= days[-1].isoformat())
            )
            conn.execute(insert(ledger_check), rows)
        return len(days)

    def checks(self, first: date, last: date) -> list[DayCheck]:
        """Die gespeicherten Prüfungen der Tage ``first … last``: nach Tag, dann ``import``,
        ``export``, ``pv`` und die Wallboxen in der Reihenfolge von ``config.yaml``."""
        query = (
            select(ledger_check)
            .where(ledger_check.c.day >= first.isoformat())
            .where(ledger_check.c.day <= last.isoformat())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        order = [IMPORT, EXPORT, PV, *(f"{WALLBOX_PREFIX}{key}" for key in self._wallboxes)]
        rank = {signal: n for n, signal in enumerate(order)}
        result = [
            DayCheck(
                date.fromisoformat(row.day),
                row.signal,
                row.ledger_kwh,
                row.counter_kwh,
                row.deviation_pct,
                bool(row.flagged),
                row.note,
            )
            for row in rows
        ]
        result.sort(key=lambda c: (c.day, rank.get(c.signal, len(rank)), c.signal))
        return result

    def totals(self, checks: Sequence[DayCheck]) -> list[dict[str, Any]]:
        """Summen je Signal über die Tage mit Zählerwert; die Abweichung folgt aus den Summen."""
        sums: dict[str, tuple[list[float], list[float]]] = {}
        for check in checks:
            ledger, counter = sums.setdefault(check.signal, ([], []))
            if check.counter_kwh is not None:
                ledger.append(check.ledger_kwh)
                counter.append(check.counter_kwh)
        result: list[dict[str, Any]] = []
        for signal, (ledger, counter) in sums.items():
            ledger_kwh = math.fsum(ledger)
            counter_kwh = math.fsum(counter) if counter else None
            deviation = (ledger_kwh - counter_kwh) / counter_kwh * 100 if counter_kwh else None
            result.append(
                {
                    "signal": signal,
                    "ledger_kwh": ledger_kwh,
                    "counter_kwh": counter_kwh,
                    "deviation_pct": deviation,
                }
            )
        return result
