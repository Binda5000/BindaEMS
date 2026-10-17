"""Regelbasierter Ersatzplan für den Akku (Plan 3b, Präzisierung 10).

Gilt, wenn der Optimierer scheitert oder sein Plan unplausibel ist. Grundlage ist
``EIGENVERBRAUCH`` in allen Slots. Reicht der SOC für einen teuren Slot des heutigen Tages nur
knapp, hält der Plan den Akku in den billigsten Slots davor zurück und lädt, wenn der Schalter es
erlaubt, die Spread-Regel erfüllt ist und Regel R1 hält, in billigen Slots aus dem Netz.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import floor
from statistics import quantiles
from typing import Final

from bindaems.app.planning.battery_model import SimStep, params_of, sim_slots, simulate
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import grid_charge_cap_w
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_after_kwh, spread_pays
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

HIGH_PERCENTILE_INDEX: Final = 7  # 80. Perzentil aus ``quantiles(n=10)``
SHORT_W: Final = 100.0  # ab dieser Netzleistung im teuren Slot reicht der SOC nur knapp
ROUND_W: Final = 100.0  # Schritt der Netzladeleistung


def _local_day(inputs: PlanInputs, index: int) -> object:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).date()


def expensive_today(inputs: PlanInputs) -> list[int]:
    """Indizes der teuren Slots: Slots des heutigen lokalen Tages ab dem aktuellen Slot, deren
    Bezugspreis das 80. Perzentil der Preise dieser Slots erreicht.

    Ein einheitlicher Preis ergibt keinen teuren Slot (es gäbe nichts zu verschieben), und ein
    Preis genau am Perzentil zählt mit, sonst bliebe bei vielen gleichen Spitzenpreisen keiner.
    """
    if not inputs.slots:
        return []
    today = _local_day(inputs, 0)
    indices = [i for i in range(len(inputs.slots)) if _local_day(inputs, i) == today]
    prices = [inputs.slots[i].import_ct for i in indices]
    if len(prices) < 2 or min(prices) == max(prices):
        return []
    threshold = quantiles(prices, n=10, method="inclusive")[HIGH_PERCENTILE_INDEX]
    return [i for i in indices if inputs.slots[i].import_ct >= threshold]


def _first_short(steps: Sequence[SimStep], expensive: Sequence[int]) -> int | None:
    """Erster teurer Slot, in dem die Nachrechnung mehr als ``SHORT_W`` aus dem Netz bezieht,
    weil der Akku den Fehlbetrag nicht deckt; Netzladen zählt nicht."""
    for index in expensive:
        if steps[index].import_w - steps[index].charge_grid_w > SHORT_W:
            return index
    return None


def fallback_intents(inputs: PlanInputs) -> list[BatteryIntent]:
    """Absicht je Slot des Ersatzplans (nur Akku)."""
    count = len(inputs.slots)
    intents = [BatteryIntent() for _ in range(count)]
    expensive = expensive_today(inputs)
    if not expensive:
        return intents

    params = params_of(inputs)
    slots = sim_slots(inputs)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    planning = inputs.planning
    prices = [slot.import_ct for slot in inputs.slots]
    is_expensive = set(expensive)

    def run(plan: Sequence[BatteryIntent]) -> list[SimStep]:
        return simulate(soc0_kwh, slots, plan, params)

    steps = run(intents)

    # Halten: die billigsten entladenden Slots vor dem ersten knappen teuren Slot.
    for _ in range(count):
        short = _first_short(steps, expensive)
        if short is None:
            break
        candidates = [
            i
            for i in range(short)
            if i not in is_expensive
            and intents[i].kind is BatteryIntentKind.EIGENVERBRAUCH
            and steps[i].discharge_w > 0
        ]
        if not candidates:
            break
        chosen = min(candidates, key=lambda i: (prices[i], i))
        intents[chosen] = BatteryIntent(kind=BatteryIntentKind.HALTEN)
        steps = run(intents)

    if not planning.grid_charge_allowed:
        return intents

    # Netzladen: noch immer knappe teure Slots, billige Slots davor laden aus dem Netz.
    for _ in range(count):
        short = _first_short(steps, expensive)
        if short is None:
            break
        for candidate in sorted(
            (
                i
                for i in range(short)
                if i not in is_expensive and intents[i].kind is not BatteryIntentKind.NETZLADEN
            ),
            key=lambda i: (prices[i], i),
        ):
            if not spread_pays(planning, prices[candidate], prices[short]):
                continue
            power_w = ROUND_W * floor(grid_charge_cap_w(inputs, candidate) / ROUND_W)
            if power_w <= 0:
                continue
            trial = list(intents)
            trial[candidate] = BatteryIntent(
                kind=BatteryIntentKind.NETZLADEN, grid_charge_w=power_w
            )
            trial_steps = run(trial)
            if trial_steps[candidate].charge_grid_w < SHORT_W:
                continue  # kein Platz im Akku: das Netzladen brächte nichts
            if export_after_kwh(trial_steps, candidate) > R1_TOL_KWH:
                continue
            intents, steps = trial, trial_steps
            break
        else:
            break
    return intents
