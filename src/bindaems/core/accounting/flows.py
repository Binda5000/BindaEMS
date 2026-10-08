"""Zuordnung der Energieflüsse (Spec 11.4) und ihre Integration je Viertelstunde."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

from bindaems.shared.domain import SlotFlows, Snapshot
from bindaems.shared.timeutil import slot_start


def wb_key(source: str, wallbox: str) -> str:
    return f"{source}>wb:{wallbox}"


def allocate(
    pv_w: float,
    grid_w: float,
    battery_w: float,
    house_w: float,
    wallbox_w: Mapping[str, float],
) -> dict[str, float]:
    """Ordnet die Leistungen den Flüssen zu; nur Einträge > 0 werden geliefert.

    ``grid_w`` (+ = Bezug) folgt aus der Bilanz der übrigen Größen und dient nur der
    Vollständigkeit der Schnittstelle: Netzflüsse sind die Reste der Zuordnung.
    ``wallbox_w`` ist in Prioritätsreihenfolge geordnet.
    """
    del grid_w  # Netzflüsse ergeben sich als Rest (Schritte 4, 7 und 8)
    flows: dict[str, float] = defaultdict(float)
    pv = max(pv_w, 0.0)
    house = max(house_w, 0.0)
    wallboxes = {name: max(power, 0.0) for name, power in wallbox_w.items()}
    charge = max(battery_w, 0.0)
    discharge = max(-battery_w, 0.0)

    def take(available: float, demand: float, key: str) -> tuple[float, float]:
        amount = min(available, demand)
        flows[key] += amount
        return available - amount, demand - amount

    pv, house = take(pv, house, "pv>house")
    for name in wallboxes:
        pv, wallboxes[name] = take(pv, wallboxes[name], wb_key("pv", name))
    pv, charge = take(pv, charge, "pv>battery")
    flows["pv>grid"] += pv

    discharge, house = take(discharge, house, "battery>house")
    for name in wallboxes:
        discharge, wallboxes[name] = take(discharge, wallboxes[name], wb_key("battery", name))
    flows["battery>grid"] += discharge

    flows["grid>house"] += house
    for name, rest in wallboxes.items():
        flows[wb_key("grid", name)] += rest
    flows["grid>battery"] += charge
    return {key: value for key, value in flows.items() if value > 0}


def _counter(snap: Snapshot, signal: str) -> float | None:
    value = snap.ok(signal)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class SlotAccumulator:
    """Integriert Flussleistungen nach dem Halteprinzip zu Energien je Viertelstunde.

    Die Leistung eines Abtastpunkts gilt bis zum nächsten; das Intervall wird dem Slot des
    früheren Punkts zugerechnet, aber nur bis zu ``max_gap_s`` – längere Lücken bleiben offen
    und senken ``covered_s``.
    """

    def __init__(self, counter_signals: Sequence[str], max_gap_s: float = 5.0) -> None:
        self._signals = list(counter_signals)
        self._max_gap_s = max_gap_s
        self._slot: datetime | None = None
        self._prev: tuple[datetime, Mapping[str, float]] | None = None
        self._covered_s = 0.0
        self._flows_wh: dict[str, float] = defaultdict(float)
        self._start: dict[str, float | None] = {}
        self._last: dict[str, float | None] = {}

    def add(self, ts: datetime, flows_w: Mapping[str, float], snap: Snapshot) -> SlotFlows | None:
        if self._prev is not None:
            prev_ts, prev_flows = self._prev
            dt = (ts - prev_ts).total_seconds()
            if 0 < dt <= self._max_gap_s:
                for key, power in prev_flows.items():
                    self._flows_wh[key] += power * dt / 3600.0
                self._covered_s += dt
        self._prev = (ts, dict(flows_w))

        values = {signal: _counter(snap, signal) for signal in self._signals}
        closed = None
        current = slot_start(ts)
        if self._slot is not None and current != self._slot:
            closed = self._result(self._slot, end=values)
            self._reset()
        if self._slot is None:
            self._slot = current
        for signal, value in values.items():
            if value is not None:
                if self._start.get(signal) is None:
                    self._start[signal] = value
                self._last[signal] = value
        return closed

    def flush(self) -> SlotFlows | None:
        """Gibt den laufenden Slot einmal aus (z. B. beim Herunterfahren)."""
        if self._slot is None:
            return None
        result = self._result(self._slot, end=self._last)
        self._reset()
        self._prev = None
        return result

    def _result(self, slot: datetime, end: Mapping[str, float | None]) -> SlotFlows:
        return SlotFlows(
            slot_start=slot,
            covered_s=self._covered_s,
            flows_wh=dict(self._flows_wh),
            counters={s: (self._start.get(s), end.get(s)) for s in self._signals},
        )

    def _reset(self) -> None:
        self._slot = None
        self._covered_s = 0.0
        self._flows_wh = defaultdict(float)
        self._start = {}
        self._last = {}
