"""Nachrechnung von Lücken der Abrechnung aus den Zählerständen (Plan 4a, Präzisierung 10).

Fehlen Viertelstunden in der Abrechnung (die app war aus, der Strom riss ab, InfluxDB fehlte beim
Nachtragen), liest der Schätzer die Zählerstände an den Rändern der Lücke und verteilt die Energie
gleichmäßig auf ihre Slots: Netzbezug, Einspeisung, PV und Wallboxen aus den Zählern, der Akku aus
der Differenz des Ladezustands, das Haus als Rest der Bilanz. Die Slots tragen die Herkunft
``estimate`` und ``covered_s = 0``; ein echter Slot (``covered_s > 0``) ersetzt sie später, ein
vorhandener Slot wird nie überschrieben (``LedgerService.ingest`` ersetzt nur bei größerer
Abdeckung, und der Schätzer nimmt nur fehlende Slots in Angriff).

Netzbezug und Einspeisung müssen an beiden Rändern bekannt sein, sonst bleibt die Lücke leer.
Fehlende PV- oder Wallbox-Zähler zählen 0, ebenso ein fehlender Ladezustand (Akku ohne
Änderung). Lücken über 7 Tage bleiben leer: Eine so lange Pause ist kein Messausfall mehr, und die
gleichmäßige Verteilung wäre irreführend.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

import structlog

from bindaems.app.history.influx import InfluxReader
from bindaems.app.ledger.checks import (
    EXPORT,
    EXPORT_COUNTER,
    IMPORT,
    IMPORT_COUNTER,
    NOTE_MISSING,
    NOTE_RESET,
    PV,
)
from bindaems.app.ledger.counters import pv_signals, reading_near, soc_near, wallbox_signal
from bindaems.app.ledger.service import WALLBOX_PREFIX, LedgerService
from bindaems.shared.config import Config
from bindaems.shared.domain import SlotFlows
from bindaems.shared.timeutil import SLOT, Clock, ensure_utc, slot_start, slots_between

log = structlog.get_logger(__name__)

MAX_GAP = timedelta(days=7)  # längere Lücken bleiben leer
MAX_GAP_SLOTS = int(MAX_GAP / SLOT)
HOUSE_ZERO_KWH = 1e-9  # Rundungsrest der Hausbilanz, darunter gilt 0
FLOW_MIN_WH = 1e-6  # kleinere Flüsse je Slot sind Rundungsreste und entfallen


def allocate_wh(
    pv: float, battery_net: float, house: float, wallboxes: Mapping[str, float]
) -> dict[str, float]:
    """Ordnet Energien (oder Leistungen) den Flüssen zu; nur Einträge > 0 werden geliefert.

    Dieselbe Wasserfall-Regel wie ``core.accounting.flows.allocate`` (die app importiert den core
    nicht; ein Property-Test vergleicht beide): PV deckt Haus, dann die Wallboxen in der Reihenfolge
    von ``wallboxes``, dann die Akkuladung, der Rest geht ins Netz. Die Entladung deckt Haus und
    Wallboxen, der Rest geht ins Netz. Was bleibt, kommt aus dem Netz. ``battery_net`` ist positiv
    beim Laden. Die Netzflüsse sind die Reste der Zuordnung.
    """
    flows: dict[str, float] = {}
    available_pv = max(pv, 0.0)
    demand_house = max(house, 0.0)
    demands = {name: max(wh, 0.0) for name, wh in wallboxes.items()}
    charge = max(battery_net, 0.0)
    discharge = max(-battery_net, 0.0)

    def take(available: float, demand: float, key: str) -> tuple[float, float]:
        amount = min(available, demand)
        flows[key] = flows.get(key, 0.0) + amount
        return available - amount, demand - amount

    available_pv, demand_house = take(available_pv, demand_house, "pv>house")
    for name in demands:
        available_pv, demands[name] = take(
            available_pv, demands[name], f"pv>{WALLBOX_PREFIX}{name}"
        )
    available_pv, charge = take(available_pv, charge, "pv>battery")
    flows["pv>grid"] = flows.get("pv>grid", 0.0) + available_pv

    discharge, demand_house = take(discharge, demand_house, "battery>house")
    for name in demands:
        discharge, demands[name] = take(discharge, demands[name], f"battery>{WALLBOX_PREFIX}{name}")
    flows["battery>grid"] = flows.get("battery>grid", 0.0) + discharge

    flows["grid>house"] = flows.get("grid>house", 0.0) + demand_house
    for name, rest in demands.items():
        key = f"grid>{WALLBOX_PREFIX}{name}"
        flows[key] = flows.get(key, 0.0) + rest
    flows["grid>battery"] = flows.get("grid>battery", 0.0) + charge
    return {key: value for key, value in flows.items() if value > 0}


def gaps(present: Iterable[datetime], start: datetime, end: datetime) -> list[tuple[datetime, int]]:
    """Fehlende Slots in ``[start, end)`` als zusammenhängende Läufe ``(Beginn, Zahl der Slots)``.

    Nur vollständig vergangene Slots zählen: Ein Slot gehört dazu, wenn er vor ``end`` endet.
    ``end`` ist der Aufrufer-Zeitpunkt „jetzt“ oder früher; ``present`` sind die Anfänge der
    vorhandenen Slots.
    """
    have = {ensure_utc(slot) for slot in present}
    stop = ensure_utc(end)
    result: list[tuple[datetime, int]] = []
    run_start: datetime | None = None
    count = 0
    for slot in slots_between(start, stop):
        missing = slot + SLOT <= stop and slot not in have
        if missing and run_start is None:
            run_start, count = slot, 0
        if missing:
            count += 1
        elif run_start is not None:
            result.append((run_start, count))
            run_start = None
    if run_start is not None:
        result.append((run_start, count))
    return result


def estimate_gap(
    start: datetime,
    n: int,
    energies: Mapping[str, float],
    battery_net_kwh: float,
    wallbox_order: Sequence[str],
) -> list[SlotFlows]:
    """Verteilt die Energie einer Lücke gleichmäßig auf ihre ``n`` Slots ab ``start``.

    ``energies`` in kWh über die ganze Lücke mit den Schlüsseln ``import``, ``export``, ``pv`` und
    ``wb:<Schlüssel>`` (fehlend zählt 0); ``battery_net_kwh`` ist positiv beim Laden.
    Das Haus ist der Rest der Bilanz; wäre er negativ (DC-gekoppelte PV ohne Zähler, Messfehler),
    gilt der Fehlbetrag als zusätzliche PV und das Haus als 0. Die Slots tragen ``covered_s = 0``
    und keine Zählerstände.
    """
    if n < 1:
        return []
    wallbox_kwh = {
        key: max(energies.get(f"{WALLBOX_PREFIX}{key}", 0.0), 0.0) for key in wallbox_order
    }
    pv = energies.get(PV, 0.0)
    house = (
        energies.get(IMPORT, 0.0)
        - energies.get(EXPORT, 0.0)
        + pv
        - math.fsum(wallbox_kwh.values())
        - battery_net_kwh
    )
    if abs(house) < HOUSE_ZERO_KWH:
        house = 0.0  # Rundungsrest der Bilanz
    if house < 0:
        pv -= house
        house = 0.0
    per_slot = 1000.0 / n  # kWh der Lücke → Wh je Slot
    allocated = allocate_wh(
        pv * per_slot,
        battery_net_kwh * per_slot,
        house * per_slot,
        {key: kwh * per_slot for key, kwh in wallbox_kwh.items()},
    )
    flows = {key: wh for key, wh in allocated.items() if wh >= FLOW_MIN_WH}
    first = ensure_utc(start)
    return [SlotFlows(first + SLOT * i, 0.0, dict(flows), {}) for i in range(n)]


class GapEstimator:
    def __init__(
        self, ledger: LedgerService, reader: InfluxReader, clock: Clock, cfg: Config
    ) -> None:
        self._ledger = ledger
        self._reader = reader
        self._clock = clock
        self._usable_kwh = cfg.battery.usable_kwh
        self._wallboxes = cfg.wallbox_order
        self._pv_signals = pv_signals(cfg)
        self._wallbox_signals = {key: wallbox_signal(key) for key in self._wallboxes}

    async def run(self, start: datetime, end: datetime) -> int:
        """Rechnet die Lücken in ``[start, end)`` nach und liefert die Zahl der geschätzten Slots.

        Gerechnet wird nur bis zum Beginn der laufenden Viertelstunde. Eine Lücke bleibt leer, wenn
        sie länger als 7 Tage ist oder Netzbezug oder Einspeisung an einem Rand fehlen (der Grund
        steht im Log). Scheitert eine Abfrage an InfluxDB, geht die Ausnahme an den Aufrufer; bis
        dahin geschätzte Lücken bleiben gespeichert.
        """
        stop = min(ensure_utc(end), slot_start(self._clock.now()))
        first = ensure_utc(start)
        if stop <= first:
            return 0
        present = await asyncio.to_thread(self._present, first, stop)
        estimated = 0
        for begin, n in gaps(present, first, stop):
            if n > MAX_GAP_SLOTS:
                log.warning("Lücke zu lang für eine Nachrechnung", start=begin.isoformat(), slots=n)
                continue
            slots = await self._estimate(begin, n)
            if slots:
                estimated += await asyncio.to_thread(self._store, slots)
        if estimated:
            log.info("Lücken der Abrechnung nachgerechnet", slots=estimated)
        return estimated

    def _present(self, start: datetime, end: datetime) -> list[datetime]:
        return [slot.slot_start for slot in self._ledger.slots(start, end)]

    def _store(self, slots: Sequence[SlotFlows]) -> int:
        return sum(1 for slot in slots if self._ledger.ingest(slot, "estimate"))

    async def _estimate(self, begin: datetime, n: int) -> list[SlotFlows]:
        end = begin + SLOT * n
        signals = [
            IMPORT_COUNTER,
            EXPORT_COUNTER,
            *self._pv_signals,
            *self._wallbox_signals.values(),
        ]
        *readings, soc_from, soc_to = await asyncio.gather(
            *(reading_near(self._reader, s, begin, before=True) for s in signals),
            *(reading_near(self._reader, s, end, before=False) for s in signals),
            soc_near(self._reader, begin, before=True),
            soc_near(self._reader, end, before=False),
        )
        before = dict(zip(signals, readings[: len(signals)], strict=True))
        after = dict(zip(signals, readings[len(signals) :], strict=True))

        def diff(signal: str) -> float | None:
            low, high = before[signal], after[signal]
            return None if low is None or high is None else high - low

        energies: dict[str, float] = {}
        for key, signal in ((IMPORT, IMPORT_COUNTER), (EXPORT, EXPORT_COUNTER)):
            value = diff(signal)
            if value is None or value < 0:
                log.info(
                    "Lücke bleibt leer",
                    start=begin.isoformat(),
                    slots=n,
                    reason=f"{key}: {NOTE_MISSING if value is None else NOTE_RESET}",
                )
                return []
            energies[key] = value
        # fehlende oder zurückgesetzte Zähler von PV und Wallboxen zählen 0
        energies[PV] = math.fsum(max(diff(s) or 0.0, 0.0) for s in self._pv_signals)
        for key, signal in self._wallbox_signals.items():
            energies[f"{WALLBOX_PREFIX}{key}"] = max(diff(signal) or 0.0, 0.0)
        battery_net = 0.0
        if soc_from is not None and soc_to is not None:
            battery_net = (soc_to - soc_from) / 100 * self._usable_kwh
        return estimate_gap(begin, n, energies, battery_net, self._wallboxes)
