"""Plausibilitätsprüfung eines Plans des Optimierers (Plan 3b, Präzisierung 9).

Prüft die Flüsse des Optimierers und die Nachrechnung der Absichten mit dem Akkumodell. Ein
Befund heißt, dass der Plan nicht ausgeführt werden darf; der Planungsdienst nimmt dann den
Ersatzplan mit dem ersten Befund als Grund. Je Art wird nur der erste Slot gemeldet.

Geplante Autos (Plan 4b, Präzisierung 15): Die PV-Bilanz zählt die PV ins Auto mit; je Auto ist
die Ladeleistung 0 oder zwischen ``min_w`` und ``max_w`` (``BALANCE_TOL_W`` Toleranz), und ab dem
ersten Slot ab der Abfahrt lädt es nicht. Die Befunde gelten je Auto. Ein erwartetes Auto
(Plan 5b, Präzisierung 10) lädt auch vor dem ersten Slot ab seiner Ankunft nicht.

Akku ins Auto (Plan Akku ins Auto, wenn er übrig ist): Die Leistung eines Autos und die
Entladegrenze zählen den Akkuanteil ``bt_ev`` mit. Gibt der Akku einem Auto mehr als
``BALANCE_TOL_W``, muss der Schalter an sein, der Slot ``EIGENVERBRAUCH`` sein, keine Netzenergie im
Akku sein können (Entscheidung 2, ``grid_in_battery``, auch aus einem Netzladen vor dem Horizont)
und nichts eingespeist werden.

Vorheizen und Vorkühlen (Plan 5c): ``check_thermal`` prüft den Versatz je Slot gegen das
Komfortband der Betriebsart; ein Befund verwirft nur die Verschiebung, nicht den Plan.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, Literal

from bindaems.app.planning.battery_model import SLOT_H, SimStep, params_of, sim_slots
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult, ev_battery_power_w
from bindaems.app.planning.thermal_shift import STEP_K
from bindaems.app.planning.vehicles import charge_window
from bindaems.shared.plan import (
    MAX_THERMAL_STEPS,
    BatteryIntent,
    BatteryIntentKind,
    Plan,
    PlanSlot,
)
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

BALANCE_TOL_W: Final = 1.0  # Bilanz und Leistungsgrenzen
SOC_TOL_KWH: Final = 0.01
R1_TOL_KWH: Final = 0.1  # Einspeisung nach einem ``NETZLADEN``-Slot, solange der Akku geladen ist
EMPTY_TOL_KWH: Final = R1_TOL_KWH  # so nah an der Reserve gilt der Akku der Nachrechnung als leer
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


def grid_in_battery(
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    lo_kwh: float,
    *,
    at_start: bool = False,
) -> list[bool]:
    """Je Slot, ob nach einem Netzladen Netzenergie im Akku sein kann (Entscheidung 2).

    Das gilt für Slot ``t``, wenn es einen ``NETZLADEN``-Slot ``s ≤ t`` gibt, dessen Fenster
    ``charged_window_end(steps, s, lo_kwh)`` über ``t`` reicht: bis der Akku wieder leer ist,
    dieselbe Größe wie bei Regel R1. Mit ``at_start`` (Netzenergie von vor dem Horizont,
    ``PlanInputs.grid_in_battery_at_start``) gilt es ebenso ab Slot 0, bis die Nachrechnung den
    Akku leer zeigt.
    """
    result = [False] * len(steps)
    if at_start:
        for later in range(charged_window_end(steps, 0, lo_kwh)):
            result[later] = True
    for index, intent in enumerate(intents):
        if intent.kind is not BatteryIntentKind.NETZLADEN:
            continue
        for later in range(index, charged_window_end(steps, index, lo_kwh)):
            result[later] = True
    return result


def grid_in_battery_at_start(inputs: PlanInputs, previous: Plan | None, *, carried: bool) -> bool:
    """Ob zu Beginn des Horizonts Netzenergie im Akku sein kann (Entscheidung 2 über Pläne hinweg,
    Fix-Welle F2; ``PlanInputs.grid_in_battery_at_start``).

    Abgeleitet aus dem vorigen Plan ``previous`` (dem zuletzt gespeicherten, an den der core sich
    gehalten hat) und dem SOC jetzt, weiter wie ``grid_in_battery``, „bis der Akku wieder leer ist“:

    - Ist der Akku jetzt leer (SOC höchstens ``lo_kwh + EMPTY_TOL_KWH``, wie die Nachrechnung), ist
      keine Netzenergie darin.
    - Sonst ist sie darin, wenn der vorige Plan sie schon zu Beginn annahm (``carried``, sein
      ``grid_in_battery_at_start``) oder einer seiner ``NETZLADEN``-Slots begonnen hat, solange der
      Plan galt (Beginn vor ``created_at`` dieses Laufs und vor ``valid_until``): Nach diesem
      Beginn hat der core ihn ausgeführt, der laufende Slot zählt also mit.

    Ohne vorigen Plan: keine. Gemessen wird der SOC nur bei jedem Lauf; war der Akku dazwischen
    kurz leer, bleibt die Netzenergie bis zum nächsten leeren SOC eines Laufs. Das ist die
    vorsichtige Seite: Akku ins Auto und Einspeisung bleiben dann länger gesperrt.
    """
    params = params_of(inputs)
    soc_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    if soc_kwh <= params.lo_kwh + EMPTY_TOL_KWH or previous is None:
        return False
    if carried:
        return True
    applied_until = min(ensure_utc(inputs.created_at), ensure_utc(previous.valid_until))
    return any(
        slot.intent.kind is BatteryIntentKind.NETZLADEN and ensure_utc(slot.start) < applied_until
        for slot in previous.slots
    )


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


def _hhmm(moment: datetime) -> str:
    return ensure_utc(moment).astimezone(LOCAL_TZ).strftime("%H:%M")


def _local_time(inputs: PlanInputs, index: int) -> str:
    return _hhmm(inputs.slots[index].start)


def _kwh_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def check_plan(
    inputs: PlanInputs,
    result: OptimizerResult,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    *,
    ev_battery_w: Sequence[Mapping[str, float]] | None = None,
) -> list[str]:
    """Befunde in der Reihenfolge der Slots (je Art der erste); leer, wenn der Plan plausibel ist.

    ``steps`` ist die Nachrechnung der ``intents`` mit dem Akkumodell, mit der Ladeleistung der
    Autos und ihrem Akkuanteil. ``ev_battery_w`` ist der Akkuanteil je Auto und Slot, den der Plan
    behält (``drop_held_battery``); ihn prüfen die Befunde „Akku ins Auto“. Ohne Angabe gilt der
    aus ``result`` (``ev_battery_power_w``). Bilanz, Leistungsgrenzen und Grenzen der Autos rechnen
    immer mit den Flüssen von ``result``. Flüsse, Schritte und Slots müssen gleich viele sein,
    sonst ``ValueError``.
    """
    params = params_of(inputs)
    slots = sim_slots(inputs)
    from_battery = ev_battery_power_w(result) if ev_battery_w is None else list(ev_battery_w)
    if len(from_battery) != len(intents):
        raise ValueError("Akkuanteil der Autos muss zu den Absichten passen")
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
        discharge_w = flows.bt_h + sum(flows.ev_battery.values())
        if (
            charge_w > slot.max_charge_w + BALANCE_TOL_W
            or discharge_w > slot.max_discharge_w + BALANCE_TOL_W
            or (charge_w > BALANCE_TOL_W and discharge_w > BALANCE_TOL_W)
        ):
            report("power", index, f"Leistung über der Grenze um {at}")
        if soc_outside(flows.soc_end_kwh):
            report("soc", index, f"SOC außerhalb der Grenzen um {at}")

    for car in inputs.vehicles:
        window = charge_window(car, inputs.slots)
        for index, flows in enumerate(result.flows):
            pv, grid = flows.ev.get(car.key, (0.0, 0.0))
            power_w = pv + grid + flows.ev_battery.get(car.key, 0.0)
            if power_w <= BALANCE_TOL_W:
                continue
            if index >= window.stop:
                report(f"after:{car.key}", index, f"Auto {car.name} lädt nach der Abfahrt")
            if car.arrival_at is not None and index < window.start:
                at = _hhmm(car.arrival_at)
                report(
                    f"before:{car.key}",
                    index,
                    f"Auto {car.name} lädt vor der erwarteten Ankunft um {at}",
                )
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
                f"Regel R verletzt: Netzladen um {at}, danach {exported} kWh Einspeisung, "
                "bevor der Akku leer ist",
            )
        if not grid_charge_pays(inputs, steps, index):
            report("spread", index, f"Netzladen um {at} rechnet sich nicht")

    car_switch = inputs.planning.car_battery_allowed
    grid_inside = grid_in_battery(
        intents, steps, params.lo_kwh, at_start=inputs.grid_in_battery_at_start
    )
    for index, (flows, item, intent) in enumerate(zip(result.flows, steps, intents, strict=True)):
        if sum(from_battery[index].values()) <= BALANCE_TOL_W:
            continue
        at = _local_time(inputs, index)
        if not car_switch:
            report("car_switch", index, f"Akku ins Auto um {at}, obwohl ausgeschaltet")
        if intent.kind is not BatteryIntentKind.EIGENVERBRAUCH:
            report("car_hold", index, f"Akku ins Auto um {at} bei Halten oder Netzladen")
        if grid_inside[index]:
            report("car_grid", index, f"Akku ins Auto um {at}, solange Netzstrom im Akku ist")
        if flows.pv_x > BALANCE_TOL_W or item.export_w > BALANCE_TOL_W:
            report("car_export", index, f"Akku ins Auto um {at}, während eingespeist wird")

    return [text for _, text in sorted(found.values(), key=lambda entry: entry[0])]


def check_thermal(slots: Sequence[PlanSlot], *, up_k: float, down_k: float) -> list[str]:
    """Befund, wenn ein Slot einen Versatz der Klimaanlage (``thermal_steps``) außerhalb des
    Komfortbands ``[−down_k / 0,5, up_k / 0,5]`` Stufen oder außerhalb von ±6 Stufen plant; nur
    der erste solche Slot. Leer, wenn alle passen oder keiner einen Versatz hat."""
    lo = max(-MAX_THERMAL_STEPS, -math.floor(down_k / STEP_K + 1e-9))
    hi = min(MAX_THERMAL_STEPS, math.floor(up_k / STEP_K + 1e-9))
    for slot in slots:
        steps = slot.thermal_steps
        if steps is not None and not lo <= steps <= hi:
            return [f"Versatz der Klimaanlage außerhalb des Bands um {_hhmm(slot.start)}"]
    return []
