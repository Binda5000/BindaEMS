"""Tests des Plans aus Absichten (Task 7): Nachrechnung, Budget nach Präzisierung 10/11, Gründe."""

from __future__ import annotations

import pytest
from tests.plan_helpers import month, resimulate, sample_slot, scenario, small_battery

from bindaems.app.planning.builder import baseline_cost_ct, build_plan, support_thresholds
from bindaems.app.planning.explain import slot_text
from bindaems.shared.plan import PLAN_VALID, BatteryIntent, BatteryIntentKind, PlanSlot
from bindaems.shared.timeutil import SLOT

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


def test_baseline_differs_from_the_plan_when_the_battery_has_energy() -> None:
    # SOC 60 % von 30 kWh: 12 kWh über der Reserve decken alle 8 Slots („Victron allein“ kostet
    # nichts). Der Plan hält in den billigen Slots und bezieht dort 4 · 0,5 kWh zu 10 ct.
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=60.0)
    plan = build_plan(inputs, [HOLD] * 4 + [EV] * 4, source="optimizer", solver_status="optimal")
    assert plan.baseline_cost_ct == pytest.approx(0.0)
    assert plan.expected_cost_ct == pytest.approx(4 * 0.5 * 10.0)


def test_cost_counts_feed_in_revenue() -> None:
    # Voller Akku: 3 kW PV-Überschuss gehen je 15 min ins Netz (0,75 kWh zu 5 ct = 3,75 ct
    # Erlös); im dritten Slot hält der Plan und bezieht 0,5 kWh zu 20 ct = 10 ct.
    inputs = scenario(
        prices=[20.0] * 3,
        pv=[4000.0, 4000.0, 0.0],
        load=[1000.0, 1000.0, 2000.0],
        soc_pct=100.0,
        feed_in=5.0,
    )
    plan = build_plan(inputs, [EV, EV, HOLD], source="optimizer", solver_status="optimal")
    assert [s.export_w for s in plan.slots] == [3000.0, 3000.0, 0.0]
    assert [s.cost_ct for s in plan.slots] == pytest.approx([-3.75, -3.75, 10.0])
    assert plan.expected_cost_ct == pytest.approx(2.5)
    assert plan.baseline_cost_ct == pytest.approx(-7.5)


# --- Plan-Schwelle der Akku-Unterstützung (Präzisierung 6) ----------------------------------


def slots_from(soc_pct: float, drops: list[float]) -> list[PlanSlot]:
    """Slots ab 19:00 UTC; ``drops`` sind die Entladungen je Slot in Prozentpunkten (negativ:
    Laden). Der SOC am Ende eines Slots ist der am Anfang des nächsten."""
    result: list[PlanSlot] = []
    for index, drop in enumerate(drops):
        slot = sample_slot(f"{19 + index // 4:02d}:{index % 4 * 15:02d}")
        result.append(
            slot.model_copy(update={"soc_start_pct": soc_pct, "soc_end_pct": soc_pct - drop})
        )
        soc_pct -= drop
    return result


def test_thresholds_reserve_the_evening_discharge() -> None:
    # SOC 60 → 50 → 40 → 30 → 20 (je 10 Punkte Entladung), Reserve 20
    found = support_thresholds(slots_from(60, [10, 10, 10, 10]), 20.0)
    assert [t for t, _ in found] == [60.0, 50.0, 40.0, 30.0]


def test_thresholds_name_the_horizon_end_while_the_need_lasts() -> None:
    slots = slots_from(60, [10, 10, 10, 10])
    assert {um for _, um in support_thresholds(slots, 20.0)} == {slots[-1].start + SLOT}


def test_pv_refill_releases_the_threshold() -> None:
    # Entladung 10, dann Laden 30: R_1 = max(0, -30) = 0, R_0 = 10. Slot 0 braucht seine eigenen
    # 10 Punkte, Slot 1 vor dem Laden nichts mehr; „bis“ ist der Beginn von Slot 1.
    slots = slots_from(50, [10, -30])
    found = support_thresholds(slots, 20.0)
    assert [t for t, _ in found] == [30.0, 20.0]
    assert [um for _, um in found] == [slots[1].start, slots[1].start + SLOT]


def test_threshold_is_rounded_up_and_capped() -> None:
    assert [t for t, _ in support_thresholds(slots_from(100, [7.5, 7.5]), 20.0)] == [35.0, 28.0]
    assert [t for t, _ in support_thresholds(slots_from(100, [50, 50]), 20.0)] == [100.0, 70.0]
    # Gleitkommafehler über einer ganzen Zahl heben die Schwelle nicht an
    assert support_thresholds(slots_from(60, [10.0000000001]), 20.0)[0][0] == 30.0


def test_thresholds_of_an_empty_plan() -> None:
    assert support_thresholds([], 20.0) == []


def evening(**overrides):
    """8 Slots bei 3,5 kW Last ohne PV aus 90 % (rund 9,3 Punkte Entladung je Slot bei 10 kWh)."""
    values = dict(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[3500.0] * 8,
        soc_pct=90.0,
        battery=small_battery(),
    )
    return scenario(**{**values, **overrides})


def test_slots_carry_the_threshold() -> None:
    plan = build_plan(evening(), [EV] * 8, source="optimizer", solver_status="optimal")
    # Der Akku (10 kWh, Reserve 20 %) liefert mit Wirkungsgrad rund 9,3 Punkte je Slot; der Bedarf
    # des ersten Slots ist alles über der Reserve, der des letzten nur noch der Rest.
    assert [s.support_soc_pct for s in plan.slots] == [90, 81, 72, 63, 53, 44, 35, 25]
    assert all(0 <= s.support_soc_pct <= 100 for s in plan.slots)


def test_reserved_code_only_above_the_configured_threshold() -> None:
    plan = build_plan(
        evening(support_soc_pct=70.0), [EV] * 8, source="optimizer", solver_status="optimal"
    )
    codes = [[r.code for r in s.reasons] for s in plan.slots]
    assert codes[0] == ["EIGENVERBRAUCH", "UNTERSTUETZUNG_RESERVIERT"]
    assert "UNTERSTUETZUNG_RESERVIERT" in codes[2] and "UNTERSTUETZUNG_RESERVIERT" not in codes[3]
    assert "UNTERSTUETZUNG_RESERVIERT" not in codes[-1]
    reserved = plan.slots[0].reasons[1]
    assert reserved.params == {"soc": 90.0, "um": (plan.slots[-1].start + SLOT).isoformat()}


def test_a_higher_configured_threshold_silences_the_code() -> None:
    plan = build_plan(
        evening(support_soc_pct=90.0), [EV] * 8, source="optimizer", solver_status="optimal"
    )
    assert all(r.code != "UNTERSTUETZUNG_RESERVIERT" for s in plan.slots for r in s.reasons)


def test_reserved_code_stands_before_estimated() -> None:
    inputs = evening(origins=["estimated"] * 8)
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert [r.code for r in plan.slots[0].reasons] == [
        "EIGENVERBRAUCH",
        "UNTERSTUETZUNG_RESERVIERT",
        "GESCHAETZT",
    ]


def test_no_reserved_code_in_hold_slots() -> None:
    plan = build_plan(
        evening(load=[0.0, 0.0] + [3500.0] * 6),
        [HOLD] * 2 + [EV] * 6,
        source="optimizer",
        solver_status="optimal",
    )
    held = [[r.code for r in s.reasons] for s in plan.slots[:2]]
    assert all("UNTERSTUETZUNG_RESERVIERT" not in codes for codes in held)
    assert plan.slots[0].support_soc_pct is not None and plan.slots[0].support_soc_pct > 70.0
