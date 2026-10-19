"""Regelbasierter Ersatzplan für Akku und geplante Autos (Plan 3b, Präzisierung 10; Plan 4b,
Entscheidung 8).

Gilt, wenn der Optimierer scheitert oder sein Plan unplausibel ist. Grundlage ist
``EIGENVERBRAUCH`` in allen Slots. Reicht der SOC für einen teuren Slot des heutigen Tages nur
knapp, hält der Plan den Akku in den billigsten Slots davor zurück und lädt, wenn der Schalter es
erlaubt, die Spread-Regel erfüllt ist und Regel R1 hält, in billigen Slots aus dem Netz.

Die geplanten Autos laden zuerst den erwarteten PV-Überschuss bis zur Abfahrt, dann in den
günstigsten Slots davor mit voller Leistung (``fallback_vehicles``); der Plan des Akkus rechnet
sie mit, und sein Netzladen lässt neben Haus und Autos den höchsten Netzbezug
(``grid_max_import_w``) frei.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import floor
from statistics import quantiles
from typing import Final

from bindaems.app.planning.battery_model import (
    EV_ON_W,
    SLOT_H,
    SimStep,
    params_of,
    sim_slots,
    simulate,
)
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import grid_charge_cap_w
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_after_kwh, spread_pays
from bindaems.app.planning.vehicles import _slots_before
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

HIGH_PERCENTILE_INDEX: Final = 7  # 80. Perzentil aus ``quantiles(n=10)``
SHORT_W: Final = 100.0  # ab dieser Netzleistung im teuren Slot reicht der SOC nur knapp
ROUND_W: Final = 100.0  # Schritt der Netzladeleistung
_KWH_EPS: Final = 1e-9  # Gleitkommafehler beim Vergleich von Energien der Autos


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
    weil der Akku den Fehlbetrag nicht deckt; Netzladen und die Autos zählen nicht."""
    for index in expensive:
        item = steps[index]
        if item.import_w - item.charge_grid_w - item.ev_grid_w > SHORT_W:
            return index
    return None


def fallback_vehicles(inputs: PlanInputs) -> dict[str, list[float]]:
    """Ladeleistung je geplantem Auto und Slot im Ersatzplan in W (Entscheidung 8).

    Je Auto in der Reihenfolge der Priorität (``inputs.vehicles``), nur in den Slots vor der
    Abfahrt:

    1. **PV:** der erwartete Überschuss (P50, ``pv_w − load_w``), den die Autos davor übrig
       lassen, Slot für Slot bis der ganze Bedarf (``need_kwh``) gedeckt ist. Ein Slot zählt
       nur, wenn der Überschuss mindestens ``min_w`` erreicht.
    2. **Netz:** dann die günstigsten Slots (beim Gleichstand der frühere) mit voller Leistung,
       bis der Bedarf im Horizont (``horizon_need_kwh``) gedeckt ist; der letzte lädt nur den
       Rest, mindestens ``min_w``. Je Slot laden alle Autos zusammen höchstens
       ``grid_max_import_w`` plus PV minus Haus.

    Regel R2 hält das meist von selbst ein, denn der Überschuss nach P50 ist mindestens der nach
    P25. Nicht immer: Einen Überschuss unter ``min_w`` rechnet R2 (``grid_allowed_kwh``) dem Auto
    als PV an, der Ersatzplan lädt dort aber nicht; bei vielen solchen Slots lädt er mehr aus dem
    Netz als ``grid_allowed_kwh`` erlaubt. Was nicht in den Horizont passt, bleibt Fehlmenge.
    """
    count = len(inputs.slots)
    pv_left = [max(0.0, slot.pv_w - slot.load_w) for slot in inputs.slots]
    room = [inputs.battery.grid_max_import_w + slot.pv_w - slot.load_w for slot in inputs.slots]
    result: dict[str, list[float]] = {}
    for car in inputs.vehicles:
        watts = [0.0] * count
        before = _slots_before(car.departure_at, inputs.slots) if car.need_kwh > 0 else 0

        def charge(index: int, power_w: float, *, from_pv: bool, w: list[float] = watts) -> None:
            added = power_w - w[index]
            w[index] = power_w
            room[index] -= added
            if from_pv:
                pv_left[index] -= added

        left = car.need_kwh
        for index in range(before):
            if left <= _KWH_EPS:
                break
            power_w = min(pv_left[index], car.max_w, max(left / SLOT_H * 1000, car.min_w))
            if power_w < car.min_w:
                continue
            charge(index, power_w, from_pv=True)
            left -= power_w * SLOT_H / 1000

        left = car.horizon_need_kwh - sum(watts) * SLOT_H / 1000
        cheapest = sorted(range(before), key=lambda index: (inputs.slots[index].import_ct, index))
        for index in cheapest:
            if left <= _KWH_EPS:
                break
            current = watts[index]
            power_w = max(min(car.max_w, current + left / SLOT_H * 1000), car.min_w)
            power_w = min(power_w, current + room[index])
            if power_w < car.min_w or power_w <= current:
                continue
            charge(index, power_w, from_pv=False)
            left -= (power_w - current) * SLOT_H / 1000
        result[car.key] = watts
    return result


def per_slot(powers: Mapping[str, Sequence[float]], count: int) -> list[dict[str, float]]:
    """Ladeleistung je Slot und Auto aus ``fallback_vehicles``; ein Auto unter ``EV_ON_W``
    fehlt im Slot."""
    return [
        {key: watts[index] for key, watts in powers.items() if watts[index] >= EV_ON_W}
        for index in range(count)
    ]


def fallback_intents(
    inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]] = ()
) -> list[BatteryIntent]:
    """Absicht je Slot des Ersatzplans für den Akku; ``ev_w`` ist die Ladeleistung der Autos je
    Slot (``per_slot(fallback_vehicles(…))``), die die Nachrechnung mitrechnet."""
    count = len(inputs.slots)
    intents = [BatteryIntent() for _ in range(count)]
    expensive = expensive_today(inputs)
    if not expensive:
        return intents

    params = params_of(inputs)
    slots = sim_slots(inputs, ev_w)
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
            # Unter dem höchsten Netzbezug bleibt neben Haus und Autos Platz fürs Netzladen
            headroom_w = (
                inputs.battery.grid_max_import_w
                - steps[candidate].ev_grid_w
                - max(0.0, slots[candidate].load_w - slots[candidate].pv_w)
            )
            cap_w = min(grid_charge_cap_w(inputs, candidate), headroom_w)
            power_w = ROUND_W * floor(cap_w / ROUND_W)
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
