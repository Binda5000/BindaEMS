"""Tests der Plausibilitätsprüfung (Präzisierung 9): Bilanz, Grenzen, Schalter, Regel R1 und
Spread-Regel an der Nachrechnung."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from tests.plan_helpers import (
    SCENARIO_START,
    at,
    flow,
    flows_of,
    flows_result,
    month,
    planned_car,
    pv_arc,
    resimulate,
    sample_plan,
    scenario,
    small_battery,
)

from bindaems.app.planning.battery_model import SLOT_H, SimStep, params_of, sim_slots, simulate
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import OptimizerResult, SlotFlows, solve
from bindaems.app.planning.plausibility import (
    EMPTY_TOL_KWH,
    charged_window_end,
    check_plan,
    export_while_charged_kwh,
    grid_in_battery,
    grid_in_battery_at_start,
    pays_off,
    spread_pays,
)
from bindaems.app.planning.service import plan_once
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, Plan
from bindaems.shared.timeutil import SLOT


def test_clean_plan_has_no_issues() -> None:
    inputs = scenario(
        prices=[10.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[2000.0] * 8,
        soc_pct=20.0,
        grid_charge=True,
    )
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []


def test_start_soc_outside_limits_is_plausible() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=10.0)
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []


def test_grid_charge_when_switched_off_is_reported() -> None:
    inputs = scenario(prices=[10.0], pv=[0.0], load=[1000.0], grid_charge=False)
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000)]
    issues = check_plan(
        inputs, flows_result([flow(gr_h=1000)]), intents, resimulate(inputs, intents)
    )
    assert "Netzladen um 02:00, obwohl ausgeschaltet" in issues


def test_r1_on_resimulation_is_reported() -> None:
    inputs = scenario(
        prices=[5.0, 8.0],
        pv=[0.0, 9000.0],
        load=[500.0, 500.0],
        battery=small_battery(),
        soc_pct=90.0,
        grid_charge=True,
    )
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000), BatteryIntent()]
    result = flows_result([flow(gr_h=500, gr_b=1000), flow(pv_h=500, pv_x=8500)])
    issues = check_plan(inputs, result, intents, resimulate(inputs, intents))
    assert any(
        i.startswith("Regel R verletzt: Netzladen um 02:00, danach ")
        and i.endswith(" kWh Einspeisung, bevor der Akku leer ist")
        for i in issues
    )


def test_r1_allows_grid_charge_used_up_before_pv() -> None:
    # Die 0,5 kWh Netzladung (2 kW · 15 min) sind in Slot 1 verbraucht (Last 4 kW, Akku leer),
    # bevor Slot 2 mit PV einspeist: Regel R1 gilt nur bis der Akku leer ist.
    inputs = scenario(
        prices=[5.0, 40.0, 8.0],
        pv=[0.0, 0.0, 9000.0],
        load=[500.0, 4000.0, 500.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    intents = [
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000),
        BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH),
        BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH),
    ]
    steps = resimulate(inputs, intents)
    assert steps[2].export_w > 0  # der Test prüft wirklich eine Einspeisung hinter der Leerung
    issues = check_plan(inputs, flows_of(steps, inputs), intents, steps)
    assert not any(i.startswith("Regel R verletzt") for i in issues)


def test_unprofitable_grid_charge_is_reported_without_power_price() -> None:
    inputs = scenario(
        prices=[10.0, 12.0], pv=[0.0, 0.0], load=[1000.0, 1000.0], soc_pct=20.0, grid_charge=True
    )
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000), BatteryIntent()]
    steps = resimulate(inputs, intents)
    assert "Netzladen um 02:00 rechnet sich nicht" in check_plan(
        inputs, flows_of(steps, inputs), intents, steps
    )
    with_peak = inputs.model_copy(update={"peak_months": [month("2026-10", 2.0)]})
    assert "Netzladen um 02:00 rechnet sich nicht" not in check_plan(
        with_peak, flows_of(steps, with_peak), intents, steps
    )


def test_balance_violation_is_reported_once() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, soc_pct=50.0)
    intents = [BatteryIntent(), BatteryIntent()]
    result = flows_result([flow(gr_h=900.0), flow(gr_h=900.0)])  # 100 W fehlen
    issues = check_plan(inputs, result, intents, resimulate(inputs, intents))
    assert issues.count("Bilanz verletzt um 02:00") == 1
    assert not any("Bilanz verletzt um 02:15" in i for i in issues)


def test_soc_outside_limits_is_reported() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, soc_pct=50.0)
    intents = [BatteryIntent(), BatteryIntent()]
    ok = flow(gr_h=1000.0, soc_end_kwh=15.0)
    below = flow(gr_h=1000.0, soc_end_kwh=5.0)  # Reserve 6 kWh
    issues = check_plan(inputs, flows_result([ok, below]), intents, resimulate(inputs, intents))
    assert "SOC außerhalb der Grenzen um 02:15" in issues


def test_power_over_limit_and_simultaneous_are_reported() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, soc_pct=50.0)
    intents = [BatteryIntent(), BatteryIntent()]
    over = flow(gr_h=1000.0, bt_h=13000.0, soc_end_kwh=15.0)  # Grenze 12 kW
    both = SlotFlows(
        pv_h=0.0, pv_b=0.0, pv_x=0.0, gr_h=0.0, gr_b=500.0, bt_h=1000.0, soc_end_kwh=15.0
    )
    steps = resimulate(inputs, intents)
    assert "Leistung über der Grenze um 02:00" in check_plan(
        inputs, flows_result([over, over]), intents, steps
    )
    assert "Leistung über der Grenze um 02:15" in check_plan(
        inputs, flows_result([flow(gr_h=1000.0, soc_end_kwh=15.0), both]), intents, steps
    )


def test_issues_come_in_slot_order() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, soc_pct=50.0)
    intents = [BatteryIntent(), BatteryIntent()]
    result = flows_result([flow(gr_h=1000.0, soc_end_kwh=5.0), flow(gr_h=900.0, soc_end_kwh=15.0)])
    issues = check_plan(inputs, result, intents, resimulate(inputs, intents))
    assert issues == ["SOC außerhalb der Grenzen um 02:00", "Bilanz verletzt um 02:15"]


def _steps_of(soc_end_kwh: list[float], export_w: list[float]) -> list[SimStep]:
    """Schritte mit den gegebenen SOC-Enden und Einspeisungen (alles andere 0)."""
    return [
        SimStep(
            soc_start_kwh=0.0,
            soc_end_kwh=soc,
            import_w=0.0,
            export_w=export,
            charge_pv_w=0.0,
            charge_grid_w=0.0,
            discharge_w=0.0,
            cost_ct=0.0,
        )
        for soc, export in zip(soc_end_kwh, export_w, strict=True)
    ]


def test_export_while_charged_stops_when_battery_is_empty() -> None:
    # Reserve 2,0 kWh: Slot 1 endet mit 2,05 kWh, also innerhalb der Toleranz (0,1 kWh) leer;
    # die Einspeisung in Slot 2 liegt hinter dem Fenster und zählt nicht.
    steps = _steps_of([4.0, 2.05, 3.0], [0.0, 0.0, 3000.0])
    assert export_while_charged_kwh(steps, 0, 2.0) == 0.0
    assert charged_window_end(steps, 0, 2.0) == 2


def test_export_while_charged_counts_export_in_the_empty_slot() -> None:
    # Ein Slot, der den Akku leert und zugleich einspeist (PV über Last), zählt mit: 2 kW · 15 min.
    steps = _steps_of([4.0, 2.0], [0.0, 2000.0])
    assert export_while_charged_kwh(steps, 0, 2.0) == 0.5


def test_export_while_charged_without_empty_slot_counts_to_the_end() -> None:
    steps = _steps_of([4.0, 3.0, 5.0], [0.0, 0.0, 4000.0])
    assert export_while_charged_kwh(steps, 0, 2.0) == 1.0
    assert charged_window_end(steps, 0, 2.0) == 3


def test_export_while_charged_starts_at_the_given_slot() -> None:
    # Ab Slot 1 ist der Akku schon im Slot selbst leer: nur dessen Einspeisung zählt.
    steps = _steps_of([4.0, 2.0, 2.0], [4000.0, 1000.0, 8000.0])
    assert charged_window_end(steps, 1, 2.0) == 2
    assert export_while_charged_kwh(steps, 1, 2.0) == 0.25


def test_pays_off_picks_most_expensive_later_slot_earliest_on_tie() -> None:
    inputs = scenario(
        prices=[10.0, 40.0, 30.0, 40.0],
        pv=[0.0] * 4,
        load=[1000.0] * 4,
        soc_pct=20.0,
        grid_charge=True,
    )
    intents = [
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=3000),
        BatteryIntent(),
        BatteryIntent(),
        BatteryIntent(),
    ]
    steps = resimulate(inputs, intents)
    assert pays_off(inputs, steps, 0) == (1, 40.0)
    assert pays_off(inputs, steps, 3) is None


def test_pays_off_by_import_looks_at_grid_draw() -> None:
    inputs = scenario(
        prices=[10.0, 40.0], pv=[0.0, 0.0], load=[1000.0, 1000.0], soc_pct=20.0, grid_charge=True
    )
    steps = resimulate(
        inputs, [BatteryIntent(), BatteryIntent()]
    )  # leerer Akku: Bezug, keine Entladung
    assert pays_off(inputs, steps, 0) is None
    assert pays_off(inputs, steps, 0, by="import") == (1, 40.0)


# --- Restwert am Horizontende und Verschleiß (Final-Welle A1) -------------------------------


def _end_value_inputs() -> PlanInputs:
    """56 Slots ab 18:00 lokal: Abend und Nacht 32 ct, die letzten 2 h 15 ct, Restwert 22 ct,
    Netzladen an, kein PV. Der Akku entlädt am Abend und lädt am Ende für die Zeit danach."""
    return scenario(
        prices=[32.0] * 48 + [15.0] * 8,
        pv=[0.0] * 56,
        load=[1000.0] * 56,
        soc_pct=50.0,
        grid_charge=True,
        start="2026-10-16T16:00:00+00:00",
        end_value_ct=22.0,
    )


def test_grid_charge_for_the_end_value_is_plausible() -> None:
    inputs = _end_value_inputs()
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert any(i.kind is BatteryIntentKind.NETZLADEN for i in intents[48:])
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []
    assert plan_once(inputs, result).source == "optimizer"


def test_end_value_needs_the_charged_energy_to_stay_until_the_end() -> None:
    # Slot 0 lädt 2 kW aus dem Netz (15 ct), danach entleeren 8 kW Last den Akku bis zur
    # Reserve: Am Ende ist nichts von der Ladung übrig, der Restwert zählt nicht.
    inputs = scenario(
        prices=[15.0, 15.0, 15.0, 15.0],
        pv=[0.0] * 4,
        load=[0.0, 8000.0, 8000.0, 8000.0],
        battery=small_battery(),
        soc_pct=30.0,
        grid_charge=True,
        end_value_ct=22.0,
    )
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000)] + [
        BatteryIntent()
    ] * 3
    steps = resimulate(inputs, intents)
    assert steps[-1].soc_end_kwh < 2.0 + 0.01  # Reserve 2 kWh
    assert "Netzladen um 02:00 rechnet sich nicht" in check_plan(
        inputs, flows_of(steps, inputs), intents, steps
    )


def test_end_value_must_beat_price_and_margin() -> None:
    # η_ch · v_end − π_t ≥ m: 0,938 · 19 − 15 = 2,82 ≥ 2, aber 0,938 · 18 − 15 = 1,89 < 2.
    def issues(end_value_ct: float) -> list[str]:
        inputs = scenario(
            prices=[15.0] * 4,
            pv=[0.0] * 4,
            load=[500.0] * 4,
            battery=small_battery(),
            soc_pct=30.0,
            grid_charge=True,
            end_value_ct=end_value_ct,
        )
        intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000)] + [
            BatteryIntent(kind=BatteryIntentKind.HALTEN)
        ] * 3
        steps = resimulate(inputs, intents)
        return check_plan(inputs, flows_of(steps, inputs), intents, steps)

    assert "Netzladen um 02:00 rechnet sich nicht" not in issues(19.0)
    assert "Netzladen um 02:00 rechnet sich nicht" in issues(18.0)


def test_spread_rule_charges_wear_on_the_delivered_energy() -> None:
    # Verschleiß wie im Optimierer auf die Entladung: η_rt · (π_später − w) − π_t ≥ m.
    # 0,88 · (40 − 2) − 31,3 = 2,14 ≥ 2; mit w statt η_rt · w wären es 1,90 < 2.
    inputs = scenario(
        prices=[31.3, 40.0], pv=[0.0, 0.0], load=[1000.0, 1000.0], soc_pct=50.0, grid_charge=True
    )
    steps = resimulate(inputs, [BatteryIntent(), BatteryIntent()])
    assert pays_off(inputs, steps, 0) == (1, 40.0)
    assert spread_pays(inputs.planning, 31.3, 40.0)
    assert not spread_pays(inputs.planning, 31.5, 40.0)  # 1,94 < 2


# --- Kleine spätere Entladungen zählen (Nachtrag 18.10.2026) --------------------------------


def test_pays_off_counts_later_discharge_below_100_w() -> None:
    # Jede spätere Entladung über 1 W zählt als Nutzen, wenn sich der Spread rechnet.
    def hit(load_w: float) -> tuple[int, float] | None:
        inputs = scenario(
            prices=[10.0, 40.0], pv=[0.0, 0.0], load=[0.0, load_w], soc_pct=50.0, grid_charge=True
        )
        intents = [
            BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000),
            BatteryIntent(),
        ]
        return pays_off(inputs, resimulate(inputs, intents), 0)

    assert hit(50.0) == (1, 40.0)
    assert hit(0.5) is None


def test_optimizer_plan_using_the_charge_below_100_w_per_slot_passes() -> None:
    # Festes Beispiel aus dem Befund (Property-Test, Hypothesis): SOC an der Reserve, 300 W Last,
    # steigende PV. Der Optimierer lädt um 07:15 335 W aus dem Netz (10 ct) und entlädt um
    # 08:15–09:15 (19 ct) je Slot knapp unter 100 W. 0,88 · (19 − 2) − 10 = 4,96 ≥ 2: Das rechnet
    # sich, auch wenn kein Slot 100 W erreicht; früher meldete die Plausibilität „rechnet sich
    # nicht“.
    start = "2026-10-16T05:15:00+00:00"
    inputs = scenario(
        prices=[10.0] * 4 + [19.0] * 4 + [10.0] * 13,
        pv=pv_arc(start, 21, 344.0),
        load=[300.0] * 21,
        soc_pct=20.0,
        grid_charge=True,
        start=start,
        end_value_ct=6.642748367682174,
    )
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=300)
    steps = resimulate(inputs, intents)
    assert 0 < max(item.discharge_w for item in steps[4:8]) < 100
    plan = plan_once(inputs, result)
    assert plan.source == "optimizer", plan.issues


def test_plan_with_fixed_flows_discharging_below_100_w_passes() -> None:
    # Fest vorgegebene Flüsse: 1 kW Netzladen zu 10 ct, danach 50 W Entladung zu 40 ct. Jede
    # Entladung über 1 W zählt (0,88 · (40 − 2) − 10 ≥ 2); mit der alten Schwelle von 100 W
    # fiele der Plan durch.
    inputs = scenario(
        prices=[10.0, 40.0],
        pv=[0.0, 0.0],
        load=[0.0, 50.0],
        battery=small_battery(),
        soc_pct=50.0,
        grid_charge=True,
    )
    result = flows_result(
        [flow(gr_b=1000.0, soc_end_kwh=5.25), flow(bt_h=50.0, soc_end_kwh=5.2375)]
    )
    intents = derive_intents(inputs, result)
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000)
    plan = plan_once(inputs, result)
    assert plan.source == "optimizer", plan.issues


def test_unprofitable_grid_charge_of_another_plan_is_still_reported() -> None:
    # Die Spread-Prüfung bleibt für Pläne wirksam, die nicht aus den Absichten des Optimierers
    # kommen: 2 kW Netzladen zu 20 ct, danach Eigenverbrauch zu 20 ct (0,88 · 18 − 20 < 2).
    inputs = scenario(
        prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=50.0, grid_charge=True
    )
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000)] + [
        BatteryIntent()
    ] * 3
    steps = resimulate(inputs, intents)
    assert all(item.discharge_w > 100 for item in steps[1:])
    assert "Netzladen um 02:00 rechnet sich nicht" in check_plan(
        inputs, flows_of(steps, inputs), intents, steps
    )


def test_flows_and_steps_must_match_the_slots() -> None:
    # Fehlende Slots dürfen nicht stillschweigend ungeprüft bleiben.
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, soc_pct=50.0)
    intents = [BatteryIntent(), BatteryIntent()]
    steps = resimulate(inputs, intents)
    full = flows_of(steps, inputs)
    with pytest.raises(ValueError):
        check_plan(inputs, flows_result(full.flows[:1]), intents, steps)
    with pytest.raises(ValueError):
        check_plan(inputs, full, intents, steps[:1])


# Geplante Autos (Plan 4b, Präzisierung 15)


def _car_inputs(count: int = 4, departure: int = 2, **car: Any) -> PlanInputs:
    """``count`` Slots ohne PV mit 500 W Haus und einem Auto, das nach ``departure`` Slots
    abfährt (Tesla wie ``planned_car``: 3450 bis 11040 W)."""
    vehicle = planned_car(departure_at=at(SCENARIO_START) + departure * SLOT, **car)
    return scenario(
        prices=[20.0] * count, pv=[0.0] * count, load=[500.0] * count, vehicles=[vehicle]
    )


def _check(inputs: PlanInputs, flows: list[SlotFlows]) -> list[str]:
    """Befunde für ``flows`` mit der Nachrechnung von ``EIGENVERBRAUCH``; ``soc_end_kwh`` der
    Flüsse (bei ``flow`` die Änderung im Slot) zählt ab dem SOC des Laufs weiter."""
    soc_kwh = inputs.battery.soc_pct / 100 * inputs.battery.usable_kwh
    absolute: list[SlotFlows] = []
    for item in flows:
        soc_kwh += item.soc_end_kwh
        absolute.append(replace(item, soc_end_kwh=soc_kwh))
    flows = absolute
    result = flows_result(flows)
    intents = [BatteryIntent() for _ in flows]
    ev_w = [{k: pv + gr for k, (pv, gr) in f.ev.items()} for f in flows]
    steps = simulate(
        inputs.battery.soc_pct / 100 * inputs.battery.usable_kwh,
        sim_slots(inputs, ev_w),
        intents,
        params_of(inputs),
    )
    return check_plan(inputs, result, intents, steps)


def test_car_after_departure_is_implausible() -> None:
    inputs = _car_inputs(departure=2)
    flows = [
        flow(gr_h=500, ev={"tesla": (0.0, 11040.0)}),
        flow(gr_h=500),
        flow(gr_h=500, ev={"tesla": (0.0, 3450.0)}),
        flow(gr_h=500),
    ]
    assert _check(inputs, flows) == ["Auto Tesla Model 3 lädt nach der Abfahrt"]


def test_car_outside_its_limits_is_implausible() -> None:
    inputs = _car_inputs(departure=4)
    flows = [
        flow(gr_h=500, ev={"tesla": (0.0, 3449.5)}),  # 1 W Toleranz
        flow(gr_h=500, ev={"tesla": (0.0, 1000.0)}),
        flow(gr_h=500, ev={"tesla": (0.0, 11041.5)}),
        flow(gr_h=500, ev={"tesla": (0.0, 0.5)}),  # praktisch aus
    ]
    assert _check(inputs, flows) == ["Auto Tesla Model 3 lädt außerhalb seiner Grenzen um 02:15"]


def test_balance_counts_the_pv_into_the_car() -> None:
    vehicle = planned_car(departure_at=at(SCENARIO_START) + 2 * SLOT)
    inputs = scenario(prices=[20.0] * 2, pv=[6000.0] * 2, load=[500.0] * 2, vehicles=[vehicle])
    good = [flow(pv_h=500, pv_b=1500, ev={"tesla": (4000.0, 0.0)})] * 2
    assert _check(inputs, good) == []
    missing = [flow(pv_h=500, pv_b=1500)] * 2
    assert _check(inputs, missing)[0] == "Bilanz verletzt um 02:00"


def test_optimizer_plan_with_a_charging_car_is_plausible() -> None:
    # Ohne PV lädt das Auto in den billigen Slots aus dem Netz, der Akku deckt das Haus
    vehicle = planned_car(departure_at=at(SCENARIO_START) + 8 * SLOT, need_kwh=5.0)
    inputs = scenario(
        prices=[30.0, 30.0, 10.0, 10.0, 30.0, 30.0, 30.0, 30.0],
        pv=[0.0] * 8,
        load=[500.0] * 8,
        vehicles=[vehicle],
    )
    result = solve(inputs)
    plan = plan_once(inputs, result)
    assert plan.source == "optimizer", plan.issues
    assert sum(w for s in plan.slots for w in s.ev_w.values()) * SLOT_H / 1000 == pytest.approx(5.0)
    assert plan.slots[2].ev_w["tesla"] > 3000 and plan.slots[0].ev_w == {}
    assert plan.slots[2].import_w >= plan.slots[2].ev_w["tesla"] - 1e-6
    assert [v.key for v in plan.vehicles] == ["tesla"] and plan.vehicles[0].reachable


def test_fallback_plan_carries_the_cars() -> None:
    vehicle = planned_car(departure_at=at(SCENARIO_START) + 4 * SLOT, need_kwh=2.76)
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, vehicles=[vehicle])
    failed = OptimizerResult(
        status="error", flows=(), peaks_kw={}, objective_ct=None, solve_ms=0, message="kaputt"
    )
    plan = plan_once(inputs, failed)
    assert plan.source == "fallback"
    assert [s.ev_w for s in plan.slots] == [{"tesla": pytest.approx(11040.0)}, {}, {}, {}]
    assert plan.vehicles[0].planned_kwh == pytest.approx(2.76)
    assert plan.vehicles[0].reachable


# Erwartete Autos (Plan 5b, Präzisierung 10, Ruling T10-6)


def test_plausibility_flags_charging_before_arrival() -> None:
    # Ankunft im Slot 2 (02:30 Ortszeit): Laden davor ist ein Befund, ab der Ankunft nicht
    inputs = _car_inputs(departure=4, expected=True, arrival_at=at(SCENARIO_START) + 2 * SLOT)
    flows = [
        flow(gr_h=500),
        flow(gr_h=500, ev={"tesla": (0.0, 11040.0)}),
        flow(gr_h=500, ev={"tesla": (0.0, 11040.0)}),
        flow(gr_h=500, ev={"tesla": (0.0, 3450.0)}),
    ]
    assert _check(inputs, flows) == ["Auto Tesla Model 3 lädt vor der erwarteten Ankunft um 02:30"]
    allowed = [flow(gr_h=500), flow(gr_h=500), *flows[2:]]
    assert _check(inputs, allowed) == []


def test_optimizer_plan_with_an_expected_car_is_plausible() -> None:
    vehicle = planned_car(
        departure_at=at(SCENARIO_START) + 8 * SLOT,
        need_kwh=5.0,
        expected=True,
        arrival_at=at(SCENARIO_START) + 4 * SLOT,
    )
    inputs = scenario(
        prices=[10.0] * 4 + [30.0] * 4, pv=[0.0] * 8, load=[500.0] * 8, vehicles=[vehicle]
    )
    plan = plan_once(inputs, solve(inputs))
    assert plan.source == "optimizer", plan.issues
    assert all(s.ev_w == {} for s in plan.slots[:4])
    assert sum(w for s in plan.slots for w in s.ev_w.values()) * SLOT_H / 1000 == pytest.approx(5.0)
    assert [(v.key, v.expected) for v in plan.vehicles] == [("tesla", True)]


def test_fallback_ignores_expected_cars() -> None:
    # Ruling T10-1: Der Ersatzplan bleibt ohne erwartete Autos (Spec 9.4); das angesteckte lädt
    golf = planned_car(
        key="egolf",
        name="e-Golf",
        wallbox="evcs",
        departure_at=at(SCENARIO_START) + 4 * SLOT,
        need_kwh=2.76,
    )
    tesla = planned_car(
        departure_at=at(SCENARIO_START) + 4 * SLOT,
        need_kwh=2.76,
        expected=True,
        arrival_at=at(SCENARIO_START) + SLOT,
    )
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, vehicles=[golf, tesla])
    failed = OptimizerResult(
        status="error", flows=(), peaks_kw={}, objective_ct=None, solve_ms=0, message="kaputt"
    )
    plan = plan_once(inputs, failed)
    assert plan.source == "fallback"
    assert [v.key for v in plan.vehicles] == ["egolf"]
    assert all("tesla" not in s.ev_w for s in plan.slots)
    assert sum(s.ev_w.get("egolf", 0.0) for s in plan.slots) * SLOT_H / 1000 == pytest.approx(2.76)
    codes = {r.code for s in plan.slots for r in s.reasons}
    assert "AUTO_ERWARTET" not in codes
    # Nur das erwartete Auto: der Ersatzplan rechnet wie ohne Autos
    alone = plan_once(inputs.model_copy(update={"vehicles": [tesla]}), failed)
    assert alone.vehicles == [] and all(s.ev_w == {} for s in alone.slots)


# --- Vorheizen und Vorkühlen (Plan 5c) ------------------------------------------------------


def test_plausibility_flags_steps_outside_band() -> None:
    from tests.plan_helpers import sample_plan

    from bindaems.app.planning.plausibility import check_thermal

    plan = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15", "10:30"])

    def slots(*steps: int | None) -> list[Any]:
        return [
            slot.model_copy(update={"thermal_steps": s})
            for slot, s in zip(plan.slots, steps, strict=True)
        ]

    # Band +1,5 / −1,0 K: Stufen −2 bis 3
    assert check_thermal(slots(3, -2, None), up_k=1.5, down_k=1.0) == []
    assert check_thermal(slots(None, None, None), up_k=0.0, down_k=0.0) == []
    assert check_thermal(slots(1, 4, 5), up_k=1.5, down_k=1.0) == [
        "Versatz der Klimaanlage außerhalb des Bands um 12:15"
    ]
    assert check_thermal(slots(0, 0, -3), up_k=1.5, down_k=1.0) == [
        "Versatz der Klimaanlage außerhalb des Bands um 12:30"
    ]
    # nie über ±6 Stufen, auch mit breiterem Band
    assert check_thermal(slots(7, 0, 0), up_k=5.0, down_k=5.0) != []
    assert check_thermal(slots(-7, 0, 0), up_k=5.0, down_k=5.0) != []
    assert check_thermal(slots(6, -6, 0), up_k=5.0, down_k=5.0) == []


# Akku ins Auto (Plan Akku ins Auto, wenn er übrig ist)

EIGEN = BatteryIntent()
HALTEN = BatteryIntent(kind=BatteryIntentKind.HALTEN)


def _battery_case(
    start: str,
    flows: list[SlotFlows],
    intents: list[BatteryIntent],
    *,
    prices: list[float] | None = None,
    pv: list[float] | None = None,
    car_battery: bool = True,
    grid_charge: bool = False,
    battery: Any = None,
) -> tuple[PlanInputs, OptimizerResult, list[BatteryIntent], list[SimStep]]:
    """Eingaben, Flüsse, Absichten und ihre Nachrechnung mit dem Akkuanteil der Flüsse; Slots
    ab ``start`` (UTC) mit 500 W Haus, SOC 80 %, ein Auto bis nach dem Horizont. ``soc_end_kwh``
    der Flüsse zählt wie in ``_check`` ab dem SOC des Laufs weiter."""
    count = len(flows)
    vehicle = planned_car(departure_at=at(start) + 8 * SLOT)
    inputs = scenario(
        prices=prices or [20.0] * count,
        pv=pv or [0.0] * count,
        load=[500.0] * count,
        soc_pct=80.0,
        vehicles=[vehicle],
        car_battery=car_battery,
        grid_charge=grid_charge,
        start=start,
        battery=battery,
    )
    soc_kwh = inputs.battery.soc_pct / 100 * inputs.battery.usable_kwh
    absolute: list[SlotFlows] = []
    for item in flows:
        soc_kwh += item.soc_end_kwh
        absolute.append(replace(item, soc_end_kwh=soc_kwh))
    result = flows_result(absolute)
    ev_w = [
        {k: pv + gr + f.ev_battery.get(k, 0.0) for k, (pv, gr) in f.ev.items()} for f in absolute
    ]
    ev_battery_w = [dict(f.ev_battery) for f in absolute]
    steps = simulate(
        soc_kwh - sum(f.soc_end_kwh for f in flows),
        sim_slots(inputs, ev_w, ev_battery_w),
        intents,
        params_of(inputs),
    )
    return inputs, result, intents, steps


def _car_from_battery(**flows: float) -> SlotFlows:
    """Das Auto lädt 4 kW, 3 kW davon aus dem Akku; der Akku deckt auch das Haus."""
    values = {"bt_h": 500.0, **flows}
    return flow(ev={"tesla": (0.0, 1000.0)}, ev_battery={"tesla": 3000.0}, **values)


def _case(case: str) -> tuple[PlanInputs, OptimizerResult, list[BatteryIntent], list[SimStep]]:
    """Ein Slot um 22:00 Ortszeit (bzw. 12:00 mit PV), in dem das Auto aus dem Akku lädt."""
    late = "2026-10-16T20:00:00+00:00"  # 22:00 Ortszeit
    if case == "clean":
        return _battery_case(late, [_car_from_battery()], [EIGEN])
    if case == "switch_off":
        return _battery_case(late, [_car_from_battery()], [EIGEN], car_battery=False)
    if case == "hold":
        return _battery_case(late, [_car_from_battery()], [HALTEN])
    if case == "grid_in_battery":
        # Netzladen um 21:45, um 22:00 ist der Akku noch nicht leer
        return _battery_case(
            "2026-10-16T19:45:00+00:00",
            [flow(gr_h=500, gr_b=2000), _car_from_battery()],
            [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000), EIGEN],
            prices=[10.0, 30.0],
            grid_charge=True,
        )
    if case == "exporting":
        exporting = flow(
            pv_h=500, pv_x=7500, ev={"tesla": (0.0, 1000.0)}, ev_battery={"tesla": 3000.0}
        )
        return _battery_case("2026-10-16T10:00:00+00:00", [exporting], [EIGEN], pv=[8000.0])
    raise ValueError(case)


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("switch_off", "Akku ins Auto um 22:00, obwohl ausgeschaltet"),
        ("hold", "Akku ins Auto um 22:00 bei Halten oder Netzladen"),
        ("grid_in_battery", "Akku ins Auto um 22:00, solange Netzstrom im Akku ist"),
    ],
)
def test_check_plan_reports_wrong_car_battery(case: str, text: str) -> None:
    assert text in check_plan(*_case(case))


def test_car_battery_while_exporting_is_implausible() -> None:
    assert "Akku ins Auto um 12:00, während eingespeist wird" in check_plan(*_case("exporting"))


def test_car_battery_in_eigenverbrauch_with_the_switch_is_plausible() -> None:
    assert check_plan(*_case("clean")) == []


def test_car_limits_and_discharge_limit_count_the_battery_share() -> None:
    # 1 kW Netz plus 3 kW Akku liegt in den Grenzen des Autos (3450 bis 11040 W)
    assert check_plan(*_case("clean")) == []
    # Haus 500 W plus Auto 5 kW aus dem Akku liegt über der Entladegrenze (5 kW)
    over = flow(bt_h=500, ev={"tesla": (0.0, 0.0)}, ev_battery={"tesla": 5000.0})
    findings = check_plan(
        *_battery_case("2026-10-16T20:00:00+00:00", [over], [EIGEN], battery=small_battery())
    )
    assert findings == ["Leistung über der Grenze um 22:00"]


def test_check_plan_checks_the_car_battery_it_is_given() -> None:
    # Fix-Welle F1: Die Prüfungen „Akku ins Auto“ lesen den Akkuanteil, den der Plan behält; die
    # Leistungsprüfungen bleiben bei den Flüssen.
    inputs, result, intents, steps = _case("hold")
    assert check_plan(inputs, result, intents, steps, ev_battery_w=[{}]) == []
    found = check_plan(inputs, result, intents, steps, ev_battery_w=[{"tesla": 3000.0}])
    assert found == ["Akku ins Auto um 22:00 bei Halten oder Netzladen"]


def test_grid_in_battery_from_before_the_horizon_lasts_until_empty() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[10000.0] * 4, soc_pct=40.0)
    steps = resimulate(inputs, [BatteryIntent()] * 4)  # 12 kWh, Reserve 6 kWh: leer in Slot 2
    lo = params_of(inputs).lo_kwh
    assert grid_in_battery([BatteryIntent()] * 4, steps, lo) == [False] * 4
    assert grid_in_battery([BatteryIntent()] * 4, steps, lo, at_start=True) == [
        True,
        True,
        True,
        False,
    ]


def test_check_plan_counts_grid_energy_from_before_the_horizon() -> None:
    inputs, result, intents, steps = _case("clean")
    carried = inputs.model_copy(update={"grid_in_battery_at_start": True})
    assert "Akku ins Auto um 22:00, solange Netzstrom im Akku ist" in check_plan(
        carried, result, intents, steps
    )


# --- Netzenergie von vor dem Horizont (Fix-Welle F2) ---------------------------------------------

_NETZLADEN = {"intent": BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000.0)}


def _now_inputs(soc_pct: float) -> PlanInputs:
    """Eingangsdaten um 02:01 (UTC) mit diesem SOC; Standardakku 30 kWh, Reserve 20 % (6 kWh)."""
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, soc_pct=soc_pct)
    return inputs.model_copy(update={"created_at": at("2026-10-16T02:01:00+00:00")})


def _previous(created: str, slots: list[str], grid_charge_at: str | None) -> Plan:
    changes = [_NETZLADEN if start == grid_charge_at else {} for start in slots]
    return sample_plan(created=created, slots=slots, intents=changes)


def test_grid_in_battery_at_start_after_a_grid_charge_of_the_previous_plan() -> None:
    previous = _previous("2026-10-16T01:46:00+00:00", ["01:45", "02:00", "02:15"], "01:45")
    assert grid_in_battery_at_start(_now_inputs(50.0), previous, carried=False)
    # Der Slot, in dem der neue Plan beginnt, lief schon eine Minute nach dem vorigen Plan
    running = _previous("2026-10-16T01:46:00+00:00", ["01:45", "02:00", "02:15"], "02:00")
    assert grid_in_battery_at_start(_now_inputs(50.0), running, carried=False)


def test_no_grid_in_battery_at_start_without_a_started_grid_charge() -> None:
    later = _previous("2026-10-16T01:46:00+00:00", ["01:45", "02:00", "02:15"], "02:15")
    assert not grid_in_battery_at_start(_now_inputs(50.0), later, carried=False)
    assert not grid_in_battery_at_start(_now_inputs(50.0), None, carried=False)
    # Nach ``valid_until`` des vorigen Plans (45 min) führt der core seine Slots nicht mehr aus
    stale = _previous(
        "2026-10-16T00:46:00+00:00",
        ["00:45", "01:00", "01:15", "01:30", "01:45", "02:00"],
        "01:45",
    )
    assert not grid_in_battery_at_start(_now_inputs(50.0), stale, carried=False)


def test_grid_in_battery_at_start_is_carried_until_the_battery_is_empty() -> None:
    previous = _previous("2026-10-16T01:46:00+00:00", ["01:45", "02:00"], None)
    assert grid_in_battery_at_start(_now_inputs(50.0), previous, carried=True)
    # Leer: SOC höchstens Reserve + ``EMPTY_TOL_KWH`` (6,1 kWh von 30 kWh)
    empty_pct = (6.0 + EMPTY_TOL_KWH) / 30.0 * 100
    assert not grid_in_battery_at_start(_now_inputs(empty_pct), previous, carried=True)
    assert grid_in_battery_at_start(_now_inputs(empty_pct + 0.1), previous, carried=True)
    grid_charged = _previous("2026-10-16T01:46:00+00:00", ["01:45", "02:00"], "01:45")
    assert not grid_in_battery_at_start(_now_inputs(15.0), grid_charged, carried=True)
