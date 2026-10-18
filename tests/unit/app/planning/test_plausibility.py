"""Tests der Plausibilitätsprüfung (Präzisierung 9): Bilanz, Grenzen, Schalter, Regel R1 und
Spread-Regel an der Nachrechnung."""

from __future__ import annotations

import pytest
from tests.plan_helpers import (
    flow,
    flows_of,
    flows_result,
    month,
    pv_arc,
    resimulate,
    scenario,
    small_battery,
)

from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import SlotFlows, solve
from bindaems.app.planning.plausibility import (
    check_plan,
    export_after_kwh,
    pays_off,
    spread_pays,
)
from bindaems.app.planning.service import plan_once
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind


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
    assert any(i.startswith("Regel R verletzt: Netzladen um 02:00, danach ") for i in issues)


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


def test_export_after_includes_the_slot() -> None:
    inputs = scenario(
        prices=[5.0, 8.0],
        pv=[4000.0, 4000.0],
        load=[0.0, 0.0],
        battery=small_battery(),
        soc_pct=100.0,
    )
    steps = resimulate(inputs, [BatteryIntent(), BatteryIntent()])
    assert export_after_kwh(steps, 0) == 2.0
    assert export_after_kwh(steps, 1) == 1.0


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
