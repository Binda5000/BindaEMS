"""Tests des Plans aus Absichten (Task 7): Nachrechnung, Budget nach Präzisierung 10/11, Gründe."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from tests.plan_helpers import (
    NOW,
    SCENARIO_START,
    STORM_WARNING,
    assemble,
    at,
    flat,
    month,
    planned_car,
    resimulate,
    sample_slot,
    scenario,
    small_battery,
    state,
    storm_scenario,
    virtual,
)

from bindaems.app.planning.battery_model import SLOT_H
from bindaems.app.planning.builder import baseline_cost_ct, build_plan, support_thresholds
from bindaems.app.planning.explain import slot_text
from bindaems.shared.plan import PLAN_VALID, BatteryIntent, BatteryIntentKind, Plan, PlanSlot
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import SLOT, slot_start

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


def test_virtual_month_gives_a_budget() -> None:
    # Entscheidung 8: ohne Leistungspreis gibt der gedachte Satz sein Budget an den Wächter
    load = flat(NOW, 200, 500.0)
    load[slot_start(NOW) + 3 * SLOT] = 3210.0
    battery = state(soc_pct=20.0)  # an der Reserve: das Haus bezieht aus dem Netz
    inputs = assemble(load=load, battery=battery, month_peak_kw=2.0)
    assert [m.virtual for m in inputs.peak_months] == [True]
    intents = [EV] * len(inputs.slots)
    plan = build_plan(inputs, intents, source="optimizer", solver_status="optimal")
    assert max(s.import_w for s in plan.slots) == 3210
    assert {s.budget_kw for s in plan.slots} == {3.3}  # ceil(max(2,0, 3,21) · 10) / 10
    assert plan.power_price is False
    # ohne Bezug über der bisherigen Spitze bleibt sie das Budget
    calm = assemble(battery=battery, month_peak_kw=2.0)
    plan = build_plan(calm, intents, source="optimizer", solver_status="optimal")
    assert {s.budget_kw for s in plan.slots} == {2.0}
    # Satz 0 schaltet den gedachten Monat ab: kein Budget
    off = assemble(
        load=load,
        battery=battery,
        month_peak_kw=2.0,
        planning=PlanningSettings(peak_soft_ct_per_kw=0.0),
    )
    plan = build_plan(off, intents, source="optimizer", solver_status="optimal")
    assert {s.budget_kw for s in plan.slots} == {None}
    assert plan.power_price is False


def test_real_month_sets_power_price_next_to_a_virtual_one() -> None:
    inputs = scenario(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[1000.0] * 8,
        soc_pct=20.0,
        start="2026-12-31T22:00:00+00:00",
        peak_months=[virtual("2026-12", 2.0), month("2027-01", 2.0)],
    )
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.power_price is True
    assert [s.budget_kw for s in plan.slots] == [2.0] * 8
    only_virtual = inputs.model_copy(update={"peak_months": [virtual("2026-12", 2.0)]})
    plan = build_plan(only_virtual, [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.power_price is False
    assert [s.budget_kw for s in plan.slots] == [2.0] * 4 + [None] * 4


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


def test_fallback_budget_covers_its_own_import() -> None:
    # Gesamtreview I2: Monatsbeginn, minimum_kw 0, noch kein Bezug. HALTEN holt die Hauslast aus
    # dem Netz; ein Budget von 0 kW striche den Override des core auf 0 W.
    inputs = scenario(
        prices=[20.0] * 2, pv=[0.0] * 2, load=[1000.0] * 2, peak_months=[month("2026-10", 0.0)]
    )
    plan = build_plan(
        inputs, [HOLD] * 2, source="fallback", solver_status="error", fallback_reason="x"
    )
    assert [s.import_w for s in plan.slots] == [1000.0, 1000.0]
    assert [s.budget_kw for s in plan.slots] == [1.0, 1.0]


def test_fallback_budget_rounds_its_import_up_to_a_tenth() -> None:
    inputs = scenario(
        prices=[20.0] * 2, pv=[0.0] * 2, load=[1234.0] * 2, peak_months=[month("2026-10", 1.0)]
    )
    plan = build_plan(
        inputs, [HOLD] * 2, source="fallback", solver_status="error", fallback_reason="x"
    )
    assert {s.budget_kw for s in plan.slots} == {1.3}


@pytest.mark.parametrize("source", ["optimizer", "fallback"])
def test_budget_without_peak_and_import_is_the_car_floor(source: str) -> None:
    # Monatsbeginn ohne Mindestwert und ohne Bezug im Plan: nicht 0 kW, sondern der kleinste
    # Ladestrom des größten Autos (darunter kann der Wächter nur drosseln und alarmieren)
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[2000.0] * 2,
        load=[500.0] * 2,
        peak_months=[month("2026-10", 0.0)],
        car_floor_kw=4.14,
    )
    plan = build_plan(
        inputs,
        [EV] * 2,
        source=source,  # type: ignore[arg-type]
        solver_status="optimal" if source == "optimizer" else "error",
        fallback_reason=None if source == "optimizer" else "x",
    )
    assert [s.import_w for s in plan.slots] == [0.0, 0.0]
    assert [s.budget_kw for s in plan.slots] == [4.2, 4.2]


def test_the_car_floor_is_only_a_floor() -> None:
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0] * 2,
        load=[1000.0] * 2,
        peak_months=[month("2026-10", 0.0)],
        car_floor_kw=4.14,
    )
    plan = build_plan(
        inputs, [HOLD] * 2, source="fallback", solver_status="error", fallback_reason="x"
    )
    assert {s.budget_kw for s in plan.slots} == {1.0}  # Bezug des Plans, kein Boden


@pytest.mark.parametrize("source", ["optimizer", "fallback"])
def test_peak_target_raises_the_budget(source: str) -> None:
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0] * 2,
        load=[1000.0] * 2,
        peak_months=[month("2026-10", 2.0)],
        peak_target_kw=5.04,
    )
    plan = build_plan(
        inputs,
        [HOLD] * 2,
        source=source,  # type: ignore[arg-type]
        solver_status="optimal" if source == "optimizer" else "error",
        fallback_reason=None if source == "optimizer" else "x",
        optimizer_peaks_kw={"2026-10": 3.0} if source == "optimizer" else None,
    )
    assert {s.budget_kw for s in plan.slots} == {5.1}


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
    # der Start-SOC des Plans ist der gemessene SOC, nicht das Ende des ersten Slots
    assert plan.soc_start_pct == inputs.battery.soc_pct == 40.0


def test_an_old_plan_without_the_start_soc_loads_with_none() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[500.0] * 2, soc_pct=40.0)
    plan = build_plan(inputs, [EV] * 2, source="optimizer", solver_status="optimal", issues=[])
    data = plan.model_dump(mode="json")
    del data["soc_start_pct"]
    assert Plan.model_validate(data).soc_start_pct is None


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


def test_support_threshold_rises_in_window() -> None:
    # Entscheidung 7: ohne Entladung danach ist die Schwelle im Fenster die Untergrenze; davor
    # braucht der Plan den Akku bis zum Fenster, danach gilt wieder die Reserve.
    slots = slots_from(60, [0, 0, 0, 0, 0, 0])
    floors = [None, None, 40.0, 40.0, None, None]
    found = support_thresholds(slots, 20.0, floors)
    assert [t for t, _ in found] == [40.0, 40.0, 40.0, 40.0, 20.0, 20.0]
    # „bis“ ist das Ende des Fensters
    assert {um for _, um in found[:4]} == {slots[4].start}
    # eine Entladung im Fenster kommt auf die Untergrenze danach
    found = support_thresholds(slots_from(60, [0, 0, 5, 5, 0, 0]), 20.0, floors)
    assert [t for t, _ in found] == [45.0, 45.0, 45.0, 40.0, 20.0, 20.0]


def test_support_threshold_without_floors_unchanged() -> None:
    for drops in ([10, 10, 10, 10], [10, -30], [7.5, 7.5], [50, 50], [10.0000000001]):
        slots = slots_from(60, drops)
        assert support_thresholds(slots, 20.0, [None] * len(drops)) == support_thresholds(
            slots, 20.0
        )
    with pytest.raises(ValueError):
        support_thresholds(slots_from(60, [10, 10]), 20.0, [None])


def test_plan_carries_storm() -> None:
    inputs = storm_scenario(soc_pct=45.0)
    plan = build_plan(inputs, [EV] * 96, source="fallback", solver_status=None)
    assert plan.storm is not None
    assert plan.storm.target_pct == 40.0
    assert plan.storm.warnings == [STORM_WARNING]
    assert [s.storm_reserve_pct for s in plan.slots] == [s.storm_floor_pct for s in inputs.slots]
    # die Plan-Schwelle hebt sich im Fenster
    assert plan.slots[50].support_soc_pct is not None and plan.slots[50].support_soc_pct >= 40.0
    # ohne Warnung kein Block und keine Untergrenze
    bare = build_plan(
        storm_scenario(soc_pct=45.0, warning=None), [EV] * 96, source="fallback", solver_status=None
    )
    assert bare.storm is None
    assert all(s.storm_reserve_pct is None for s in bare.slots)


def test_plan_carries_storm_without_a_floor_in_the_horizon() -> None:
    # Ruling R12: Eine bekannte Warnung nach dem Horizont zeigt der Plan trotzdem (ohne Slot mit
    # Untergrenze ist das Ziel das der Einstellung)
    later = STORM_WARNING.model_copy(
        update={
            "begin": STORM_WARNING.begin + timedelta(days=3),
            "end": STORM_WARNING.end + timedelta(days=3),
        }
    )
    inputs = storm_scenario(soc_pct=45.0, warning=later)
    assert all(s.storm_floor_pct is None for s in inputs.slots)
    plan = build_plan(inputs, [EV] * 96, source="fallback", solver_status=None)
    assert plan.storm is not None
    assert plan.storm.target_pct == 40.0
    assert plan.storm.warnings == [later]


def test_an_old_plan_without_storm_loads_with_none() -> None:
    plan = build_plan(
        storm_scenario(soc_pct=45.0), [EV] * 96, source="fallback", solver_status=None
    )
    data = plan.model_dump(mode="json")
    del data["storm"]
    for slot in data["slots"]:
        del slot["storm_reserve_pct"]
    old = Plan.model_validate(data)
    assert old.storm is None
    assert all(s.storm_reserve_pct is None for s in old.slots)


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


# Geplante Autos (Plan 4b, Präzisierung 14)


def _car_plan(
    ev_w: list[dict[str, float]],
    *,
    shortfall: dict[str, float] | None = None,
    departure: int = 6,
    **car: Any,
) -> Plan:
    """Plan des Optimierers über acht Slots ohne PV (500 W Haus) mit einem Auto, das nach
    ``departure`` Slots abfährt, und der Ladeleistung ``ev_w`` je Slot."""
    vehicle = planned_car(departure_at=at(SCENARIO_START) + departure * SLOT, **car)
    inputs = scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, vehicles=[vehicle])
    return build_plan(
        inputs,
        [EV] * 8,
        source="optimizer",
        solver_status="optimal",
        ev_w=ev_w,
        shortfall_kwh=shortfall,
    )


def test_plan_summary_per_car() -> None:
    # 76 % → 80 % mit 75 kWh und η 0,9: 3,33 kWh aus der Wallbox; der Plan lädt 3,0 kWh
    ev_w = [{}, {}, {"tesla": 6000.0}, {"tesla": 6000.0}, {}, {}, {}, {}]
    plan = _car_plan(ev_w, shortfall={"tesla": 1 / 3}, soc_pct=76.0, target_soc_pct=80.0)
    (car,) = plan.vehicles
    assert (car.key, car.name, car.wallbox) == ("tesla", "Tesla Model 3", "twc")
    assert car.departure_at == at(SCENARIO_START) + 6 * SLOT
    assert (car.soc_start_pct, car.soc_assumed, car.limit_pct) == (76.0, False, None)
    assert car.target_soc_pct == 80.0
    assert car.need_kwh == pytest.approx(10 / 3)
    assert car.planned_kwh == pytest.approx(3.0)  # Wallbox-Seite: Σ e · Δ
    assert car.shortfall_kwh == pytest.approx(1 / 3)
    assert car.expected_soc_pct == pytest.approx(76.0 + 3.0 * 0.9 / 75 * 100)  # 79,6 %
    assert car.finish_at == at(SCENARIO_START) + 4 * SLOT  # Ende des letzten Slots mit Laden
    assert not car.reachable
    assert [s.ev_w for s in plan.slots[1:4]] == [{}, {"tesla": 6000.0}, {"tesla": 6000.0}]
    # Das Auto lädt aus dem Netz, der Akku deckt nur das Haus
    assert (plan.slots[2].import_w, plan.slots[2].discharge_w) == (6000.0, 500.0)


def test_reached_car_is_summarised_as_reached() -> None:
    # Der core hält das Ziel für erreicht (49 % bei Ziel 50 %): kein Bedarf, kein Laden, kein
    # Warten auf PV und keine Abfahrtsziel-Gründe in den Slots
    reached = planned_car(
        soc_pct=49.0,
        target_soc_pct=50.0,
        reached=True,
        need_kwh=0.0,
        horizon_need_kwh=0.0,
        grid_allowed_kwh=0.0,
        departure_at=at(SCENARIO_START) + 6 * SLOT,
    )
    inputs = scenario(
        prices=[20.0] * 8,
        pv=[0.0, 0.0, 4000.0, 4000.0, 0.0, 0.0, 0.0, 0.0],
        load=[500.0] * 8,
        vehicles=[reached],
    )
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal", ev_w=[{}] * 8)
    (car,) = plan.vehicles
    assert car.reached is True
    assert car.reachable is True and car.finish_at is None
    assert (car.need_kwh, car.planned_kwh, car.shortfall_kwh) == (0.0, 0.0, 0.0)
    assert (car.soc_start_pct, car.target_soc_pct) == (49.0, 50.0)
    codes = {reason.code for slot in plan.slots for reason in slot.reasons}
    assert not codes & {"AUTO_WARTET_AUF_PV", "ZIEL_SOC_ABFAHRT"}


def test_reachable_allows_a_tenth_of_a_kwh_and_computes_a_missing_shortfall() -> None:
    ev_w = [{}, {}, {"tesla": 6000.0}, {"tesla": 6000.0}, {}, {}, {}, {}]
    close = _car_plan(ev_w, soc_pct=76.0, target_soc_pct=80.0, need_kwh=3.1)
    assert close.vehicles[0].shortfall_kwh == pytest.approx(0.1)  # aus dem Bedarf gerechnet
    assert close.vehicles[0].reachable
    far = _car_plan(ev_w, soc_pct=76.0, target_soc_pct=80.0, need_kwh=3.11)
    assert not far.vehicles[0].reachable


def test_expected_soc_stops_at_the_target_once_the_need_is_covered() -> None:
    # 79,9 % → 80 % sind 0,083 kWh; ein Slot mit Mindestleistung lädt 0,86 kWh
    ev_w = [{"tesla": 3450.0}, {}, {}, {}, {}, {}, {}, {}]
    plan = _car_plan(ev_w, shortfall={"tesla": 0.0}, soc_pct=79.9, target_soc_pct=80.0)
    car = plan.vehicles[0]
    assert car.planned_kwh == pytest.approx(3450 * SLOT_H / 1000)
    assert car.expected_soc_pct == 80.0
    assert car.reachable and car.finish_at == at(SCENARIO_START) + SLOT


def test_expected_soc_never_exceeds_100_and_without_charging_has_no_finish() -> None:
    idle = _car_plan([{}] * 8, shortfall={"tesla": 0.0}, soc_pct=85.0, target_soc_pct=80.0)
    car = idle.vehicles[0]
    assert (car.planned_kwh, car.expected_soc_pct, car.finish_at) == (0.0, 85.0, None)
    assert car.reachable
    full = _car_plan(
        [{"tesla": 11040.0}] * 6 + [{}, {}],
        shortfall={"tesla": 0.0},
        soc_pct=99.0,
        target_soc_pct=100.0,
        need_kwh=20.0,
    )
    assert full.vehicles[0].expected_soc_pct == 100.0


def test_departure_after_the_horizon_compares_with_the_horizon_need() -> None:
    # Bedarf 30 kWh, im Horizont nötig 2,76 kWh: ein voller Slot reicht; den Rest lädt der core
    # nach dem Horizont mit voller Leistung (8 h · 11,04 kW), das Ziel ist erreichbar
    ev_w = [{"tesla": 11040.0}, {}, {}, {}, {}, {}, {}, {}]
    plan = _car_plan(ev_w, departure=40, need_kwh=30.0, horizon_need_kwh=2.76)
    car = plan.vehicles[0]
    assert car.shortfall_kwh == pytest.approx(0.0, abs=1e-9) and car.reachable
    assert car.expected_soc_pct == pytest.approx(40.0 + 30.0 * 0.9 / 75 * 100)


def test_finish_at_is_none_when_the_departure_is_beyond_the_horizon_and_need_remains() -> None:
    # Abfahrt 3 h nach dem Horizontende, der Plan lädt nur einen Slot PV (Mindestleistung):
    # Der Rest des Bedarfs kommt erst nach dem Horizont, „Fertig um“ ist unbekannt (Ruling R20)
    ev_w = [{}, {}, {"tesla": 3450.0}, {}, {}, {}, {}, {}]
    plan = _car_plan(ev_w, departure=20, need_kwh=30.0, horizon_need_kwh=0.0)
    car = plan.vehicles[0]
    assert car.finish_at is None
    assert car.reachable and car.shortfall_kwh == pytest.approx(0.0, abs=1e-9)
    # deckt der Plan den Bedarf schon im Horizont, ist „Fertig um“ das Ende seines letzten Slots
    full = [{"tesla": 11040.0}] * 3 + [{}] * 5
    done = _car_plan(full, departure=20, need_kwh=8.0, horizon_need_kwh=0.0).vehicles[0]
    assert done.finish_at == at(SCENARIO_START) + 3 * SLOT


def test_expected_soc_beyond_the_horizon_counts_full_power_after_it() -> None:
    # Abfahrt 1 h nach dem Horizontende: nach dem Horizont lädt der core höchstens 11,04 kWh,
    # mit dem Slot im Plan (0,8625 kWh) also 11,9025 kWh bis zur Abfahrt (Ruling R24)
    ev_w = [{}, {}, {"tesla": 3450.0}, {}, {}, {}, {}, {}]
    short = _car_plan(ev_w, departure=12, need_kwh=30.0, horizon_need_kwh=0.0).vehicles[0]
    assert short.expected_soc_pct == pytest.approx(40.0 + 11.9025 * 0.9 / 75 * 100)
    # 3 h nach dem Horizont reichen für den ganzen Bedarf: erwartet ist das Ziel, nicht mehr
    ample = _car_plan(ev_w, departure=20, horizon_need_kwh=0.0).vehicles[0]
    assert ample.expected_soc_pct == pytest.approx(80.0)


def test_baseline_carries_the_same_cars() -> None:
    ev_w = [{"tesla": 6000.0}] + [{}] * 7
    plan = _car_plan(ev_w, shortfall={"tesla": 0.0})
    # Der Akku deckt das Haus in beiden; nur das Auto bezieht: 1,5 kWh zu 20 ct
    assert plan.expected_cost_ct == pytest.approx(30.0)
    assert plan.baseline_cost_ct == pytest.approx(30.0)


def test_power_below_one_watt_is_not_in_the_plan() -> None:
    plan = _car_plan([{"tesla": 0.4}] + [{}] * 7, shortfall={"tesla": 0.0})
    assert plan.slots[0].ev_w == {} and plan.vehicles[0].finish_at is None


# Akku ins Auto (Plan Akku ins Auto, wenn er übrig ist)


def _battery_inputs(*, car_battery: bool = True, **kwargs: Any) -> Any:
    """Vier Slots ohne PV (500 W Haus, 20 ct), SOC 80 %, ein Auto bis nach dem Horizont."""
    vehicle = planned_car(departure_at=at(SCENARIO_START) + 8 * SLOT)
    values: dict[str, Any] = {
        "prices": [20.0] * 4,
        "pv": [0.0] * 4,
        "load": [500.0] * 4,
        "soc_pct": 80.0,
        "vehicles": [vehicle],
        "car_battery": car_battery,
    }
    values.update(kwargs)
    return scenario(**values)


INPUTS = _battery_inputs()


def test_build_plan_fills_ev_battery_and_threshold_counts_it() -> None:
    plan = build_plan(
        INPUTS,
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        ev_w=[{"tesla": 4000}] * 4,
        ev_battery_w=[{"tesla": 3000}] * 4,
    )
    assert plan.slots[0].ev_battery_w == {"tesla": 3000}
    without = build_plan(
        INPUTS, [EV] * 4, source="optimizer", solver_status="optimal", ev_w=[{"tesla": 4000}] * 4
    )
    assert plan.slots[0].support_soc_pct > without.slots[0].support_soc_pct
    # Die Nachrechnung gibt dem Auto 3 kW aus dem Akku; nur 1 kW kommt aus dem Netz
    assert plan.slots[0].discharge_w == pytest.approx(3500.0)
    assert plan.slots[0].import_w == pytest.approx(1000.0)
    assert without.slots[0].ev_battery_w == {}


def test_baseline_cost_stays_without_the_car_battery() -> None:
    # Entscheidung 10: „Victron allein“ lädt die Autos aus PV und Netz
    common: dict[str, Any] = {"source": "optimizer", "solver_status": "optimal"}
    plan = build_plan(
        INPUTS, [EV] * 4, ev_w=[{"tesla": 4000}] * 4, ev_battery_w=[{"tesla": 3000}] * 4, **common
    )
    without = build_plan(INPUTS, [EV] * 4, ev_w=[{"tesla": 4000}] * 4, **common)
    assert plan.baseline_cost_ct == pytest.approx(without.baseline_cost_ct)
    assert plan.expected_cost_ct < without.expected_cost_ct


def test_now_battery_only_in_an_optimizer_plan_with_the_switch() -> None:
    # 20 ct − Verschleiß − Marge = 16 ct ≥ Restwert 0: übriger Akku in jedem Eigenverbrauch-Slot
    intents = [EV, EV, HOLD, EV]
    plan = build_plan(INPUTS, intents, source="optimizer", solver_status="optimal")
    assert [s.now_battery for s in plan.slots] == [True, True, False, True]
    fallback = build_plan(INPUTS, intents, source="fallback", solver_status="error")
    assert not any(s.now_battery for s in fallback.slots)
    off = build_plan(
        _battery_inputs(car_battery=False), intents, source="optimizer", solver_status="optimal"
    )
    assert not any(s.now_battery for s in off.slots)


def test_battery_share_below_one_watt_is_not_in_the_plan() -> None:
    plan = build_plan(
        INPUTS,
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        ev_w=[{"tesla": 4000}] * 4,
        ev_battery_w=[{"tesla": 0.4}] * 4,
    )
    assert plan.slots[0].ev_battery_w == {}


def test_budget_without_expected_cars_keeps_the_battery_share_of_the_plugged_car() -> None:
    # Ruling F-1: Das Budget rechnet ohne das erwartete Auto nach, aber mit dem Akkuanteil des
    # angesteckten; der Akku deckt es ganz, das Budget bleibt bei der bisherigen Spitze
    plugged = planned_car(departure_at=at(SCENARIO_START) + 8 * SLOT)
    expected = planned_car(
        key="egolf",
        name="e-Golf",
        wallbox="evcs",
        departure_at=at(SCENARIO_START) + 8 * SLOT,
        expected=True,
        arrival_at=at(SCENARIO_START),
    )
    common: dict[str, Any] = {
        "prices": [20.0] * 4,
        "pv": [0.0] * 4,
        "load": [500.0] * 4,
        "soc_pct": 80.0,
        "car_battery": True,
        "peak_months": [month("2026-10", 2.0)],
    }
    ev_w = [{"tesla": 4000.0, "egolf": 1500.0}] * 4
    ev_battery_w = [{"tesla": 4000.0}] * 4
    plan = build_plan(
        scenario(**common, vehicles=[plugged, expected]),
        [EV] * 4,
        source="optimizer",
        solver_status="optimal",
        ev_w=ev_w,
        ev_battery_w=ev_battery_w,
    )
    assert [s.budget_kw for s in plan.slots] == [2.0] * 4
