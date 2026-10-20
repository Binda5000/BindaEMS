"""Tests der Szenario-Umgebung (Plan 3b, Task 15): erzeugte Tage, Datei und Lauf.

Die ganzen Vergleiche mit „Victron allein“ rechnet ``tests/integration/test_planning_scenarios.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

import pytest
from pydantic import ValidationError

from bindaems.app.planning.battery_model import BatteryParams, SimSlot, SimStep, eta, simulate
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult
from bindaems.shared.config import load_config
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, Plan
from bindaems.shared.timeutil import LOCAL_TZ, SLOT
from bindaems.tools.scenario import runner
from bindaems.tools.scenario.data import ScenarioCar, ScenarioFile, ScenarioSlot
from bindaems.tools.scenario.runner import (
    car_sessions,
    end_soc_value_ct,
    forecast_at,
    known_until,
    price_inputs,
    run_scenario,
)
from bindaems.tools.scenario.synthetic import DAY_TYPES, synthetic


def local_dt(text: str) -> datetime:
    """Lokale Zeit ``JJJJ-MM-TT HH:MM`` in Europe/Vienna."""
    return datetime.fromisoformat(text).replace(tzinfo=LOCAL_TZ)


# --- Tests aus dem Plan --------------------------------------------------------------------


def test_baseline_never_grid_charges_or_holds() -> None:
    result = run_scenario(
        synthetic("winter-spread", date(2027, 1, 11), 1), step_slots=4, grid_charge=False
    )
    assert result.r1_violations == 0 and all(d.grid_charge_kwh == 0 for d in result.days)


def test_known_prices_follow_day_ahead_rule() -> None:
    data = synthetic("wechselhaft", date(2027, 1, 11), 2)
    assert known_until(data, local_dt("2027-01-11 13:45")) == local_dt("2027-01-12 00:00")
    assert known_until(data, local_dt("2027-01-11 14:00")) == local_dt("2027-01-13 00:00")


def test_forecast_uses_horizon_class_of_lead() -> None:
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 2)
    s = data.slots[60]
    assert forecast_at(s, lead=timedelta(hours=2)).pv == s.pv_forecast_w["h0_6"]
    assert forecast_at(s, lead=timedelta(hours=30)).pv == s.pv_forecast_w["h24"]


def test_synthetic_days_are_deterministic() -> None:
    assert synthetic("wechselhaft", date(2027, 1, 11), 1) == synthetic(
        "wechselhaft", date(2027, 1, 11), 1
    )


# --- Erzeugte Tage ---------------------------------------------------------------------------


def _slot_at(data: ScenarioFile, text: str) -> ScenarioSlot:
    wanted = local_dt(text)
    return next(s for s in data.slots if s.start == wanted)


@pytest.mark.parametrize("kind", DAY_TYPES)
def test_synthetic_days_have_local_days_and_all_classes(kind: str) -> None:
    first = date(2027, 7, 5) if kind.startswith("sommer") else date(2027, 1, 11)
    data = synthetic(kind, first, 2)
    assert len(data.slots) == 192
    assert data.slots[0].start == local_dt(f"{first} 00:00")
    assert all(b.start - a.start == SLOT for a, b in zip(data.slots, data.slots[1:], strict=False))
    assert all(set(s.pv_forecast_w) == {"h0_6", "h6_24", "h24"} for s in data.slots)
    assert all(s.feed_in_ct == pytest.approx(6.0) for s in data.slots)
    assert data.source == "synthetic" and data.planning.grid_charge_allowed
    assert data.battery.start_soc_pct == 50.0


def test_synthetic_battery_matches_example_config() -> None:
    cfg = load_config(Path("deploy/config.example.yaml"))
    battery = synthetic("winter-spread", date(2027, 1, 11), 1).battery
    assert battery.usable_kwh == cfg.battery.usable_kwh
    assert battery.reserve_soc_pct == cfg.battery.reserve_soc_pct
    assert battery.soc_max_pct == cfg.battery.soc_max_pct
    assert battery.max_charge_w == cfg.battery.max_charge_w
    assert battery.max_discharge_w == cfg.battery.max_discharge_w
    assert battery.max_grid_charge_setpoint_w == cfg.victron.max_grid_charge_setpoint_w
    grid = cfg.grid
    assert battery.grid_max_import_w == (grid.fuse_a - grid.fuse_margin_a) * 230 * 3


def test_synthetic_prices_follow_the_example_tariff() -> None:
    # Börsenpreis + 10 ct netto, USt. 20 %
    winter = synthetic("winter-spread", date(2027, 1, 11), 1)
    assert _slot_at(winter, "2027-01-11 03:00").import_ct == pytest.approx((7 + 10) * 1.2)
    assert _slot_at(winter, "2027-01-11 17:00").import_ct == pytest.approx((32 + 10) * 1.2)
    assert _slot_at(winter, "2027-01-11 20:00").import_ct == pytest.approx((12 + 10) * 1.2)
    summer = synthetic("sommer-sonnig", date(2027, 7, 5), 2)
    assert _slot_at(summer, "2027-07-05 13:00").import_ct == pytest.approx((2 + 10) * 1.2)
    assert _slot_at(summer, "2027-07-06 13:00").import_ct == pytest.approx((-1 + 10) * 1.2)
    assert _slot_at(summer, "2027-07-06 14:00").import_ct == pytest.approx((2 + 10) * 1.2)
    assert _slot_at(summer, "2027-07-05 18:00").import_ct == pytest.approx((18 + 10) * 1.2)
    varied = synthetic("wechselhaft", date(2027, 1, 11), 1)
    assert _slot_at(varied, "2027-01-11 12:00").import_ct == pytest.approx((6 + 10) * 1.2)


def test_only_winter_leistung_has_a_power_price() -> None:
    from bindaems.app.tariff.calc import power_rate

    day = date(2027, 1, 11)
    rate = power_rate(day, synthetic("winter-leistung", day, 1).tariff)
    assert rate is not None and not rate.missing
    assert (rate.below_eur_kw_year, rate.above_eur_kw_year) == (30.0, 60.0)
    assert power_rate(day, synthetic("winter-spread", day, 1).tariff) is None


@pytest.mark.parametrize("kind", ["winter-spread", "winter-leistung"])
def test_winter_days_leave_peak_imports_to_victron_alone(kind: str) -> None:
    # Voraussetzung des Vergleichs: Ohne Plan bezieht der Akku zum Spitzenpreis aus dem Netz,
    # sonst gäbe es für Netzladen nichts zu verschieben (Spread-Regel gegen 26,4 ct verfehlt).
    data = synthetic(kind, date(2027, 1, 11), 2)
    battery = data.battery
    params = BatteryParams(30.0, 6.0, 30.0, eta(88.0), eta(88.0))
    slots = [
        SimSlot(s.start, s.pv_w, s.load_w, s.import_ct, s.feed_in_ct, 10000.0, 12000.0)
        for s in data.slots
    ]
    steps = simulate(battery.start_soc_pct * 0.3, slots, [BatteryIntent()] * len(slots), params)
    peak_ct = max(s.import_ct for s in data.slots)
    peak_kwh = (
        sum(st.import_w for s, st in zip(data.slots, steps, strict=True) if s.import_ct == peak_ct)
        / 4000
    )
    assert peak_kwh > 1.0


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_wechselhaft_leaves_peak_imports_to_victron_alone(seed: int) -> None:
    # Wie im Winter: Ohne Plan bezieht der Akku am zweiten Abend zum Spitzenpreis (38,4 ct) aus
    # dem Netz, für jede Saat der Wolken; sonst zeigte der Tag nur, dass der Plan nicht schadet.
    data = synthetic("wechselhaft", date(2027, 1, 11), 2, seed=seed)
    params = BatteryParams(30.0, 6.0, 30.0, eta(88.0), eta(88.0))
    slots = [
        SimSlot(s.start, s.pv_w, s.load_w, s.import_ct, s.feed_in_ct, 10000.0, 12000.0)
        for s in data.slots
    ]
    steps = simulate(
        data.battery.start_soc_pct * 0.3, slots, [BatteryIntent()] * len(slots), params
    )
    peak_ct = max(s.import_ct for s in data.slots)
    peak_kwh = (
        sum(st.import_w for s, st in zip(data.slots, steps, strict=True) if s.import_ct == peak_ct)
        / 4000
    )
    assert peak_ct == pytest.approx((22 + 10) * 1.2)
    assert peak_kwh > 1.0


def test_synthetic_pv_and_load_shapes() -> None:
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 1)
    assert _slot_at(data, "2027-07-05 05:15").pv_w == 0.0
    assert _slot_at(data, "2027-07-05 20:30").pv_w == 0.0
    noon = _slot_at(data, "2027-07-05 12:45")  # Mitte des Tagbogens 05:30–20:30 ist 13:00
    assert 8900 < noon.pv_w <= 9000
    assert 350 * 0.85 <= _slot_at(data, "2027-07-05 03:00").load_w <= 350 * 1.15
    assert 1150 * 0.85 <= _slot_at(data, "2027-07-05 07:00").load_w <= 1150 * 1.15
    assert 1850 * 0.85 <= _slot_at(data, "2027-07-05 19:00").load_w <= 1850 * 1.15
    clouds = synthetic("wechselhaft", date(2027, 1, 11), 1)
    ratio = [s.pv_w for s in clouds.slots if s.pv_w > 0]
    assert ratio and max(ratio) <= 1500


def test_forecast_errors_grow_with_lead() -> None:
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 1)
    for s in data.slots:
        if s.pv_w <= 0:
            continue
        short = s.pv_forecast_w["h0_6"] / s.pv_w - 1
        assert abs(short) <= 0.1 + 1e-9  # ±0,2 · 0,5
        assert s.pv_forecast_w["h24"] / s.pv_w - 1 == pytest.approx(3 * short)
        load_short = s.load_forecast_w["h0_6"] / s.load_w - 1
        assert abs(load_short) <= 0.05 + 1e-9  # ±0,1 · 0,5


def test_scenario_file_roundtrips_as_json() -> None:
    data = synthetic("winter-leistung", date(2027, 1, 11), 1)
    assert ScenarioFile.model_validate_json(data.model_dump_json()) == data


def test_unknown_day_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unbekannte Art"):
        synthetic("herbst", date(2027, 1, 11), 1)


# --- Lauf ----------------------------------------------------------------------------------


def test_forecast_falls_back_to_next_class_then_actual() -> None:
    s = synthetic("sommer-sonnig", date(2027, 7, 5), 1).slots[50]
    only_short = s.model_copy(update={"pv_forecast_w": {"h0_6": 1.0}, "load_forecast_w": {}})
    assert forecast_at(only_short, lead=timedelta(hours=30)).pv == 1.0
    assert forecast_at(only_short, lead=timedelta(hours=30)).load == s.load_w
    long_only = s.model_copy(update={"pv_forecast_w": {"h24": 2.0, "h0_6": 1.0}})
    # fehlt h6_24, gilt zuerst die früher ausgegebene Klasse h24
    assert forecast_at(long_only, lead=timedelta(hours=10)).pv == 2.0


def test_estimated_prices_use_median_of_previous_days() -> None:
    data = synthetic("wechselhaft", date(2027, 1, 11), 4)
    now = local_dt("2027-01-13 10:01")  # zwei Vortage in den Daten, bekannt bis Mitternacht
    prices = {p.start: p for p in price_inputs(data, now)}
    known = prices[local_dt("2027-01-13 23:45")]
    assert known.origin == "primary"
    later = prices[local_dt("2027-01-14 18:00")]
    assert later.origin == "estimated"
    before = [_slot_at(data, f"2027-01-1{d} 18:00").import_ct for d in (1, 2)]
    assert later.import_ct == pytest.approx(median(before))
    # am zweiten Tag gibt es nur einen Vortag: keine Schätzung
    early = {p.start for p in price_inputs(data, local_dt("2027-01-12 10:01"))}
    assert max(early) == local_dt("2027-01-12 23:45")


def test_baseline_is_own_consumption_on_actual_values() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    result = run_scenario(data, step_slots=4)
    battery = data.battery
    params = BatteryParams(
        capacity_kwh=battery.usable_kwh,
        lo_kwh=battery.reserve_soc_pct / 100 * battery.usable_kwh,
        hi_kwh=battery.soc_max_pct / 100 * battery.usable_kwh,
        eta_ch=eta(88.0),
        eta_dis=eta(88.0),
    )
    slots = [
        SimSlot(s.start, s.pv_w, s.load_w, s.import_ct, s.feed_in_ct, 10000.0, 12000.0)
        for s in data.slots
    ]
    steps = simulate(15.0, slots, [BatteryIntent()] * len(slots), params)
    assert result.days[0].baseline_ct == pytest.approx(sum(s.cost_ct for s in steps))
    assert result.baseline_ct == pytest.approx(result.days[0].baseline_ct)  # ohne Leistungspreis
    assert result.optimizer_ct == pytest.approx(result.days[0].optimizer_ct)
    assert result.baseline_power_ct == result.optimizer_power_ct == 0.0
    assert result.plans == 24 and result.step_slots == 4
    assert result.reserve_kwh == pytest.approx(6.0)
    assert result.min_soc_kwh >= result.reserve_kwh - 0.01
    assert [d.day for d in result.days] == [date(2027, 1, 11)]


def test_end_soc_adjustment_uses_median_of_last_day() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    last_day = [s.import_ct for s in data.slots[96:]]
    value = end_soc_value_ct(data, 2.0)
    assert value == pytest.approx(2.0 * eta(88.0) * median(last_day))
    assert end_soc_value_ct(data, -1.0) == pytest.approx(-value / 2)


def test_power_price_counts_highest_import_of_the_month() -> None:
    data = synthetic("winter-leistung", date(2027, 1, 11), 1)
    peak = data.model_copy(update={"month_peak_kw": 12.0})
    result = run_scenario(peak, step_slots=4, grid_charge=False)
    # 12 kW vor dem Lauf: 10 kW · 30 €/kW/Jahr + 2 kW · 60 €/kW/Jahr, je Monat, USt. 20 %
    expected_ct = (10 * 30 + 2 * 60) / 12 * 1.2 * 100
    assert result.baseline_power_ct == pytest.approx(expected_ct)
    assert result.optimizer_power_ct == pytest.approx(expected_ct)
    assert result.baseline_ct == pytest.approx(result.days[0].baseline_ct + expected_ct)


def _record_steps(monkeypatch: pytest.MonkeyPatch) -> list[tuple[SimSlot, BatteryIntent, SimStep]]:
    """Zeichnet jeden ausgeführten Slot des Laufs auf: Ist-Werte, Absicht, Ergebnis."""
    seen: list[tuple[SimSlot, BatteryIntent, SimStep]] = []
    original = runner.step

    def recording(
        soc: float, slot: SimSlot, intent: BatteryIntent, params: BatteryParams
    ) -> SimStep:
        result = original(soc, slot, intent, params)
        seen.append((slot, intent, result))
        return result

    monkeypatch.setattr(runner, "step", recording)
    return seen


def _record_inputs(monkeypatch: pytest.MonkeyPatch) -> list[PlanInputs]:
    seen: list[PlanInputs] = []
    original = runner.solve

    def recording(inputs: PlanInputs) -> OptimizerResult:
        seen.append(inputs)
        return original(inputs)

    monkeypatch.setattr(runner, "solve", recording)
    return seen


def test_slots_without_valid_plan_run_on_their_own(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bei einem Takt von 4 h gilt jeder Plan 45 min (Minute 1 bis 46): die ersten vier Slots;
    # danach läuft der Akku auf Eigenverbrauch.
    seen = _record_steps(monkeypatch)
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    result = run_scenario(data, step_slots=16)
    assert result.plans == 6
    kinds = [intent.kind for _, intent, _ in seen]
    assert all(
        kind is BatteryIntentKind.EIGENVERBRAUCH for i, kind in enumerate(kinds) if i % 16 >= 4
    )
    assert any(kind is not BatteryIntentKind.EIGENVERBRAUCH for kind in kinds)  # Pläne wirken


def test_replanning_carries_soc_peak_and_previous_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    steps = _record_steps(monkeypatch)
    inputs = _record_inputs(monkeypatch)
    data = synthetic("winter-leistung", date(2027, 1, 11), 1)
    run_scenario(data.model_copy(update={"month_peak_kw": 1.5}), step_slots=8)
    assert [i.trigger for i in inputs[:2]] == ["start", "slot"]
    assert inputs[0].slots[0].prev_battery_w is None
    assert inputs[1].slots[0].prev_battery_w is not None  # voriger Plan für die Planträgheit
    for item in inputs:
        index = (item.slots[0].start - data.slots[0].start) // SLOT
        assert item.created_at == item.slots[0].start + timedelta(minutes=1)
        before = [result for _, _, result in steps[:index]]
        expected_soc = before[-1].soc_end_kwh if before else 15.0
        assert item.battery.soc_pct == pytest.approx(expected_soc / 30 * 100)
        peak = max([1.5, *(result.import_w / 1000 for result in before)])
        assert item.month_peak_kw == pytest.approx(peak)
        assert item.peak_months[0].peak0_kw == pytest.approx(max(peak, 2.0))  # Mindestwert 2 kW


def test_missing_inputs_give_no_plan_and_own_consumption(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ab drei Slots vor dem Ende der Daten reicht der Horizont nicht mehr (unter 4 Slots).
    seen = _record_steps(monkeypatch)
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    result = run_scenario(data, step_slots=31)  # Neuplanung bei 0, 31, 62 und 93
    assert result.plans == 3
    assert [intent.kind for _, intent, _ in seen[93:]] == [BatteryIntentKind.EIGENVERBRAUCH] * 3


def test_failed_solver_gives_fallback_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(inputs: PlanInputs) -> OptimizerResult:
        return OptimizerResult("error", (), {}, None, 0, "kaputt")

    monkeypatch.setattr(runner, "solve", failing)
    result = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=8)
    assert result.plans == result.fallbacks == 24
    assert [d.fallbacks for d in result.days] == [12, 12]


def test_grid_charge_before_export_counts_as_rule_r_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ein Plan, der an einem Sonnentag morgens aus dem Netz lädt, verletzt Regel R: Danach
    # erwartet er Einspeisung. Der Lauf zählt jeden ausgeführten Slot davon.
    original = runner.plan_once

    def charging(inputs: PlanInputs, result: OptimizerResult) -> Plan:
        plan = original(inputs, result)
        intent = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000.0)
        slots = [s.model_copy(update={"intent": intent}) for s in plan.slots]
        return plan.model_copy(update={"slots": slots})

    monkeypatch.setattr(runner, "plan_once", charging)
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 1)
    result = run_scenario(data, step_slots=4)
    assert result.r1_violations == result.days[0].r1_violations
    # vor 20:30 folgt Einspeisung (Verstoß), danach nicht mehr: nur ein Teil der 96 Slots zählt.
    # Der Plan lädt in jedem Slot, der Akku ist nie leer: bis zum Horizontende wie bisher (75).
    assert 40 < result.r1_violations < 96
    assert result.days[0].grid_charge_kwh > 0
    assert 0 < result.charge_before_export_kwh <= result.days[0].grid_charge_kwh


def test_scenario_rejects_bad_step() -> None:
    with pytest.raises(ValueError, match="step_slots"):
        run_scenario(synthetic("winter-spread", date(2027, 1, 11), 1), step_slots=0)
    with pytest.raises(ValueError, match="Mindestens ein Tag"):
        synthetic("winter-spread", date(2027, 1, 11), 0)


# --- Ein geplantes Auto (Plan 4b, Task 13) ---------------------------------------------------

CAR_NEED_KWH = (80 - 40) / 100 * 60 / 0.9  # Bedarf aus der Wallbox (Ruling R10)


def _without_car(data: ScenarioFile) -> dict[str, object]:
    """Die Datei als JSON wie in Version 1: ohne Autos und ohne P25."""
    raw = json.loads(data.model_dump_json())
    raw["version"] = 1
    del raw["cars"]
    for item in raw["slots"]:
        del item["pv_p25_w"]
    return raw


@pytest.mark.parametrize("kind", DAY_TYPES)
def test_synthetic_days_have_one_planned_car(kind: str) -> None:
    data = synthetic(kind, date(2027, 1, 11), 1)
    assert data.version == 2
    assert data.cars == [
        ScenarioCar(
            key="auto",
            arrive="18:00",
            depart="07:00",
            soc_arrive_pct=40.0,
            target_pct=80.0,
            usable_kwh=60.0,
            max_w=11000.0,
            min_w=4140.0,
            efficiency=0.9,
        )
    ]


def test_synthetic_p25_lies_below_the_forecast() -> None:
    # P25 ist das untere Quartil des Prognosefehlers (gleichverteilt, ±0,2 · k): P50 · (1 − 0,1 · k)
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 1)
    for s in data.slots:
        assert set(s.pv_p25_w) == {"h0_6", "h6_24", "h24"}
        for cls, k in (("h0_6", 0.5), ("h6_24", 1.0), ("h24", 1.5)):
            assert s.pv_p25_w[cls] == pytest.approx(s.pv_forecast_w[cls] * (1 - 0.1 * k))


def test_forecast_carries_p25_of_the_same_class() -> None:
    s = synthetic("sommer-sonnig", date(2027, 7, 5), 1).slots[50]
    assert forecast_at(s, lead=timedelta(hours=30)).pv_p25 == s.pv_p25_w["h24"]
    assert forecast_at(s.model_copy(update={"pv_p25_w": {}}), lead=timedelta(0)).pv_p25 is None


def test_car_sessions_need_their_departure_within_the_data() -> None:
    # Angesteckt jeden Abend um 18:00, Abfahrt am Morgen danach um 07:00; die Abfahrt des
    # zweiten Abends liegt hinter den Daten, der Ansteckvorgang fehlt.
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    sessions = car_sessions(data)
    assert [(s.car.key, s.arrive, s.depart) for s in sessions] == [
        ("auto", local_dt("2027-01-11 18:00"), local_dt("2027-01-12 07:00"))
    ]
    assert car_sessions(synthetic("winter-spread", date(2027, 1, 11), 1)) == []


def test_version_1_file_loads_and_runs_without_car(monkeypatch: pytest.MonkeyPatch) -> None:
    old = ScenarioFile.model_validate(
        _without_car(synthetic("winter-spread", date(2027, 1, 11), 2))
    )
    assert old.version == 1 and old.cars == []
    assert all(s.pv_p25_w == {} for s in old.slots)
    seen = _record_steps(monkeypatch)
    inputs = _record_inputs(monkeypatch)
    result = run_scenario(old, step_slots=8)
    assert all(item.vehicles == [] for item in inputs)
    assert all(slot.pv_p25_w is None for item in inputs for slot in item.slots)
    assert all(slot.ev_w == 0 for slot, _, _ in seen)
    assert result.plans == 24  # keine Neuplanung beim Anstecken
    assert (result.targets, result.targets_missed, result.r2_violations) == (0, 0, 0)
    assert all(d.targets == d.targets_reached == d.r2_violations == 0 for d in result.days)


def test_cars_need_version_2() -> None:
    raw = _without_car(synthetic("winter-spread", date(2027, 1, 11), 1))
    raw["cars"] = [synthetic("winter-spread", date(2027, 1, 11), 1).cars[0].model_dump()]
    with pytest.raises(ValidationError, match="Version 2"):
        ScenarioFile.model_validate(raw)


def test_car_rejects_bad_times_and_power() -> None:
    car = synthetic("winter-spread", date(2027, 1, 11), 1).cars[0].model_dump()
    for change in ({"arrive": "18:10"}, {"depart": "24:00"}, {"min_w": 12000.0}):
        with pytest.raises(ValidationError):
            ScenarioCar.model_validate({**car, **change})
    with pytest.raises(ValidationError, match="Ankunft und Abfahrt"):
        ScenarioCar.model_validate({**car, "depart": "18:00"})


def _record_plans(monkeypatch: pytest.MonkeyPatch) -> list[Plan]:
    seen: list[Plan] = []
    original = runner.plan_once

    def recording(inputs: PlanInputs, result: OptimizerResult) -> Plan:
        plan = original(inputs, result)
        seen.append(plan)
        return plan

    monkeypatch.setattr(runner, "plan_once", recording)
    return seen


def test_car_follows_plan_power_up_to_its_need(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_steps(monkeypatch)
    inputs = _record_inputs(monkeypatch)
    plans = _record_plans(monkeypatch)
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    result = run_scenario(data, step_slots=4)
    arrive, depart = local_dt("2027-01-11 18:00"), local_dt("2027-01-12 07:00")
    # Plan und Ausführung kennen das Auto nur, solange es steckt
    with_car = [item for item in inputs if item.vehicles]
    assert [item.created_at for item in with_car] == [
        arrive + timedelta(hours=h, minutes=1) for h in range(13)
    ]
    first = with_car[0].vehicles[0]
    assert (first.key, first.soc_pct, first.target_soc_pct) == ("auto", 40.0, 80.0)
    assert first.departure_at == depart and first.need_kwh == pytest.approx(CAR_NEED_KWH)
    assert all(slot.pv_p25_w is not None for slot in with_car[0].slots)
    charged = 0.0
    for slot, _, step in seen:
        if not arrive <= slot.start < depart:
            assert slot.ev_w == 0
            continue
        plan = next(p for p in reversed(plans) if p.created_at <= slot.start + SLOT)
        planned_w = plan.slots[(slot.start - plan.slots[0].start) // SLOT].ev_w.get("auto", 0.0)
        # Ladeleistung des Plans, begrenzt auf den Bedarf; SOC des nächsten Plans danach
        remaining = max(0.0, CAR_NEED_KWH - charged)
        assert slot.ev_w == pytest.approx(min(planned_w, remaining / 0.25 * 1000), abs=1e-6)
        assert step.ev_pv_w + step.ev_grid_w == pytest.approx(slot.ev_w)
        charged += slot.ev_w * 0.25 / 1000
    assert charged == pytest.approx(CAR_NEED_KWH)
    later = with_car[-1].vehicles[0]
    assert later.soc_pct <= 80.0 + 1e-6
    assert (result.targets, result.targets_missed) == (1, 0)
    assert [(d.targets, d.targets_reached) for d in result.days] == [(0, 0), (1, 1)]
    assert result.r2_violations == 0


def test_replanning_on_plug_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wie der Auslöser „vehicles“ im Dienst: Beim Anstecken wird sofort neu geplant.
    inputs = _record_inputs(monkeypatch)
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    result = run_scenario(data, step_slots=7)
    arrive = local_dt("2027-01-11 18:00")
    assert [
        item.trigger for item in inputs if item.created_at == arrive + timedelta(minutes=1)
    ] == ["vehicles"]
    assert result.plans == 28  # 0, 7, …, 182 und 72 (Anstecken); ab 189 reicht der Horizont nicht


def test_plan_check_resimulates_with_the_car(monkeypatch: pytest.MonkeyPatch) -> None:
    # Die Nachrechnung je Plan (für Regel R) rechnet die Ladeleistung der Autos mit.
    calls: list[tuple[PlanInputs, list[Mapping[str, float]]]] = []
    original = runner.sim_slots

    def recording(inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]] = ()) -> list[SimSlot]:
        calls.append((inputs, list(ev_w)))
        return original(inputs, ev_w)

    monkeypatch.setattr(runner, "sim_slots", recording)
    run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=8)
    with_car = [(item, ev_w) for item, ev_w in calls if item.vehicles]
    assert with_car
    assert any(powers.get("auto", 0.0) > 0 for _, ev_w in with_car for powers in ev_w)


def test_victron_alone_charges_the_car_fully_from_arrival() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    result = run_scenario(data, step_slots=8)
    params = BatteryParams(30.0, 6.0, 30.0, eta(88.0), eta(88.0))
    ev_w = [0.0] * len(data.slots)
    left = CAR_NEED_KWH
    index = 72  # 18:00 am ersten Tag
    while left > 1e-9:
        ev_w[index] = min(11000.0, left / 0.25 * 1000)
        left -= ev_w[index] * 0.25 / 1000
        index += 1
    assert index == 82  # 9 Slots voll, im zehnten der Rest
    slots = [
        SimSlot(s.start, s.pv_w, s.load_w, s.import_ct, s.feed_in_ct, 10000.0, 12000.0, ev_w[i])
        for i, s in enumerate(data.slots)
    ]
    steps = simulate(15.0, slots, [BatteryIntent()] * len(slots), params)
    assert result.baseline_ct == pytest.approx(sum(s.cost_ct for s in steps))
    assert result.days[0].baseline_ct == pytest.approx(sum(s.cost_ct for s in steps[:96]))


def test_missed_target_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ein Plan, der das Auto nie lädt: Es fährt mit 40 % statt 80 % ab.
    original = runner.plan_once

    def idle(inputs: PlanInputs, result: OptimizerResult) -> Plan:
        plan = original(inputs, result)
        slots = [s.model_copy(update={"ev_w": {}}) for s in plan.slots]
        return plan.model_copy(update={"slots": slots})

    monkeypatch.setattr(runner, "plan_once", idle)
    result = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=4)
    assert (result.targets, result.targets_missed) == (1, 1)
    assert [(d.targets, d.targets_reached) for d in result.days] == [(0, 0), (1, 0)]


def test_plan_over_grid_allowed_counts_as_r2_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ein Plan, der das Auto bis zur Abfahrt mit voller Leistung aus dem Netz lädt, übersteigt
    # die nach Regel R2 erlaubte Netzenergie: Jeder Plan mit dem Auto zählt.
    original = runner.plan_once

    def greedy(inputs: PlanInputs, result: OptimizerResult) -> Plan:
        plan = original(inputs, result)
        if not inputs.vehicles:
            return plan
        depart = inputs.vehicles[0].departure_at
        slots = [
            s.model_copy(update={"ev_w": {"auto": 11000.0}}) if s.start < depart else s
            for s in plan.slots
        ]
        return plan.model_copy(update={"slots": slots})

    monkeypatch.setattr(runner, "plan_once", greedy)
    result = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=4)
    assert result.r2_violations == 13  # 18:01 bis 06:01
    assert [d.r2_violations for d in result.days] == [6, 7]
    assert result.targets_missed == 0  # die Ausführung lädt nur den Bedarf


def test_without_plan_the_car_starts_at_latest_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ohne Plan wie der core: ab der spätesten Startzeit 07:00 − 26,67 kWh / 11 kW · 1,1 − 15 min
    # = 04:05 mit voller Leistung (ab dem Slot 04:00), bis das Ziel erreicht ist.
    seen = _record_steps(monkeypatch)
    monkeypatch.setattr(runner, "_plan_at", lambda *args, **kwargs: None)
    result = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=4)
    charging = [slot for slot, _, _ in seen if slot.ev_w > 0]
    assert charging[0].start == local_dt("2027-01-12 04:00")
    assert [slot.ev_w for slot in charging[:9]] == [11000.0] * 9
    assert sum(slot.ev_w for slot in charging) * 0.25 / 1000 == pytest.approx(CAR_NEED_KWH)
    assert (result.plans, result.targets, result.targets_missed) == (0, 1, 0)
