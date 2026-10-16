"""Tests der Plausibilitätsprüfung (Präzisierung 9): Bilanz, Grenzen, Schalter, Regel R1 und
Spread-Regel an der Nachrechnung."""

from __future__ import annotations

from tests.plan_helpers import (
    flow,
    flows_of,
    flows_result,
    month,
    resimulate,
    scenario,
    small_battery,
)

from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import SlotFlows, solve
from bindaems.app.planning.plausibility import check_plan, export_after_kwh, pays_off
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
        inputs, flows_of(steps), intents, steps
    )
    with_peak = inputs.model_copy(update={"peak_months": [month("2026-10", 2.0)]})
    assert "Netzladen um 02:00 rechnet sich nicht" not in check_plan(
        with_peak, flows_of(steps), intents, steps
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
