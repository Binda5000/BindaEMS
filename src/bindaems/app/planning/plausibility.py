"""Plausibilitätsprüfung eines Plans des Optimierers (Plan 3b, Präzisierung 9).

Prüft die Flüsse des Optimierers und die Nachrechnung der Absichten mit dem Akkumodell. Ein
Befund heißt, dass der Plan nicht ausgeführt werden darf; der Planungsdienst nimmt dann den
Ersatzplan mit dem ersten Befund als Grund. Je Art wird nur der erste Slot gemeldet.

Geplante Autos (Plan 4b, Präzisierung 15): Die PV-Bilanz zählt die PV ins Auto mit; je Auto ist
die Ladeleistung 0 oder zwischen ``min_w`` und ``max_w`` (``BALANCE_TOL_W`` Toleranz), und ab dem
ersten Slot ab der Abfahrt lädt es nicht. Die Befunde gelten je Auto.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from bindaems.app.planning.battery_model import SLOT_H, SimStep, params_of, sim_slots
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult
from bindaems.app.planning.vehicles import _slots_before
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

BALANCE_TOL_W: Final = 1.0  # Bilanz und Leistungsgrenzen
SOC_TOL_KWH: Final = 0.01
R1_TOL_KWH: Final = 0.1  # Einspeisung nach einem ``NETZLADEN``-Slot, solange der Akku geladen ist
EMPTY_TOL_KWH: Final = 0.1  # so nah an der Reserve gilt der Akku der Nachrechnung als leer
# Über dieser Entladung zählt ein späterer Slot als Nutzen des Netzladens (Nachtrag 18.10.2026:
# jede Entladung über der Toleranz der Bilanz; der Optimierer kennt keine Mindestentladung).
USED_ABOVE_W: Final = BALANCE_TOL_W
MENTION_W: Final = 100.0  # ab dieser Leistung nennt eine Erklärung einen Fluss oder Netzbezug

PaysOffBy = Literal["discharge", "import"]


def charged_window_end(steps: Sequence[SimStep], index: int, lo_kwh: float) -> int:
    """Ende des Fensters, in dem der Akku ab Slot ``index`` noch geladen ist (ausgeschlossen).

    Gesucht ist der erste Slot ab ``index``, der mit höchstens ``lo_kwh + EMPTY_TOL_KWH`` endet;
    der Akku ist dann leer, und das Fenster schließt diesen Slot ein. Gibt es keinen, reicht es bis
    zum Ende der Schritte.
    """
    for later in range(index, len(steps)):
        if steps[later].soc_end_kwh <= lo_kwh + EMPTY_TOL_KWH:
            return later + 1
    return len(steps)


def export_while_charged_kwh(steps: Sequence[SimStep], index: int, lo_kwh: float) -> float:
    """Einspeisung in kWh ab dem Slot ``index`` (eingeschlossen), solange der Akku noch geladen ist.

    Regel R1 verbietet Netzladen nur, wenn die Nachrechnung danach einspeist, bevor der Akku
    wieder die Reserve ``lo_kwh`` erreicht (``charged_window_end``); danach kann geladene Energie
    der PV nicht mehr im Weg stehen.
    """
    end = charged_window_end(steps, index, lo_kwh)
    return sum(item.export_w for item in steps[index:end]) * SLOT_H / 1000


def spread_pays(planning: PlanningSettings, now_ct: float, later_ct: float) -> bool:
    """Spread-Regel: Netzladen zu ``now_ct`` rechnet sich für eine Entladung zu ``later_ct``.

    Je kWh aus dem Netz liefert der Akku ``η_rt`` kWh zurück; der Verschleiß fällt wie im
    Optimierer auf diese Entladung: ``η_rt · (π_später − w) − π_t ≥ m``. Plausibilität und
    Ersatzplan rechnen beide hiermit.
    """
    roundtrip = planning.roundtrip_pct / 100
    return roundtrip * (later_ct - planning.wear_ct) - now_ct >= planning.margin_ct


def pays_off(
    inputs: PlanInputs,
    steps: Sequence[SimStep],
    index: int,
    *,
    by: PaysOffBy = "discharge",
) -> tuple[int, float] | None:
    """Späterer Slot und sein Bezugspreis, mit dem die Spread-Regel für ``index`` gilt.

    Gesucht ist ein späterer Slot mit Entladung über ``USED_ABOVE_W`` (``by="discharge"``) bzw.
    Netzbezug des Hauses ab ``MENTION_W`` (``by="import"``, nur für die Erklärung „lohnt sich“;
    der Bezug der Autos zählt nicht, denn der Akku gibt nichts ins Auto), für den
    ``spread_pays`` gilt. Der teuerste solche Slot zählt, bei Gleichstand der frühere; ``None``,
    wenn es keinen gibt.
    """
    now_ct = inputs.slots[index].import_ct
    best: tuple[int, float] | None = None
    for later in range(index + 1, len(steps)):
        item = steps[later]
        used = (
            item.discharge_w > USED_ABOVE_W
            if by == "discharge"
            else item.import_w - item.ev_grid_w >= MENTION_W
        )
        later_ct = inputs.slots[later].import_ct
        if not used:
            continue
        if not spread_pays(inputs.planning, now_ct, later_ct):
            continue
        if best is None or later_ct > best[1]:
            best = (later, later_ct)
    return best


def pays_off_at_end(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> bool:
    """Netzladen im Slot ``index`` rechnet sich mit dem Restwert am Horizontende.

    Der Optimierer bewertet die Energie im Akku am Ende mit ``end_value_ct``; Netzladen für die
    Zeit danach ist also eine spätere Nutzung, wenn die Ladung bis zum Ende im Akku bleibt (End-SOC
    der Nachrechnung mindestens um die geladene Energie über der Reserve) und
    ``η_ch · v_end − π_t ≥ m`` gilt. Der Verschleiß steckt schon im Restwert.
    """
    params = params_of(inputs)
    charged_kwh = params.eta_ch * steps[index].charge_grid_w * SLOT_H / 1000
    if steps[-1].soc_end_kwh - params.lo_kwh < charged_kwh - SOC_TOL_KWH:
        return False
    now_ct = inputs.slots[index].import_ct
    return params.eta_ch * inputs.end_value_ct - now_ct >= inputs.planning.margin_ct


def grid_charge_pays(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> bool:
    """Netzladen im Slot ``index`` rechnet sich an der Nachrechnung ``steps``.

    Das gilt mit einer späteren Entladung (``pays_off``) oder mit dem Restwert am Horizontende
    (``pays_off_at_end``); mit Leistungspreis immer, weil Netzladen dann auch einer Spitze
    ausweichen darf. Plausibilität und Absichten rechnen beide hiermit.
    """
    return (
        bool(inputs.peak_months)
        or pays_off(inputs, steps, index) is not None
        or pays_off_at_end(inputs, steps, index)
    )


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

    ``steps`` ist die Nachrechnung der ``intents`` mit dem Akkumodell. Flüsse, Schritte und Slots
    müssen gleich viele sein, sonst ``ValueError``.
    """
    params = params_of(inputs)
    slots = sim_slots(inputs)
    found: dict[str, tuple[int, str]] = {}

    def report(kind: str, index: int, text: str) -> None:
        if kind not in found or index < found[kind][0]:
            found[kind] = (index, text)

    def soc_outside(soc_kwh: float) -> bool:
        return soc_kwh < params.lo_kwh - SOC_TOL_KWH or soc_kwh > params.hi_kwh + SOC_TOL_KWH

    for index, (flows, slot, inp) in enumerate(zip(result.flows, slots, inputs.slots, strict=True)):
        at = _local_time(inputs, index)
        pv_w = flows.pv_h + flows.pv_b + flows.pv_x + sum(pv for pv, _ in flows.ev.values())
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

    for car in inputs.vehicles:
        departure = _slots_before(car.departure_at, inputs.slots)
        for index, flows in enumerate(result.flows):
            pv, grid = flows.ev.get(car.key, (0.0, 0.0))
            power_w = pv + grid
            if power_w <= BALANCE_TOL_W:
                continue
            if index >= departure:
                report(f"after:{car.key}", index, f"Auto {car.name} lädt nach der Abfahrt")
            if power_w < car.min_w - BALANCE_TOL_W or power_w > car.max_w + BALANCE_TOL_W:
                at = _local_time(inputs, index)
                report(
                    f"limits:{car.key}",
                    index,
                    f"Auto {car.name} lädt außerhalb seiner Grenzen um {at}",
                )

    for index, (item, slot) in enumerate(zip(steps, slots, strict=True)):
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
        while_charged_kwh = export_while_charged_kwh(steps, index, params.lo_kwh)
        if while_charged_kwh > R1_TOL_KWH:
            exported = _kwh_text(while_charged_kwh)
            report(
                "r1",
                index,
                f"Regel R verletzt: Netzladen um {at}, danach {exported} kWh Einspeisung",
            )
        if not grid_charge_pays(inputs, steps, index):
            report("spread", index, f"Netzladen um {at} rechnet sich nicht")

    return [text for _, text in sorted(found.values(), key=lambda entry: entry[0])]
