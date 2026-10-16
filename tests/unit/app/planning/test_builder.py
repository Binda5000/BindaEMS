"""Tests des Plans aus Absichten (Task 7): Nachrechnung, Budget nach Präzisierung 10/11, Gründe."""

from __future__ import annotations

import pytest
from tests.plan_helpers import month, resimulate, scenario

from bindaems.app.planning.builder import baseline_cost_ct, build_plan
from bindaems.app.planning.explain import slot_text
from bindaems.shared.plan import PLAN_VALID, BatteryIntent, BatteryIntentKind

EV = BatteryIntent()
HOLD = BatteryIntent(kind=BatteryIntentKind.HALTEN)


def test_halten_names_the_expensive_block() -> None:
    inputs = scenario(
        prices=[20.0] * 4 + [38.0] * 8, pv=[0.0] * 12, load=[1000.0] * 12, soc_pct=50.0
    )
    plan = build_plan(inputs, [HOLD] * 4 + [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.slots[0].reasons[0].code == "HALTEN_FUER_SPITZE"
    assert slot_text(plan.slots[0]).startswith("Akku hält 50 %, weil 03:00–05:00 Uhr 38,0 ct")


def test_estimated_price_and_fallback_add_codes() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[0.0] * 4,
        load=[500.0] * 4,
        origins=["primary"] * 3 + ["estimated"],
    )
    plan = build_plan(
        inputs,
        [EV] * 4,
        source="fallback",
        solver_status="timeout",
        fallback_reason="Solver nach 30 s abgebrochen",
    )
    assert [r.code for r in plan.slots[3].reasons] == ["ERSATZPLAN", "EIGENVERBRAUCH", "GESCHAETZT"]
    assert plan.slots[3].estimated and slot_text(plan.slots[3]).endswith("Preis geschätzt.")
    assert not plan.slots[0].estimated
    assert plan.fallback_reason == "Solver nach 30 s abgebrochen"
    assert plan.solver_status == "timeout"


def test_switched_off_grid_charge_is_explained() -> None:
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0)
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.slots[0].reasons[0].code == "NETZLADEN_AUS"


def test_build_plan_resimulates_and_sets_budget() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[0.0] * 4,
        load=[3000.0, 3000.0, 4210.0, 3000.0],
        soc_pct=20.0,
        peak_months=[month("2026-10", 2.0)],
    )
    plan = build_plan(
        inputs,
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        optimizer_peaks_kw={"2026-10": 3.5},
    )
    assert [s.budget_kw for s in plan.slots] == [4.3] * 4  # ceil(4,21 · 10) / 10
    assert plan.slots[2].import_w == 4210 and plan.power_price


def test_budget_follows_the_optimizer_peak_when_it_is_higher() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[0.0] * 4,
        load=[1000.0] * 4,
        soc_pct=20.0,
        peak_months=[month("2026-10", 2.0)],
    )
    plan = build_plan(
        inputs,
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        optimizer_peaks_kw={"2026-10": 3.41},
    )
    assert {s.budget_kw for s in plan.slots} == {3.5}


def test_exact_tenth_is_not_rounded_up() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[0.0] * 4,
        load=[4200.0] * 4,
        soc_pct=20.0,
        peak_months=[month("2026-10", 2.0)],
    )
    plan = build_plan(inputs, [EV] * 4, source="optimizer", solver_status="optimal")
    assert {s.budget_kw for s in plan.slots} == {4.2}


def test_budget_is_empty_without_power_price() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4)
    plan = build_plan(inputs, [EV] * 4, source="optimizer", solver_status="optimal")
    assert [s.budget_kw for s in plan.slots] == [None] * 4
    assert not plan.power_price


def test_budget_is_per_local_month() -> None:
    # 2026-10-31 22:00 UTC = 23:00 lokal; ab Slot 4 (23:00 UTC = 00:00 lokal) gilt November.
    inputs = scenario(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[1000.0] * 4 + [2500.0] * 4,
        soc_pct=20.0,
        start="2026-10-31T22:00:00+00:00",
        peak_months=[month("2026-10", 2.0), month("2026-11", 2.0)],
    )
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert [s.budget_kw for s in plan.slots] == [2.0] * 4 + [2.5] * 4


def test_fallback_budget_is_previous_peak() -> None:
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0] * 2,
        load=[5000.0] * 2,
        peak_months=[month("2026-10", 3.2)],
    )
    plan = build_plan(
        inputs, [EV] * 2, source="fallback", solver_status="error", fallback_reason="x"
    )
    assert [s.budget_kw for s in plan.slots] == [3.2, 3.2]


def test_baseline_cost_is_eigenverbrauch_everywhere() -> None:
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0)
    plan = build_plan(inputs, [HOLD] * 8, source="optimizer", solver_status="optimal")
    assert plan.baseline_cost_ct == pytest.approx(baseline_cost_ct(inputs))
    assert plan.baseline_cost_ct == pytest.approx(0.25 * 2 * 4 * (10 + 40))


def test_expected_cost_is_the_sum_of_slot_costs() -> None:
    inputs = scenario(
        prices=[10.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[2000.0] * 8,
        soc_pct=60.0,
        feed_in=5.0,
    )
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.expected_cost_ct == pytest.approx(sum(s.cost_ct for s in plan.slots))
    steps = resimulate(inputs, [EV] * 8)
    assert plan.expected_cost_ct == pytest.approx(sum(s.cost_ct for s in steps))


def test_header_and_slot_fields_come_from_the_inputs() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[1000.0] * 4,
        load=[500.0] * 4,
        soc_pct=40.0,
        grid_charge=True,
        feed_in=6.0,
    ).model_copy(update={"month_peak_kw": 3.1, "settings_version": 7, "trigger": "manual"})
    plan = build_plan(
        inputs,
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        issues=["Hinweis"],
    )
    assert plan.created_at == inputs.created_at
    assert plan.valid_until == inputs.created_at + PLAN_VALID
    assert plan.trigger == "manual" and plan.settings_version == 7
    assert plan.grid_charge_allowed is True
    assert plan.reserve_pct == inputs.battery.reserve_pct
    assert plan.soc_max_pct == inputs.battery.soc_max_pct
    assert plan.month_peak_kw == 3.1
    assert plan.issues == ["Hinweis"]
    assert plan.source == "optimizer" and plan.fallback_reason is None
    first = plan.slots[0]
    assert first.start == inputs.slots[0].start
    assert first.pv_w == 1000.0 and first.load_w == 500.0
    assert first.import_ct == 20.0 and first.feed_in_ct == 6.0
    assert first.soc_start_pct == pytest.approx(40.0)
    assert first.soc_end_pct > first.soc_start_pct  # PV-Überschuss lädt
    assert first.charge_pv_w == pytest.approx(500.0)


def test_intent_count_must_match() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4)
    with pytest.raises(ValueError):
        build_plan(inputs, [EV] * 3, source="optimizer", solver_status="optimal")
