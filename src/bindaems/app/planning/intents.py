"""Ausführbare Akku-Absichten aus den Flüssen des Optimierers (Plan 3b, Präzisierung 7).

Der Optimierer plant Leistungen; ausgeführt wird eine von drei Absichten je Slot. Die Rundung
auf diese Absichten ist grob, deshalb rechnet der Planungsdienst den Plan danach mit dem
Akkumodell nach und prüft ihn auf Plausibilität.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil, floor
from typing import Final

from bindaems.app.planning.battery_model import SLOT_H, params_of, sim_slots, simulate
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult, ev_power_w, grid_charge_cap_w
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_after_kwh, grid_charge_pays
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

GRID_CHARGE_MIN_W: Final = 100.0  # darunter ist es kein Netzladen
ROUND_W: Final = 100.0  # Schritt der Netzladeleistung
HOLD_SHARE: Final = 0.5  # Halten, wenn weniger als dieser Anteil des Eigenverbrauchs entlädt
HOLD_MIN_W: Final = 100.0  # darunter lohnt es sich nicht, den Akku zu halten


def derive_intents(inputs: PlanInputs, result: OptimizerResult) -> list[BatteryIntent]:
    """Absicht je Slot; ohne Flüsse (keine Lösung) eine leere Liste.

    ``NETZLADEN(P)`` bei Netzladen ab ``GRID_CHARGE_MIN_W`` mit ``P`` auf ``ROUND_W`` gerundet
    und durch ``GC_t`` begrenzt. Sonst ``HALTEN``, wenn der Optimierer weniger als
    ``HOLD_SHARE`` dessen entlädt, was der Eigenverbrauch lieferte (mindestens ``HOLD_MIN_W``);
    die Hälfte ist die Rundung auf die nächste ausführbare Absicht. Sonst ``EIGENVERBRAUCH``.

    Zuletzt hält ``_keep_rule_r1`` Regel R1 an der Nachrechnung ein, und
    ``_drop_unprofitable`` streicht Netzladen, das sich dort nicht rechnet. Die Nachrechnung
    kennt die Ladeleistung der Autos aus den Flüssen (``ev_power_w``); der Eigenverbrauch, mit
    dem ``HALTEN`` vergleicht, ist der des Hauses ohne Autos, denn der Akku gibt nichts ins Auto.
    """
    if not result.flows:
        return []
    ev_w = ev_power_w(result)
    params = params_of(inputs)
    slots = sim_slots(inputs, ev_w)
    soc_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    intents: list[BatteryIntent] = []
    plain: list[BatteryIntent] = []  # Absicht je Slot nach der Regel ohne Netzladen
    for index, flows in enumerate(result.flows):
        slot = slots[index]
        deliverable_w = max(0.0, soc_kwh - params.lo_kwh) * params.eta_dis / SLOT_H * 1000
        # ``load_w`` ist das Haus ohne Autos
        own_w = min(max(0.0, slot.load_w - slot.pv_w), slot.max_discharge_w, deliverable_w)
        if own_w >= HOLD_MIN_W and flows.bt_h < HOLD_SHARE * own_w:
            plain.append(BatteryIntent(kind=BatteryIntentKind.HALTEN))
        else:
            plain.append(BatteryIntent())
        intents.append(plain[-1])
        soc_kwh = flows.soc_end_kwh
        if flows.gr_b >= GRID_CHARGE_MIN_W:
            cap_w = grid_charge_cap_w(inputs, index)
            power_w = min(
                ROUND_W * floor(flows.gr_b / ROUND_W + 0.5), ROUND_W * floor(cap_w / ROUND_W)
            )
            if power_w > 0:
                intents[-1] = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=power_w)
    return _drop_unprofitable(inputs, _keep_rule_r1(inputs, intents, ev_w), plain, ev_w)


def _keep_rule_r1(
    inputs: PlanInputs,
    intents: list[BatteryIntent],
    ev_w: Sequence[Mapping[str, float]] = (),
) -> list[BatteryIntent]:
    """Kürzt Netzladen, bis die Nachrechnung nach keinem ``NETZLADEN``-Slot mehr als
    ``R1_TOL_KWH`` einspeist.

    Wenn Regel R1 bindet, füllt der Optimierer den Akku bis zum Ende genau; schon die Rundung
    einer Teilentladung zu ``HALTEN`` lässt die Nachrechnung dann überlaufen und einspeisen. Der
    letzte betroffene ``NETZLADEN``-Slot lädt um die eingespeiste Energie weniger (auf
    ``ROUND_W`` aufgerundet); bleibt weniger als ``GRID_CHARGE_MIN_W``, hält er den Akku.
    ``ev_w`` ist die Ladeleistung der Autos je Slot (``sim_slots``).
    """
    params = params_of(inputs)
    slots = sim_slots(inputs, ev_w)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    result = list(intents)
    for _ in range(len(result) * 100):  # jede Runde kürzt um mindestens ``ROUND_W``
        steps = simulate(soc0_kwh, slots, result, params)
        late = [
            index
            for index, intent in enumerate(result)
            if intent.kind is BatteryIntentKind.NETZLADEN
            and export_after_kwh(steps, index) > R1_TOL_KWH
        ]
        if not late:
            break
        index = late[-1]
        excess_w = export_after_kwh(steps, index) / SLOT_H * 1000
        power_w = min(result[index].grid_charge_w, steps[index].charge_grid_w)
        power_w = ROUND_W * floor((power_w - ROUND_W * ceil(excess_w / ROUND_W)) / ROUND_W)
        result[index] = (
            BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=power_w)
            if power_w >= GRID_CHARGE_MIN_W
            else BatteryIntent(kind=BatteryIntentKind.HALTEN)
        )
    return result


def _drop_unprofitable(
    inputs: PlanInputs,
    intents: list[BatteryIntent],
    plain: list[BatteryIntent],
    ev_w: Sequence[Mapping[str, float]] = (),
) -> list[BatteryIntent]:
    """Streicht ``NETZLADEN``, das sich an der Nachrechnung nicht rechnet (``grid_charge_pays``,
    dieselbe Rechnung wie die Plausibilität); der Slot bekommt seine Absicht aus ``plain``.

    Die Rundung kann die Nutzung verschieben: Fehlen in einem billigen Slot nach dem Netzladen
    weniger als ``HOLD_MIN_W``, wird er ``EIGENVERBRAUCH`` und verbraucht die geladene Energie
    sofort, obwohl der Optimierer sie für später geladen hat. Gestrichen wird je Runde der letzte
    betroffene Slot, danach wird neu gerechnet; das Streichen speist nie mehr ein (Regel R1).

    Aufwand: im schlimmsten Fall eine Nachrechnung je Slot (so viele Runden, wie es Slots gibt);
    praktisch ist es meist eine Runde, weil selten ein ``NETZLADEN`` wegfällt. ``ev_w`` ist die
    Ladeleistung der Autos je Slot (``sim_slots``).
    """
    params = params_of(inputs)
    slots = sim_slots(inputs, ev_w)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    result = list(intents)
    for _ in range(len(result)):  # jede Runde streicht ein ``NETZLADEN``
        steps = simulate(soc0_kwh, slots, result, params)
        idle = [
            index
            for index, intent in enumerate(result)
            if intent.kind is BatteryIntentKind.NETZLADEN
            and not grid_charge_pays(inputs, steps, index)
        ]
        if not idle:
            break
        result[idle[-1]] = plain[idle[-1]]
    return result
