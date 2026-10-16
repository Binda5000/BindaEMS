"""Plausibilitätsprüfung eines Plans des Optimierers (Plan 3b, Präzisierung 9).

Prüft die Flüsse des Optimierers und die Nachrechnung der Absichten mit dem Akkumodell. Ein
Befund heißt, dass der Plan nicht ausgeführt werden darf; der Planungsdienst nimmt dann den
Ersatzplan mit dem ersten Befund als Grund. Je Art wird nur der erste Slot gemeldet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from bindaems.app.planning.battery_model import SLOT_H, SimStep, eta, params_of, sim_slots
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

BALANCE_TOL_W: Final = 1.0  # Bilanz und Leistungsgrenzen
SOC_TOL_KWH: Final = 0.01
R1_TOL_KWH: Final = 0.1  # Einspeisung nach einem ``NETZLADEN``-Slot (Regel R1)
USEFUL_W: Final = 100.0  # ab dieser Leistung zählt ein späterer Slot als Nutzen des Netzladens

PaysOffBy = Literal["discharge", "import"]


def export_after_kwh(steps: Sequence[SimStep], index: int) -> float:
    """Einspeisung in kWh ab dem Slot ``index`` (eingeschlossen) bis zum Ende der Schritte."""
    return sum(item.export_w for item in steps[index:]) * SLOT_H / 1000


def pays_off(
    inputs: PlanInputs,
    steps: Sequence[SimStep],
    index: int,
    *,
    by: PaysOffBy = "discharge",
) -> tuple[int, float] | None:
    """Späterer Slot und sein Bezugspreis, mit dem die Spread-Regel für ``index`` gilt.

    Gesucht ist ein späterer Slot mit Entladung (``by="discharge"``) bzw. Netzbezug
    (``by="import"``) ab ``USEFUL_W``, dessen Preis nach Wirkungsgrad, Verschleiß und Marge über
    dem des Slots ``index`` liegt: ``π_später · η_rt − π_t − w ≥ m``. Der teuerste solche Slot
    zählt, bei Gleichstand der frühere; ``None``, wenn es keinen gibt.
    """
    planning = inputs.planning
    roundtrip = eta(planning.roundtrip_pct) ** 2
    now_ct = inputs.slots[index].import_ct
    best: tuple[int, float] | None = None
    for later in range(index + 1, len(steps)):
        item = steps[later]
        used_w = item.discharge_w if by == "discharge" else item.import_w
        later_ct = inputs.slots[later].import_ct
        if used_w < USEFUL_W:
            continue
        if later_ct * roundtrip - now_ct - planning.wear_ct < planning.margin_ct:
            continue
        if best is None or later_ct > best[1]:
            best = (later, later_ct)
    return best


def _local_time(inputs: PlanInputs, index: int) -> str:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).strftime("%H:%M")


def _kwh_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def check_plan(
    inputs: PlanInputs,
    result: OptimizerResult,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
) -> list[str]:
    """Befunde in der Reihenfolge der Slots (je Art der erste); leer, wenn der Plan plausibel ist.

    ``steps`` ist die Nachrechnung der ``intents`` mit dem Akkumodell.
    """
    params = params_of(inputs)
    slots = sim_slots(inputs)
    found: dict[str, tuple[int, str]] = {}

    def report(kind: str, index: int, text: str) -> None:
        if kind not in found or index < found[kind][0]:
            found[kind] = (index, text)

    def soc_outside(soc_kwh: float) -> bool:
        return soc_kwh < params.lo_kwh - SOC_TOL_KWH or soc_kwh > params.hi_kwh + SOC_TOL_KWH

    for index, (flows, slot, inp) in enumerate(
        zip(result.flows, slots, inputs.slots, strict=False)
    ):
        at = _local_time(inputs, index)
        pv_w = flows.pv_h + flows.pv_b + flows.pv_x
        load_w = flows.pv_h + flows.gr_h + flows.bt_h
        if abs(pv_w - inp.pv_w) > BALANCE_TOL_W or abs(load_w - inp.load_w) > BALANCE_TOL_W:
            report("balance", index, f"Bilanz verletzt um {at}")
        charge_w = flows.pv_b + flows.gr_b
        if (
            charge_w > slot.max_charge_w + BALANCE_TOL_W
            or flows.bt_h > slot.max_discharge_w + BALANCE_TOL_W
            or (charge_w > BALANCE_TOL_W and flows.bt_h > BALANCE_TOL_W)
        ):
            report("power", index, f"Leistung über der Grenze um {at}")
        if soc_outside(flows.soc_end_kwh):
            report("soc", index, f"SOC außerhalb der Grenzen um {at}")

    for index, (item, slot) in enumerate(zip(steps, slots, strict=False)):
        at = _local_time(inputs, index)
        if item.charge_pv_w + item.charge_grid_w > slot.max_charge_w + BALANCE_TOL_W:
            report("power", index, f"Leistung über der Grenze um {at}")
        if soc_outside(item.soc_end_kwh):
            report("soc", index, f"SOC außerhalb der Grenzen um {at}")

    switch = inputs.planning.grid_charge_allowed
    for index, intent in enumerate(intents):
        if intent.kind is not BatteryIntentKind.NETZLADEN:
            continue
        at = _local_time(inputs, index)
        if not switch:
            report("switch", index, f"Netzladen um {at}, obwohl ausgeschaltet")
        after_kwh = export_after_kwh(steps, index)
        if after_kwh > R1_TOL_KWH:
            exported = _kwh_text(after_kwh)
            report(
                "r1",
                index,
                f"Regel R verletzt: Netzladen um {at}, danach {exported} kWh Einspeisung",
            )
        # Mit Leistungspreis darf Netzladen auch einer Spitze ausweichen.
        if not inputs.peak_months and pays_off(inputs, steps, index) is None:
            report("spread", index, f"Netzladen um {at} rechnet sich nicht")

    return [text for _, text in sorted(found.values(), key=lambda entry: entry[0])]
