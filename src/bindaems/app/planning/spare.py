"""Übriger Akku für Autos in Sofort (Plan Akku ins Auto, wenn er übrig ist, Entscheidung 5).

Autos in „Sofort“ kennt der Optimierer nicht; ihre Last ist unbekannt. Der Plan sagt deshalb je
Slot nur, ob Akkustrom für sie übrig ist und sich rechnet (``PlanSlot.now_battery``); wie viel,
begrenzt der core mit der Plan-Schwelle des Slots. Reine Rechnung an der Nachrechnung des Plans.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from bindaems.app.planning.battery_model import SimStep, params_of
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.plausibility import grid_in_battery
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

FULL_TOL_KWH: Final = 0.05  # so nah an der Obergrenze gilt der Akku der Nachrechnung als voll


def spare_value_ct(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> float:
    """Wert einer kWh, die nach Slot ``index`` im Akku bliebe, in ct.

    Füllt sich der Akku in einem späteren Slot (SOC am Slotende mindestens
    ``hi_kwh − FULL_TOL_KWH``) und speist dort ein (``export_w > 0``), verdrängt die kWh dort
    Einspeisung: Sie ist den Einspeisepreis des ersten solchen Slots wert (ohne Preis 0). Sonst
    bleibt sie bis zum Horizontende und ist den Restwert ``end_value_ct`` wert.
    """
    full_kwh = params_of(inputs).hi_kwh - FULL_TOL_KWH
    for later in range(index + 1, len(steps)):
        item = steps[later]
        if item.soc_end_kwh >= full_kwh and item.export_w > 0:
            return inputs.slots[later].feed_in_ct or 0.0
    return inputs.end_value_ct


def now_battery_flags(
    inputs: PlanInputs, intents: Sequence[BatteryIntent], steps: Sequence[SimStep]
) -> list[bool]:
    """Je Slot, ob Autos in Sofort aus dem Akku laden dürfen (``PlanSlot.now_battery``).

    Das gilt, wenn der Schalter ``planning.car_battery_allowed`` an ist, die Absicht
    ``EIGENVERBRAUCH`` ist, keine Netzenergie im Akku sein kann (Entscheidung 2,
    ``grid_in_battery``, auch von vor dem Horizont) und sich Akkustrom rechnet:
    ``import_ct − wear_ct − margin_ct ≥ v / η_dis`` mit ``v = spare_value_ct``. Dass der Plan vom
    Optimierer kommt, prüft der Aufrufer.
    """
    planning = inputs.planning
    if not planning.car_battery_allowed:
        return [False] * len(intents)
    params = params_of(inputs)
    grid_inside = grid_in_battery(
        intents, steps, params.lo_kwh, at_start=inputs.grid_in_battery_at_start
    )
    flags: list[bool] = []
    for index, intent in enumerate(intents):
        if intent.kind is not BatteryIntentKind.EIGENVERBRAUCH or grid_inside[index]:
            flags.append(False)
            continue
        gain_ct = inputs.slots[index].import_ct - planning.wear_ct - planning.margin_ct
        flags.append(gain_ct >= spare_value_ct(inputs, steps, index) / params.eta_dis)
    return flags
