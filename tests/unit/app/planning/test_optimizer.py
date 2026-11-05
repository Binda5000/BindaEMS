"""Tests des Optimierers für den Akku (Präzisierungen 5 und 6): Netzladen mit Marge und Schalter,
Regel R1, Reserve, Leistungspreis je Monat, Planträgheit und Fehler des Solvers; dazu die geplanten
Autos (Plan 4b, Präzisierung 11): günstigste Slots, Vorrang vor dem Akku, Fehlmenge, Regel R2 und
Trägheit; Akku ins Auto, wenn er übrig ist (Plan Akku ins Auto, Entscheidungen 2 und 3)."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, NoReturn

import pulp
import pytest
from tests.plan_helpers import (
    SCENARIO_START,
    at,
    month,
    planned_car,
    pv_arc,
    resimulate,
    scenario,
    small_battery,
    state,
)

from bindaems.app.planning import optimizer
from bindaems.app.planning.battery_model import SLOT_H, params_of
from bindaems.app.planning.grid_phases import PHASES, phase_shares
from bindaems.app.planning.inputs import BatteryState, PeakMonth, PlanInputs, peak_months
from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import (
    R1_EMPTY_TOL_KWH,
    TIME_LIMIT_S,
    OptimizerResult,
    SlotFlows,
    ev_battery_power_w,
    ev_power_w,
    grid_charge_cap_w,
    recharge_cap_w,
    solve,
    storm_charge_caps_w,
)
from bindaems.app.planning.plausibility import R1_TOL_KWH, check_plan, export_while_charged_kwh
from bindaems.app.planning.vehicles import (
    EV_ON_W,
    PlanVehicleInput,
    grid_allowed,
    horizon_need,
    need_kwh,
)
from bindaems.app.storm.windows import storm_floors
from bindaems.shared.config import Phase
from bindaems.shared.plan import RESERVE_RECHARGE_MAX_W, BatteryIntentKind, StormWarning
from bindaems.shared.settings import StormSettings, TariffSettings
from bindaems.shared.timeutil import SLOT

SLOT_KWH = SLOT_H / 1000  # W über einen Slot → kWh
_EMPTY_EPS_KWH = 1e-6  # Rundung des Lösers an der Leer-Grenze von Regel R1


def boom(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise RuntimeError("boom")


def test_cheap_night_expensive_evening_grid_charges_when_allowed() -> None:
    inputs = scenario(
        prices=[10.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[2000.0] * 8,
        soc_pct=20.0,
        grid_charge=True,
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_b for f in result.flows[:4]) > 0
    assert sum(f.bt_h for f in result.flows[4:]) > 0


def test_small_spread_does_not_grid_charge() -> None:
    # Je kWh aus dem Netz: Ertrag 0,88 · (14 − 2 Verschleiß) = 10,56 ct gegen 10 ct Bezug;
    # ohne Marge lohnt sich das, mit 2 ct Marge (12 ct) nicht.
    kw: dict[str, Any] = {
        "prices": [10.0] * 4 + [14.0] * 4,
        "pv": [0.0] * 8,
        "load": [2000.0] * 8,
        "soc_pct": 20.0,
        "grid_charge": True,
    }
    assert all(f.gr_b < 1 for f in solve(scenario(**kw)).flows)
    assert sum(f.gr_b for f in solve(scenario(**kw, margin_ct=0.0)).flows[:4]) > 0


def test_switch_off_never_grid_charges_even_at_negative_price() -> None:
    inputs = scenario(
        prices=[-5.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[2000.0] * 8,
        soc_pct=20.0,
        grid_charge=False,
    )
    assert all(f.gr_b == 0 for f in solve(inputs).flows)


def test_r1_allows_grid_charge_used_up_before_pv_surplus() -> None:
    # 10-kWh-Akku, 5 kW Ladeleistung: 8,5 kW Überschuss erzwingen später Einspeisung; die billige
    # Netzenergie ist im teuren Block verbraucht, bevor die PV kommt
    inputs = scenario(
        prices=[5.0] * 4 + [40.0] * 4 + [8.0] * 8,
        pv=[0.0] * 8 + [9000.0] * 8,
        load=[500.0] * 4 + [3000.0] * 4 + [500.0] * 8,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    flows = solve(inputs).flows
    assert sum(f.gr_b for f in flows[:4]) > 0
    lo = params_of(inputs).lo_kwh
    for i, flow in enumerate(flows):
        if flow.gr_b <= 1:
            continue
        end = next(
            (
                j
                for j in range(i, len(flows))
                if flows[j].soc_end_kwh <= lo + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
            ),
            len(flows) - 1,
        )
        assert sum(f.pv_x for f in flows[i : end + 1]) * SLOT_KWH <= 0.001


def _grid_charge_into_pv_surplus() -> PlanInputs:
    """Billig laden und dafür PV zu 20 ct einspeisen lohnte sich ohne R1; der Akku wird vor der
    PV nicht leer, die Netzenergie wäre noch drin."""
    return scenario(
        prices=[5.0] * 4 + [8.0] * 8 + [40.0] * 4,
        pv=[0.0] * 4 + [9000.0] * 8 + [0.0] * 4,
        load=[500.0] * 4 + [500.0] * 8 + [3000.0] * 4,
        feed_in=20.0,
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )


def test_r1_blocks_grid_charge_that_lasts_into_pv_surplus() -> None:
    inputs = _grid_charge_into_pv_surplus()
    lo = params_of(inputs).lo_kwh
    # Erlaubt bleibt nur Netzenergie in Höhe der Leer-Toleranz: Nach jedem Netzladen gilt der Akku
    # noch als leer, sonst wäre er bis in die PV-Einspeisung gefüllt
    assert all(
        f.gr_b < 1 or f.soc_end_kwh <= lo + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
        for f in solve(inputs).flows
    )


def test_without_r1_the_same_inputs_grid_charge_into_pv_surplus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Kontrollfall zum Test davor: Gilt der Akku immer als leer (Toleranz über der ganzen
    # Kapazität), sperrt R1 nichts mehr, und der Optimierer lädt mit denselben Eingaben aus dem
    # Netz über die Leer-Grenze hinaus. Die Sperre oben kommt also von Regel R1.
    monkeypatch.setattr(optimizer, "R1_EMPTY_TOL_KWH", 100.0)
    inputs = _grid_charge_into_pv_surplus()
    lo = params_of(inputs).lo_kwh
    assert any(
        f.gr_b >= 1 and f.soc_end_kwh > lo + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
        for f in solve(inputs).flows
    )


def test_r1_with_soc_below_reserve_at_start() -> None:
    # SOC 10 % unter der Reserve 20 %: lo = soc0, der Akku gilt als leer, Netzladen bleibt möglich
    inputs = scenario(
        prices=[5.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[500.0] * 4 + [3000.0] * 4,
        battery=small_battery(),
        soc_pct=10.0,
        grid_charge=True,
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_b for f in result.flows[:4]) > 0


def test_soc_below_reserve_is_feasible() -> None:
    result = solve(scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[1000.0] * 8, soc_pct=10.0))
    assert result.status == "optimal" and all(f.bt_h < 1 for f in result.flows)


def test_peak_term_limits_grid_import() -> None:
    inputs = scenario(
        prices=[5.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[1000.0] * 8,
        soc_pct=20.0,
        grid_charge=True,
        peak_months=[
            PeakMonth(
                month="2027-01",
                peak0_kw=2.0,
                tier_kw=10.0,
                below_ct_per_kw=5000.0,
                above_ct_per_kw=5000.0,
            )
        ],
        start="2027-01-15T00:00:00+00:00",
    )
    result = solve(inputs)
    assert max(f.gr_h + f.gr_b for f in result.flows) <= 2001
    assert result.peaks_kw == {"2027-01": pytest.approx(2.0)}


def test_month_boundary_gets_second_peak() -> None:
    # 23:00 bis 01:00 lokal; Akku auf Reserve: die Last kommt in beiden Monaten aus dem Netz
    inputs = scenario(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[3000.0] * 8,
        soc_pct=20.0,
        start="2027-01-31T22:00:00+00:00",
        peak_months=[month("2027-01", 2.0), month("2027-02", 2.0)],
    )
    assert solve(inputs).peaks_kw == {
        "2027-01": pytest.approx(3.0),
        "2027-02": pytest.approx(3.0),
    }


def test_pv_covers_house_first_even_when_switch_off() -> None:
    # Nicht PV in den Akku und Haus aus dem Netz: das wäre verdecktes Netzladen.
    inputs = scenario(
        prices=[10.0] * 4 + [40.0] * 4,
        pv=[3000.0] * 4 + [0.0] * 4,
        load=[1000.0] * 4 + [2000.0] * 4,
        soc_pct=20.0,
    )
    result = solve(inputs)
    assert result.status == "optimal"
    for slot, f in zip(inputs.slots, result.flows, strict=True):
        assert f.gr_b == 0
        assert f.pv_h == pytest.approx(min(slot.pv_w, slot.load_w), abs=1e-6)
        assert f.pv_b <= max(0.0, slot.pv_w - slot.load_w) + 1e-6
        assert f.gr_h + f.bt_h <= max(0.0, slot.load_w - slot.pv_w) + 1e-6


def test_pv_covers_house_before_export_when_feed_in_beats_import() -> None:
    # Einspeisung über dem Bezugspreis: trotzdem weder Netz noch Akku fürs Haus, solange PV reicht
    inputs = scenario(
        prices=[5.0] * 4, pv=[3000.0] * 4, load=[1000.0] * 4, soc_pct=100.0, feed_in=20.0
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert all(f.pv_h == pytest.approx(1000.0) and f.gr_h + f.bt_h < 1e-6 for f in result.flows)


def test_inertia_penalizes_deviation_from_previous_plan() -> None:
    base: dict[str, Any] = {
        "prices": [30.0] * 8,
        "pv": [0.0] * 8,
        "load": [1000.0] * 8,
        "soc_pct": 80.0,
        "prev_battery_w": [0.0] * 4,
    }
    assert all(f.bt_h < 1 for f in solve(scenario(**base, inertia_ct=100.0)).flows[:4])
    assert all(f.bt_h > 999 for f in solve(scenario(**base, inertia_ct=0.0)).flows[:4])


def test_solver_error_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pulp.LpProblem, "solve", boom)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4))
    assert (result.status, result.flows) == ("error", ())
    assert result.message is not None and "boom" in result.message


def test_grid_charge_cap_follows_setpoint_and_switch() -> None:
    on = scenario(
        prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0, 7000.0, 9000.0, 500.0], grid_charge=True
    )
    assert [grid_charge_cap_w(on, i) for i in range(4)] == [7500.0, 1000.0, 0.0, 7500.0]
    off = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4)
    assert grid_charge_cap_w(off, 0) == 0.0


def test_soc_trajectory_and_peaks_are_consistent() -> None:
    # 9 kWh über der Reserve reichen nicht für 16 kWh Last: Netzbezug über 2 kW nötig
    inputs = scenario(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[8000.0] * 8,
        start="2027-01-31T22:00:00+00:00",
        peak_months=[month("2027-01", 2.0), month("2027-02", 2.0)],
    )
    result = solve(inputs)
    assert result.status == "optimal" and result.objective_ct is not None
    assert len(result.flows) == 8 and result.solve_ms >= 0
    soc = 15.0
    for f in result.flows:
        soc += 0.25 / 1000 * (0.88**0.5 * (f.pv_b + f.gr_b) - f.bt_h / 0.88**0.5)
        assert f.soc_end_kwh == pytest.approx(soc, abs=1e-6)
        assert f.pv_h + f.gr_h + f.bt_h == pytest.approx(8000.0, abs=1e-6)
    # Spitze je Monat = max(peak0, höchster Bezug im Monat), denn höhere Spitzen kosten
    highest = {
        name: max(f.gr_h + f.gr_b for f in flows) / 1000
        for name, flows in (("2027-01", result.flows[:4]), ("2027-02", result.flows[4:]))
    }
    assert max(highest.values()) > 2.0
    assert result.peaks_kw == {
        name: pytest.approx(max(2.0, value), abs=1e-6) for name, value in highest.items()
    }


def test_infeasible_model_has_no_flows() -> None:
    # Last über dem Netzanschluss bei leerem Akku
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[60000.0] * 4, soc_pct=20.0))
    assert (result.status, result.flows, result.objective_ct) == ("infeasible", (), None)


def _hard() -> PlanInputs:
    rng = random.Random(1)  # noqa: S311 - feste Testdaten
    n = 96
    return scenario(
        prices=[rng.uniform(-5.0, 40.0) for _ in range(n)],
        pv=[max(0.0, rng.uniform(-3000.0, 9000.0)) for _ in range(n)],
        load=[rng.uniform(100.0, 3000.0) for _ in range(n)],
        soc_pct=30.0,
        grid_charge=True,
        battery=small_battery(),
    )


def test_time_limit_without_solution_has_no_flows() -> None:
    result = solve(_hard(), time_limit_s=1e-6)
    assert (result.status, result.flows, result.message) == ("time_limit", (), None)


def test_time_limit_with_solution_keeps_flows(monkeypatch: pytest.MonkeyPatch) -> None:
    original = pulp.LpProblem.solve

    def stopped(self: pulp.LpProblem, *args: Any, **kwargs: Any) -> int:
        status: int = original(self, *args, **kwargs)
        self.sol_status = pulp.LpSolutionIntegerFeasible  # wie HiGHS bei kTimeLimit mit Lösung
        return status

    monkeypatch.setattr(pulp.LpProblem, "solve", stopped)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4))
    assert result.status == "time_limit" and len(result.flows) == 4


def test_unsolved_status_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def unsolved(self: pulp.LpProblem, *_args: Any, **_kwargs: Any) -> int:
        self.status, self.sol_status = pulp.LpStatusNotSolved, pulp.LpSolutionNoSolutionFound
        return int(self.status)

    monkeypatch.setattr(pulp.LpProblem, "solve", unsolved)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4))
    assert (result.status, result.flows, result.message) == ("error", (), "Not Solved")


# Geplante Autos (Plan 4b, Präzisierung 11)


def slot(n: int) -> datetime:
    """Beginn des Slots ``n`` der Szenarien (ab ``SCENARIO_START``): Abfahrt nach ``n`` Slots."""
    return at(SCENARIO_START) + n * SLOT


def car_w(flow: SlotFlows, key: str = "tesla") -> float:
    """Ladeleistung des Autos im Slot (PV plus Netz), 0 ohne Variablen."""
    pv, grid = flow.ev.get(key, (0.0, 0.0))
    return pv + grid


def car_kwh(result: OptimizerResult, key: str = "tesla") -> float:
    return sum(car_w(f, key) for f in result.flows) * SLOT_KWH


def test_car_charges_in_the_cheapest_slots_before_departure() -> None:
    car = planned_car(departure_at=slot(8), need_kwh=3.1)
    inputs = scenario(
        prices=[30.0, 30.0, 10.0, 10.0, 30.0, 30.0, 30.0, 30.0],
        pv=[0.0] * 8,
        load=[500.0] * 8,
        vehicles=[car],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert result.shortfall_kwh == {"tesla": pytest.approx(0.0, abs=1e-6)}
    # Wallbox-Seite (Ruling R10): 3,1 kWh aus der Wallbox, ohne η
    assert (car_w(result.flows[2]) + car_w(result.flows[3])) * SLOT_KWH == pytest.approx(3.1)
    assert all(car_w(result.flows[t]) < 1e-6 for t in (0, 1, 4, 5, 6, 7))


def test_pv_goes_to_the_car_before_the_battery() -> None:
    # 5 kW Überschuss, das Auto muss 7 kW laden; der Restwert macht PV im Akku wertvoller als
    # im Auto, trotzdem kein Netz ins Auto, während PV in den Akku geht
    car = planned_car(departure_at=slot(4), max_w=7000.0, need_kwh=7.0)
    inputs = scenario(
        prices=[10.0] * 4,
        pv=[5500.0] * 4,
        load=[500.0] * 4,
        soc_pct=30.0,
        end_value_ct=40.0,
        vehicles=[car],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    for f in result.flows:
        assert f.ev["tesla"] == (pytest.approx(5000.0), pytest.approx(2000.0))
        assert (f.pv_b, f.pv_x) == (pytest.approx(0.0, abs=1e-6), pytest.approx(0.0, abs=1e-6))


def _r2_day(*, allowed: float | None) -> PlanInputs:
    """02:00 bis 16:00 Ortszeit: Nacht bis 07:00 für 10 ct, dann 30 ct, Einspeisung 15 ct, PV mit
    8 kW Spitze; Abfahrt um 16:00 mit 10 kWh Bedarf. ``allowed`` ``None``: Regel R2 aus P25."""
    count = 56
    car = planned_car(departure_at=slot(count), need_kwh=10.0)
    inputs = scenario(
        prices=[10.0] * 20 + [30.0] * (count - 20),
        pv=pv_arc(SCENARIO_START, count, 8000.0),
        load=[500.0] * count,
        feed_in=15.0,
        vehicles=[car],
    )
    if allowed is None:
        allowed = grid_allowed([car], inputs.slots, ["tesla"])["tesla"]
    vehicles = [car.model_copy(update={"grid_allowed_kwh": allowed})]
    return inputs.model_copy(update={"vehicles": vehicles})


def test_rule_r2_waits_for_pv() -> None:
    inputs = _r2_day(allowed=None)
    assert inputs.vehicles[0].grid_allowed_kwh == 0.0  # P25 deckt den Bedarf
    result = solve(inputs)
    assert result.status == "optimal"
    assert result.shortfall_kwh["tesla"] == pytest.approx(0.0, abs=1e-6)
    assert car_kwh(result) == pytest.approx(10.0)
    assert sum(f.ev["tesla"][1] for f in result.flows) * SLOT_KWH == pytest.approx(0.0, abs=1e-6)
    assert result.r2_excess_kwh == {"tesla": pytest.approx(0.0, abs=1e-6)}
    # ohne Regel R2 lädt es billig in der Nacht und speist die PV ein
    free = solve(_r2_day(allowed=10.0))
    assert sum(car_w(f) for f in free.flows[:20]) * SLOT_KWH == pytest.approx(10.0)


def test_rule_r2_gives_way_to_the_target() -> None:
    # Kein PV, laut R2 nur 1 kWh aus dem Netz: Das Ziel geht vor, 4 kWh überschreiten R2 (rho)
    car = planned_car(departure_at=slot(4), need_kwh=5.0, grid_allowed_kwh=1.0)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, vehicles=[car]))
    assert result.status == "optimal"
    assert car_kwh(result) == pytest.approx(5.0)
    assert result.shortfall_kwh["tesla"] == pytest.approx(0.0, abs=1e-6)
    assert result.r2_excess_kwh["tesla"] == pytest.approx(4.0)


def test_infeasible_target_yields_shortfall_not_infeasible() -> None:
    car = planned_car(departure_at=slot(2), need_kwh=40.0)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, vehicles=[car]))
    assert result.status == "optimal" and result.flows
    full = 2 * 11040.0 * SLOT_KWH
    assert result.shortfall_kwh["tesla"] == pytest.approx(40.0 - full)
    assert [car_w(f) for f in result.flows[:2]] == [pytest.approx(11040.0)] * 2
    assert "tesla" not in result.flows[2].ev and "tesla" not in result.flows[3].ev


def test_no_battery_to_car_and_no_grid_through_car_into_battery() -> None:
    # Teurer Bezug, voller Akku: Der Akku deckt nur das Haus, das Auto lädt aus dem Netz.
    car = planned_car(departure_at=slot(4), need_kwh=5.0)
    expensive = scenario(
        prices=[40.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, soc_pct=90.0, vehicles=[car]
    )
    result = solve(expensive)
    assert car_kwh(result) == pytest.approx(5.0)
    for f in result.flows:
        assert f.bt_h <= 500.0 + 1e-6
        assert f.ev["tesla"][0] == pytest.approx(0.0, abs=1e-6)
    # Billiges Netz, wertvoller Akku, Netzladen aus: Netz ins Auto und PV in den Akku wäre
    # Netzladen am Schalter vorbei. 11 kWh in 4 Slots heißt: jeder Slot lädt aus dem Netz.
    car = planned_car(departure_at=slot(4), need_kwh=11.0)
    cheap = scenario(
        prices=[5.0] * 4,
        pv=[6000.0] * 4,
        load=[500.0] * 4,
        soc_pct=30.0,
        end_value_ct=40.0,
        vehicles=[car],
    )
    result = solve(cheap)
    assert car_kwh(result) == pytest.approx(11.0)
    for f in result.flows:
        assert f.ev["tesla"][1] > 1
        assert f.pv_b + f.pv_x == pytest.approx(0.0, abs=1e-6)
        assert f.ev["tesla"][0] == pytest.approx(5500.0)


def test_car_charges_no_more_than_its_need() -> None:
    # Viel PV ohne Einspeisepreis, Akku voll: PV ins Auto wäre gleich viel wert wie Abregeln
    def charged(need: float) -> OptimizerResult:
        car = planned_car(departure_at=slot(4), need_kwh=need)
        inputs = scenario(
            prices=[20.0] * 4, pv=[12000.0] * 4, load=[500.0] * 4, soc_pct=100.0, vehicles=[car]
        )
        return solve(inputs)

    assert car_kwh(charged(2.0)) == pytest.approx(2.0)
    # Unter einem Slot mit Mindestleistung (3450 W): ein Slot mit Mindestleistung, keine Fehlmenge
    small = charged(0.5)
    assert sorted(car_w(f) for f in small.flows) == pytest.approx([0.0, 0.0, 0.0, 3450.0])
    assert small.shortfall_kwh["tesla"] == pytest.approx(0.0, abs=1e-6)
    # Ohne Bedarf lädt es nicht
    done = charged(0.0)
    assert car_kwh(done) == pytest.approx(0.0, abs=1e-6)
    assert done.shortfall_kwh == {"tesla": pytest.approx(0.0, abs=1e-6)}


def _with_previous_car_power(inputs: PlanInputs, power_w: float) -> PlanInputs:
    slots = [
        s.model_copy(update={"prev_ev_w": {"tesla": power_w}}) if i < 4 else s
        for i, s in enumerate(inputs.slots)
    ]
    return inputs.model_copy(update={"slots": slots})


def test_inertia_holds_the_car_power_of_the_previous_plan() -> None:
    # Später 1 ct billiger: ohne Trägheit verschiebt sich das Laden, mit Trägheit bleibt es
    car = planned_car(departure_at=slot(8), need_kwh=4 * 11040.0 * SLOT_KWH)
    base: dict[str, Any] = {
        "prices": [20.0] * 4 + [19.0] * 4,
        "pv": [0.0] * 8,
        "load": [500.0] * 8,
        "vehicles": [car],
    }
    held = solve(_with_previous_car_power(scenario(**base, inertia_ct=100.0), 11040.0))
    assert [car_w(f) for f in held.flows[:4]] == [pytest.approx(11040.0)] * 4
    moved = solve(_with_previous_car_power(scenario(**base, inertia_ct=0.0), 11040.0))
    assert [car_w(f) for f in moved.flows[4:]] == [pytest.approx(11040.0)] * 4


def test_without_cars_results_have_no_car_entries() -> None:
    result = solve(scenario(prices=[20.0] * 4, pv=[3000.0] * 4, load=[500.0] * 4))
    assert result.shortfall_kwh == {} and result.r2_excess_kwh == {}
    assert all(f.ev == {} for f in result.flows)


# Erwartete Autos (Plan 5b, Präzisierung 10, Ruling T10-2)


def test_expected_car_charges_only_after_arrival() -> None:
    # Billig nur vor der Ankunft: Angesteckt lüde das Auto dort, erwartet erst ab dem Slot 4
    base: dict[str, Any] = {"prices": [5.0] * 4 + [20.0] * 4, "pv": [0.0] * 8, "load": [500.0] * 8}
    car = planned_car(departure_at=slot(8), need_kwh=5.0, expected=True, arrival_at=slot(4))
    result = solve(scenario(**base, vehicles=[car]))
    assert result.status == "optimal"
    assert all("tesla" not in f.ev for f in result.flows[:4])  # e = 0 vor arrival_at
    assert car_kwh(result) == pytest.approx(5.0)
    assert result.shortfall_kwh == {"tesla": pytest.approx(0.0, abs=1e-6)}
    plugged = car.model_copy(update={"expected": False, "arrival_at": None})
    early = solve(scenario(**base, vehicles=[plugged]))
    assert sum(car_w(f) for f in early.flows[:4]) * SLOT_KWH == pytest.approx(5.0)


def test_expected_car_short_of_time_after_arrival_keeps_a_shortfall() -> None:
    # Zwei Slots zwischen Ankunft und Abfahrt reichen nicht: Fehlmenge statt „infeasible“
    car = planned_car(departure_at=slot(8), need_kwh=10.0, expected=True, arrival_at=slot(6))
    result = solve(scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, vehicles=[car]))
    assert result.status == "optimal"
    full = 2 * 11040.0 * SLOT_KWH
    assert result.shortfall_kwh["tesla"] == pytest.approx(10.0 - full)
    assert [car_w(f) for f in result.flows[6:]] == [pytest.approx(11040.0)] * 2
    assert all("tesla" not in f.ev for f in result.flows[:6])


def test_expected_car_shares_night_with_battery() -> None:
    # Nacht billig, Abend teuer: Der Akku lädt aus dem Netz. Kommt in der Nacht ein Auto (ab
    # dem Slot 2, Abfahrt nach dem Slot 3), teilt er die Bezugsgrenze mit ihm und lädt weniger.
    base: dict[str, Any] = {
        "prices": [10.0] * 4 + [40.0] * 8,
        "pv": [0.0] * 12,
        "load": [500.0] * 4 + [3000.0] * 8,
        "soc_pct": 20.0,
        "grid_charge": True,
        "battery": state(grid_max_import_w=12000.0),
    }
    alone = solve(scenario(**base))
    car = planned_car(departure_at=slot(4), need_kwh=5.0, expected=True, arrival_at=slot(2))
    shared = solve(scenario(**base, vehicles=[car]))
    assert alone.status == shared.status == "optimal"
    assert car_kwh(shared) == pytest.approx(5.0)
    assert all("tesla" not in f.ev for f in shared.flows[:2])
    for f in shared.flows[2:4]:
        assert f.gr_h + f.gr_b + f.ev["tesla"][1] <= 12000.0 + 1e-6
    battery_kwh = [sum(f.gr_b for f in r.flows) * SLOT_KWH for r in (alone, shared)]
    assert battery_kwh[1] < battery_kwh[0] - 1.0


def test_expected_car_does_not_raise_the_peak() -> None:
    # Ruling F-1: Die Spitze geht ins Budget des Wächters; die Netzleistung eines erwarteten Autos
    # zählt darin nicht, die eines angesteckten schon.
    base: dict[str, Any] = {
        "prices": [10.0] * 8,
        "pv": [0.0] * 8,
        "load": [500.0] * 8,
        "peak_months": [month("2026-10", 2.0)],
    }
    car = planned_car(need_kwh=10.0, expected=True, arrival_at=slot(5))
    result = solve(scenario(**base, vehicles=[car]))
    assert car_kwh(result) > 0
    assert result.peaks_kw == {"2026-10": pytest.approx(2.0)}
    plugged = car.model_copy(update={"expected": False, "arrival_at": None})
    assert solve(scenario(**base, vehicles=[plugged])).peaks_kw["2026-10"] > 2.0


@pytest.mark.slow
def test_two_cars_over_36_hours_solve_in_time() -> None:
    # 36 h ab 16:00 Ortszeit, zwei Tage PV, Netzladen an; Tesla fährt morgen um 07:00, der e-Golf
    # übermorgen um 07:00 (hinter dem Horizont)
    count = 144
    start = "2026-10-16T14:00:00+00:00"
    rng = random.Random(4)  # noqa: S311 - feste Testdaten
    prices = [rng.uniform(5.0, 40.0) for _ in range(count)]
    pv = pv_arc(start, count, 9000.0)
    inputs = scenario(
        prices=prices,
        pv=pv,
        pv_p25=[0.7 * w for w in pv],
        load=[rng.uniform(300.0, 2500.0) for _ in range(count)],
        soc_pct=40.0,
        grid_charge=True,
        start=start,
        feed_in=5.0,
    )
    horizon_end = at(start) + count * SLOT
    egolf_need = need_kwh(20.0, 90.0, 32.0, 0.9)
    egolf_departure = at("2026-10-18T05:00:00+00:00")
    cars = [
        planned_car(soc_pct=20.0, target_soc_pct=90.0),
        planned_car(
            key="egolf",
            name="e-Golf",
            wallbox="evcs",
            soc_pct=20.0,
            target_soc_pct=90.0,
            departure_at=egolf_departure,
            usable_kwh=32.0,
            min_w=6 * 230.0 * 2,
            max_w=16 * 230.0 * 2,
            need_kwh=egolf_need,
            horizon_need_kwh=horizon_need(egolf_need, 7360.0, egolf_departure, horizon_end),
        ),
    ]
    allowed = grid_allowed(cars, inputs.slots, ["tesla", "egolf"])
    cars = [car.model_copy(update={"grid_allowed_kwh": allowed[car.key]}) for car in cars]
    result = solve(inputs.model_copy(update={"vehicles": cars}))
    print(f"144 Slots, zwei Autos: {result.solve_ms} ms, Status {result.status}")
    assert result.status == "optimal"
    assert result.solve_ms < TIME_LIMIT_S * 1000
    assert all(value == pytest.approx(0.0, abs=1e-6) for value in result.shortfall_kwh.values())


# Akku ins Auto, wenn er übrig ist (Plan Akku ins Auto, Entscheidungen 2 und 3)


def _slot_at(start: str, n: int) -> datetime:
    """Beginn des Slots ``n`` eines Szenarios ab ``start``."""
    return at(start) + n * SLOT


def battery_kwh(result: OptimizerResult) -> float:
    """Energie aus dem Akku in alle Autos über den Horizont (Wallbox-Seite)."""
    return sum(sum(f.ev_battery.values()) for f in result.flows) * SLOT_KWH


def _summer_night(*, car_battery: bool) -> PlanInputs:
    """22:00 bis 14:00 Ortszeit im Juni: Akku 95 %, Haus 400 W, das Auto in „Geplant“ fährt um
    07:00 mit 20 kWh Bedarf; Bezug 18 ct, Einspeisung 6 ct, ab 09:00 9 kW PV, die den Akku füllt
    und einspeist."""
    start = "2026-06-20T20:00:00+00:00"
    count, night = 64, 44
    car = planned_car(departure_at=_slot_at(start, 36), need_kwh=20.0)
    return scenario(
        prices=[18.0] * count,
        pv=[0.0] * night + [9000.0] * (count - night),
        load=[400.0] * count,
        soc_pct=95.0,
        feed_in=6.0,
        start=start,
        vehicles=[car],
        car_battery=car_battery,
    )


def _winter_night(*, car_battery: bool, end_value_ct: float = 20.0) -> PlanInputs:
    """22:00 bis 04:00 Ortszeit im Dezember, keine PV: Akku 80 %, Haus 400 W, Bezug 12 ct; das Auto
    braucht 10 kWh bis zum Horizontende."""
    start = "2026-12-15T21:00:00+00:00"
    count = 24
    car = planned_car(departure_at=_slot_at(start, count), need_kwh=10.0)
    return scenario(
        prices=[12.0] * count,
        pv=[0.0] * count,
        load=[400.0] * count,
        soc_pct=80.0,
        end_value_ct=end_value_ct,
        start=start,
        vehicles=[car],
        car_battery=car_battery,
    )


def _grid_then_car(*, car_battery: bool, grid_charge: bool) -> PlanInputs:
    """Vier billige Slots (5 ct) mit Netzladen, dann 40 ct; ein erwartetes Auto kommt erst nach
    den billigen Slots und braucht 5 kWh. Ohne Regel R1 für den Akku ins Auto lohnte sich
    Netz → Akku → Auto."""
    count = 16
    car = planned_car(departure_at=slot(count), need_kwh=5.0, expected=True, arrival_at=slot(4))
    return scenario(
        prices=[5.0] * 4 + [40.0] * (count - 4),
        pv=[0.0] * count,
        load=[500.0] * count,
        soc_pct=20.0,
        grid_charge=grid_charge,
        vehicles=[car],
        car_battery=car_battery,
    )


def test_planned_car_takes_spare_battery_when_pv_refills_it() -> None:
    on = solve(_summer_night(car_battery=True))
    off = solve(_summer_night(car_battery=False))
    assert on.status == off.status == "optimal"
    assert battery_kwh(on) > 5.0
    assert all(not f.ev_battery for f in off.flows)
    assert on.objective_ct is not None and off.objective_ct is not None
    assert on.objective_ct < off.objective_ct
    # Der Akkuanteil zählt zum Bedarf, nicht zu Regel R2
    assert car_kwh(on) + battery_kwh(on) == pytest.approx(20.0)
    assert on.shortfall_kwh == {"tesla": pytest.approx(0.0, abs=1e-6)}


def test_planned_car_gets_no_battery_when_it_does_not_pay() -> None:
    # Restwert 20 ct, Nachtpreis 12 ct: 12 − 2 − 2 < 20 / η_dis
    result = solve(_winter_night(car_battery=True))
    assert result.status == "optimal"
    assert all(sum(f.ev_battery.values()) < EV_ON_W for f in result.flows)
    # Kontrollfall: Ohne Restwert gibt derselbe Akku dem Auto
    assert battery_kwh(solve(_winter_night(car_battery=True, end_value_ct=0.0))) > 5.0


def test_no_battery_to_car_while_grid_energy_is_in_the_battery() -> None:
    inputs = _grid_then_car(car_battery=True, grid_charge=True)
    result = solve(inputs)
    assert result.status == "optimal"
    lo = params_of(inputs).lo_kwh
    charged = [t for t, f in enumerate(result.flows) if f.gr_b >= 100]
    assert charged
    empty = next(
        (
            t
            for t, f in enumerate(result.flows)
            if t > charged[0] and f.soc_end_kwh <= lo + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
        ),
        len(result.flows) - 1,
    )
    assert all(
        sum(result.flows[t].ev_battery.values()) < EV_ON_W for t in range(charged[0], empty + 1)
    )


def test_without_r1_the_grid_energy_would_go_into_the_car(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Kontrollfall zum Test davor: Gilt der Akku immer als leer, fällt die Sperre, und die
    # Netzenergie geht über den Akku ins Auto
    monkeypatch.setattr(optimizer, "R1_EMPTY_TOL_KWH", 100.0)
    result = solve(_grid_then_car(car_battery=True, grid_charge=True))
    assert battery_kwh(result) > 1.0


def test_ev_power_counts_the_battery_share() -> None:
    result = solve(_summer_night(car_battery=True))
    shares = ev_battery_power_w(result)
    assert any(shares)
    for flows, powers, share in zip(result.flows, ev_power_w(result), shares, strict=True):
        for key, (pv, grid) in flows.ev.items():
            battery = flows.ev_battery.get(key, 0.0)
            assert powers.get(key, 0.0) == pytest.approx(pv + grid + battery, abs=EV_ON_W)
            assert share.get(key, 0.0) == pytest.approx(battery, abs=EV_ON_W)


def test_battery_to_car_never_while_pv_goes_to_grid() -> None:
    # Voller Akku ohne Restwert, Einspeisung 20 ct: Akku ins Auto und die PV einspeisen hieße,
    # den Akku ins Netz zu entladen. PV deckt zuerst die Autos.
    car = planned_car(departure_at=slot(8), need_kwh=10.0)
    inputs = scenario(
        prices=[30.0] * 8,
        pv=[4000.0] * 8,
        load=[500.0] * 8,
        soc_pct=100.0,
        feed_in=20.0,
        vehicles=[car],
        car_battery=True,
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert battery_kwh(result) > 1.0
    for f in result.flows:
        if sum(f.ev_battery.values()) >= EV_ON_W:
            assert f.pv_b + f.pv_x == pytest.approx(0.0, abs=1e-6)


# Netzenergie aus der Zeit vor dem Horizont (Fix-Welle F2, Entscheidung 2 über Pläne hinweg)


def _carried(inputs: PlanInputs) -> PlanInputs:
    """Dieselben Eingangsdaten, aber zu Beginn kann Netzenergie im Akku sein."""
    return inputs.model_copy(update={"grid_in_battery_at_start": True})


def _night_then_sun() -> PlanInputs:
    """22:00 Ortszeit im Juni, Akku 40 % (Reserve 20 %), Haus 1 kW: Der Akku ist gegen 04:00 leer.
    Ab 09:00 füllt ihn die PV (9 kW) und speist ein (6 ct); kein Auto."""
    start = "2026-06-20T20:00:00+00:00"
    count, night = 64, 44
    return scenario(
        prices=[18.0] * count,
        pv=[0.0] * night + [9000.0] * (count - night),
        load=[1000.0] * count,
        soc_pct=40.0,
        feed_in=6.0,
        start=start,
    )


def _first_empty(inputs: PlanInputs, result: OptimizerResult) -> int:
    lo = params_of(inputs).lo_kwh
    return next(
        t
        for t, f in enumerate(result.flows)
        if f.soc_end_kwh <= lo + R1_EMPTY_TOL_KWH + _EMPTY_EPS_KWH
    )


def test_grid_energy_at_start_ends_when_the_battery_is_empty() -> None:
    # Ist der Akku einmal leer, ist keine Netzenergie mehr darin: Die PV darf danach einspeisen.
    carried = _carried(_night_then_sun())
    result = solve(carried)
    assert result.status == "optimal"
    empty = _first_empty(carried, result)
    assert empty < 44
    assert any(f.pv_x > 1000.0 for f in result.flows[empty + 1 :])


def test_grid_energy_at_start_never_makes_the_model_infeasible() -> None:
    # Der Akku (95 %) wird vor der PV nicht leer und läuft voll: Einspeisen lässt sich nicht
    # vermeiden. Der Plan speist so wenig wie möglich ein (der Akku ist am Ende voll) und gibt dem
    # Auto nichts; ohne Netzenergie gäbe er ihm über 5 kWh
    # (``test_planned_car_takes_spare_battery_when_pv_refills_it``).
    carried = _carried(_summer_night(car_battery=True))
    result = solve(carried)
    assert result.status == "optimal"
    assert battery_kwh(result) == pytest.approx(0.0, abs=SLOT_KWH * EV_ON_W)
    assert result.flows[-1].soc_end_kwh == pytest.approx(params_of(carried).hi_kwh, abs=0.01)


# Netzgrenze je Phase (Plan Netzgrenze je Phase, Entscheidungen 4 und 5)

PHASE_LIMIT_W = 4370.0  # (20 A − 1 A) · 230 V an der Anlage (Entscheidung 1)


def phase_grid_w(inputs: PlanInputs, flow: SlotFlows, phase: Phase) -> float:
    """Netzbezug auf der Phase im Slot nach Entscheidung 4: Haus und Netzladen des Akkus je ein
    Drittel, jedes Auto sein Netz ins Auto geteilt durch die Anzahl seiner Phasen."""
    cars = sum(
        flow.ev.get(car.key, (0.0, 0.0))[1] * phase_shares(car.grid_phases).get(phase, 0.0)
        for car in inputs.vehicles
    )
    return (flow.gr_h + flow.gr_b + flow.gr_r + flow.gr_s) / 3 + cars


def _tesla(**overrides: Any) -> PlanVehicleInput:
    """Der Tesla auf L1 bis L3 (3 × 5 bis 16 A)."""
    return planned_car(grid_phases=["L1", "L2", "L3"], **overrides)


def _egolf(**overrides: Any) -> PlanVehicleInput:
    """Der e-Golf auf L1 und L2 (2 × 6 bis 16 A)."""
    values: dict[str, Any] = {
        "key": "egolf",
        "name": "e-Golf",
        "wallbox": "evcs",
        "usable_kwh": 38.6,
        "min_w": 6 * 230.0 * 2,
        "max_w": 16 * 230.0 * 2,
        "grid_phases": ["L1", "L2"],
    }
    values.update(overrides)
    return planned_car(**values)


def _two_cars_night(battery: BatteryState) -> PlanInputs:
    """Nacht ohne PV, Haus 600 W, acht Slots, die ersten zwei billig; Tesla und e-Golf brauchen
    je zwei Slots mit voller Leistung."""
    tesla = _tesla(departure_at=slot(8), need_kwh=2 * 11040.0 * SLOT_KWH)
    egolf = _egolf(departure_at=slot(8), need_kwh=2 * 7360.0 * SLOT_KWH)
    return scenario(
        prices=[5.0, 5.0] + [30.0] * 6,
        pv=[0.0] * 8,
        load=[600.0] * 8,
        battery=battery,
        vehicles=[tesla, egolf],
    )


def _both_at_max(result: OptimizerResult) -> list[bool]:
    """Je Slot: Laden Tesla und e-Golf beide mit voller Leistung?"""
    return [
        car_w(f, "tesla") >= 11040.0 - 1 and car_w(f, "egolf") >= 7360.0 - 1 for f in result.flows
    ]


def test_two_cars_stay_under_the_phase_limit() -> None:
    # Beide mit voller Leistung wären auf L1 200 + 3680 + 3680 W, über 4370 W
    inputs = _two_cars_night(state(grid_phase_import_w=PHASE_LIMIT_W))
    result = solve(inputs)
    assert result.status == "optimal"
    for f in result.flows:
        for phase in PHASES:
            assert phase_grid_w(inputs, f, phase) <= PHASE_LIMIT_W + 1
    assert not any(_both_at_max(result))
    assert all(value == pytest.approx(0.0, abs=1e-6) for value in result.shortfall_kwh.values())


def test_phase_limit_leaves_little_room_for_grid_charging() -> None:
    # Der Tesla muss in Slot 0 mit voller Leistung laden (3680 W je Phase), das Haus nimmt 200 W
    # je Phase; für das Netzladen bleiben 3 · 490 W, obwohl der Restwert viel mehr lohnte
    tesla = _tesla(departure_at=slot(1), need_kwh=11040.0 * SLOT_KWH)
    inputs = scenario(
        prices=[5.0, 40.0],
        pv=[0.0, 0.0],
        load=[600.0, 600.0],
        soc_pct=30.0,
        grid_charge=True,
        end_value_ct=30.0,
        battery=state(grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[tesla],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert car_w(result.flows[0]) == pytest.approx(11040.0)
    assert 1000.0 < result.flows[0].gr_b <= 3 * (4370 - 200 - 3680) + 1


def test_house_above_phase_limit_stays_feasible() -> None:
    # 15 kW Haus: 5000 W je Phase, über der Grenze; der Akku steht an der Reserve und hilft nicht
    cars = [_tesla(departure_at=slot(4)), _egolf(departure_at=slot(4), need_kwh=5.0)]
    inputs = scenario(
        prices=[5.0] * 4,
        pv=[0.0] * 4,
        load=[15000.0] * 4,
        soc_pct=20.0,
        grid_charge=True,
        battery=state(grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=cars,
    )
    result = solve(inputs)
    assert result.status == "optimal"
    for f in result.flows:
        assert f.gr_b == 0
        assert car_w(f, "tesla") == pytest.approx(0.0, abs=1e-6)
        assert car_w(f, "egolf") == pytest.approx(0.0, abs=1e-6)
    assert result.shortfall_kwh["tesla"] > 0
    assert result.shortfall_kwh["egolf"] > 0


def test_cars_on_different_phases_share_no_room() -> None:
    # Gast auf L3, e-Golf auf L1/L2: je Phase 200 + 3680 W, beide passen zugleich
    guest = planned_car(
        key="gast",
        name="Gast",
        wallbox="gast",
        min_w=6 * 230.0,
        max_w=16 * 230.0,
        grid_phases=["L3"],
        departure_at=slot(4),
        need_kwh=3680.0 * SLOT_KWH,
    )
    egolf = _egolf(departure_at=slot(4), need_kwh=7360.0 * SLOT_KWH)
    inputs = scenario(
        prices=[5.0, 30.0, 30.0, 30.0],
        pv=[0.0] * 4,
        load=[600.0] * 4,
        battery=state(grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[guest, egolf],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert car_w(result.flows[0], "gast") == pytest.approx(3680.0)
    assert car_w(result.flows[0], "egolf") == pytest.approx(7360.0)


def test_pv_into_two_cars_is_not_limited_by_phases() -> None:
    # 11,4 kW PV-Überschuss, Regel R2 lässt nur PV zu: PV ins Auto zählt nicht zur Phasengrenze
    tesla = _tesla(departure_at=slot(4), need_kwh=8.0, grid_allowed_kwh=0.0)
    egolf = _egolf(departure_at=slot(4), need_kwh=6.0, grid_allowed_kwh=0.0)
    inputs = scenario(
        prices=[30.0] * 4,
        pv=[12000.0] * 4,
        load=[600.0] * 4,
        battery=state(grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[tesla, egolf],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    for f in result.flows:
        assert sum(pv for pv, _ in f.ev.values()) > PHASE_LIMIT_W * 2


def test_without_phase_limit_two_cars_charge_together() -> None:
    # Ohne Grenze (alte Eingangsdaten, Entscheidung 5) laden beide in den billigen Slots zugleich
    result = solve(_two_cars_night(state()))
    assert result.status == "optimal"
    assert any(_both_at_max(result))


# Monatsspitze ohne Leistungspreis (Plan Netzgrenze je Phase, Entscheidung 8)


def _two_cars_flat_night(peaks: list[PeakMonth]) -> PlanInputs:
    """Nacht ohne PV, Haus 600 W, acht Slots zum gleichen Preis, Akku an der Reserve; Tesla und
    e-Golf brauchen je 10 kWh bis zum Ende."""
    tesla = _tesla(departure_at=slot(8), need_kwh=10.0)
    egolf = _egolf(departure_at=slot(8), need_kwh=10.0)
    return scenario(
        prices=[20.0] * 8,
        pv=[0.0] * 8,
        load=[600.0] * 8,
        soc_pct=20.0,
        vehicles=[tesla, egolf],
        peak_months=peaks,
    )


# nötiger Mittelwert des Bezugs in kW: 2 · 10 kWh in 2 h und das Haus
_FLAT_NIGHT_MEAN_KW = (2 * 10.0 + 8 * 600.0 * SLOT_KWH) / (8 * SLOT_H)  # 10,6 kW


def _peak_import_kw(result: OptimizerResult) -> float:
    """Höchster Netzbezug des Plans in kW (Haus, Netzladen und Netz ins Auto)."""
    return max(f.gr_h + f.gr_b + sum(g for _, g in f.ev.values()) for f in result.flows) / 1000


def test_soft_peak_spreads_two_cars() -> None:
    starts = [at(SCENARIO_START)]
    peaks = peak_months(starts, TariffSettings(), 3.0, soft_ct_per_kw=100.0)
    assert [(m.virtual, m.peak0_kw) for m in peaks] == [(True, 3.0)]
    inputs = _two_cars_flat_night(peaks)
    result = solve(inputs)
    assert result.status == "optimal"
    mean_kw = _FLAT_NIGHT_MEAN_KW
    assert mean_kw == pytest.approx(10.6)
    assert _peak_import_kw(result) <= max(3.0, mean_kw) + 0.1
    assert not any(_both_at_max(result))
    assert all(value == pytest.approx(0.0, abs=1e-6) for value in result.shortfall_kwh.values())
    assert result.peaks_kw["2026-10"] <= max(3.0, mean_kw) + 0.1


def test_without_soft_rate_two_cars_charge_as_before() -> None:
    # Satz 0: kein gedachter Monat, der Optimierer rechnet wie ohne Leistungspreis
    peaks = peak_months([at(SCENARIO_START)], TariffSettings(), 3.0, soft_ct_per_kw=0.0)
    assert peaks == []
    result = solve(_two_cars_flat_night(peaks))
    assert result.status == "optimal" and result.peaks_kw == {}
    before = solve(_two_cars_flat_night([]))
    assert result.flows == before.flows
    # Ursache und Wirkung: ohne Satz bleibt die Spitze nicht beim nötigen Mittelwert (beobachtet:
    # beide Autos zugleich mit voller Leistung, 19,0 kW), mit Satz schon (oben)
    assert _peak_import_kw(result) > _FLAT_NIGHT_MEAN_KW + 0.1


# Nachladen auf die Reserve (Plan Netzgrenze je Phase, Entscheidung 9)

_BIG_KWH = 96.0
_RESERVE_KWH = 0.2 * _BIG_KWH


def _below_reserve(
    *,
    soc_pct: float = 16.0,
    grid_charge: bool = False,
    prices: list[float] | None = None,
    pv: list[float] | None = None,
    battery: BatteryState | None = None,
    vehicles: list[PlanVehicleInput] | None = None,
    end_value_ct: float = 0.0,
) -> PlanInputs:
    """Akku 96 kWh, Reserve 20 %, SOC 16 %, Haus 600 W, ohne PV und ohne Restwert; 24 Slots zu
    30 ct, Slot 4 und 5 zu 10 ct. Es fehlen 3,84 kWh bis zur Reserve, gut fünf Slots mit 3 kW."""
    prices = prices if prices is not None else [30.0] * 4 + [10.0] * 2 + [30.0] * 18
    return scenario(
        prices=prices,
        pv=pv if pv is not None else [0.0] * len(prices),
        load=[600.0] * len(prices),
        soc_pct=soc_pct,
        grid_charge=grid_charge,
        battery=battery or state(usable_kwh=_BIG_KWH),
        vehicles=vehicles,
        end_value_ct=end_value_ct,
    )


@pytest.mark.parametrize("end_value_ct", [0.0, 25.0])
def test_recharge_to_reserve_with_switch_off(end_value_ct: float) -> None:
    # Mit Restwert genauso: Nachgeladenes trägt keinen Restwert, der Plan lädt bis zur Reserve
    inputs = _below_reserve(end_value_ct=end_value_ct)
    result = solve(inputs)
    assert result.status == "optimal"
    assert all(f.gr_b == 0 for f in result.flows)
    # genau die Lücke bis zur Reserve, nie darüber
    recharged = sum(params_of(inputs).eta_ch * f.gr_r * SLOT_KWH for f in result.flows)
    assert recharged == pytest.approx(_RESERVE_KWH - 0.16 * _BIG_KWH, abs=0.01)
    assert result.flows[-1].soc_end_kwh == pytest.approx(_RESERVE_KWH, abs=0.01)
    intents = derive_intents(inputs, result)
    charging = [i for i in intents if i.kind is BatteryIntentKind.NETZLADEN]
    assert charging and all(i.reserve_recharge for i in charging)
    assert all(0 < i.grid_charge_w <= RESERVE_RECHARGE_MAX_W for i in charging)
    # die billigen Slots zuerst, mit voller Leistung
    assert [intents[4].grid_charge_w, intents[5].grid_charge_w] == [3000.0, 3000.0]
    steps = resimulate(inputs, intents)
    end_pct = steps[-1].soc_end_kwh / _BIG_KWH * 100
    one_slot_pct = 3000.0 * SLOT_KWH * params_of(inputs).eta_ch / _BIG_KWH * 100
    assert 20.0 - 0.1 <= end_pct <= 20.0 + one_slot_pct
    assert check_plan(inputs, result, intents, steps) == []


def test_recharge_cap_follows_limit_setpoint_and_reserve() -> None:
    inputs = _below_reserve(battery=state(usable_kwh=_BIG_KWH, max_grid_charge_setpoint_w=2600.0))
    assert recharge_cap_w(inputs, 0) == 2000.0  # 2600 W Sollwert − 600 W Haus
    assert recharge_cap_w(_below_reserve(), 0) == RESERVE_RECHARGE_MAX_W
    assert recharge_cap_w(_below_reserve(soc_pct=20.0), 0) == 0.0


def test_no_recharge_at_or_above_reserve() -> None:
    for soc_pct in (20.0, 35.0):
        inputs = _below_reserve(soc_pct=soc_pct)
        assert all(recharge_cap_w(inputs, t) == 0 for t in range(len(inputs.slots)))
        result = solve(inputs)
        assert result.status == "optimal"
        assert all(f.gr_r == 0 and f.gr_b == 0 for f in result.flows)
        assert all(
            i.kind is not BatteryIntentKind.NETZLADEN for i in derive_intents(inputs, result)
        )


@pytest.mark.parametrize("end_value_ct", [0.0, 25.0])
def test_pv_refills_before_grid_recharge(end_value_ct: float) -> None:
    # 24 Slots Nacht, dann 16 Slots mit 4 kW PV: 3,4 kW Überschuss füllen die Reserve umsonst.
    # Auch mit Restwert über dem Preis der billigen Slots lädt der Plan nicht aus dem Netz nach.
    prices = [30.0] * 4 + [10.0] * 2 + [30.0] * 34
    inputs = _below_reserve(prices=prices, pv=[0.0] * 24 + [4000.0] * 16, end_value_ct=end_value_ct)
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_r for f in result.flows) < 1
    assert all(i.kind is not BatteryIntentKind.NETZLADEN for i in derive_intents(inputs, result))
    assert result.flows[-1].soc_end_kwh >= _RESERVE_KWH - 0.01


def test_no_recharge_above_reserve_for_end_value() -> None:
    # Die PV füllt den Akku tagsüber über die Reserve, danach kommt ein Slot zu 5 ct: Ohne
    # Schalter lädt der Plan dort nicht nach, auch wenn der Restwert (25 ct) mehr wert ist.
    prices = [30.0] * 24 + [30.0] * 16 + [5.0] * 4 + [30.0] * 4
    pv = [0.0] * 24 + [8000.0] * 16 + [0.0] * 8
    inputs = _below_reserve(prices=prices, pv=pv, end_value_ct=25.0)
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_r for f in result.flows) < 1
    intents = derive_intents(inputs, result)
    assert not any(i.reserve_recharge for i in intents)
    assert all(i.kind is not BatteryIntentKind.NETZLADEN for i in intents)


@pytest.mark.parametrize("grid_charge", [False, True])
def test_recharge_does_not_block_export_by_r1(grid_charge: bool) -> None:
    # Nachts billig nachladen, mittags ein Slot mit 12 kW PV: 10 kW gehen in den Akku, 1,4 kW
    # ins Netz; das reicht nicht ganz bis zur Reserve, der Rest kommt aus dem Netz davor. Mit der
    # Reserve als Leer-Grenze steht der nachgeladene Akku nicht unter Regel R.
    prices = [30.0] * 4 + [10.0] * 4 + [30.0] * 4 + [8.0] + [40.0] * 7
    pv = [0.0] * 12 + [12000.0] + [0.0] * 7
    inputs = _below_reserve(prices=prices, pv=pv, grid_charge=grid_charge)
    result = solve(inputs)
    assert result.status == "optimal"
    assert result.flows[12].pv_x > 1000
    intents = derive_intents(inputs, result)
    charging = [i for i, intent in enumerate(intents) if intent.kind is BatteryIntentKind.NETZLADEN]
    assert charging and all(i < 12 for i in charging)
    # ohne Schalter nur Nachladen; mit Schalter lädt er dazu für die Slots vor der PV
    assert all(intents[i].reserve_recharge for i in charging) or grid_charge
    steps = resimulate(inputs, intents)
    assert steps[12].export_w > 1000
    for index in charging:
        assert export_while_charged_kwh(steps, index, _RESERVE_KWH) <= R1_TOL_KWH
    assert check_plan(inputs, result, intents, steps) == []
    assert steps[-1].soc_end_kwh >= _RESERVE_KWH - 0.1


def test_recharge_respects_phase_limit() -> None:
    # Der Tesla muss in Slot 0 voll laden (3680 W je Phase), das Haus nimmt 200 W je Phase: Im
    # billigen Slot bleiben fürs Nachladen 3 · 490 W, der Rest kommt später
    tesla = _tesla(departure_at=slot(1), need_kwh=11040.0 * SLOT_KWH)
    inputs = _below_reserve(
        prices=[5.0] + [30.0] * 11,
        battery=state(usable_kwh=_BIG_KWH, grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[tesla],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert car_w(result.flows[0]) == pytest.approx(11040.0)
    assert 1000.0 < result.flows[0].gr_r <= 3 * (4370 - 200 - 3680) + 1
    assert sum(f.gr_r for f in result.flows[1:]) > 3000.0
    for f in result.flows:
        for phase in PHASES:
            assert phase_grid_w(inputs, f, phase) <= PHASE_LIMIT_W + 1


def test_recharged_energy_stays_in_the_battery() -> None:
    # Nachts billig nachladen und morgens teuer entladen wäre Netzladen am Schalter vorbei: Was
    # nachgeladen ist, verlässt den Akku nicht, auch wenn die PV ihn danach wieder füllt.
    prices = [10.0] * 8 + [60.0] * 8 + [30.0] * 24
    pv = [0.0] * 16 + [6000.0] * 24
    inputs = _below_reserve(prices=prices, pv=pv)
    result = solve(inputs)
    assert result.status == "optimal"
    soc0 = 0.16 * _BIG_KWH
    charged = 0.0
    for f in result.flows:
        charged += params_of(inputs).eta_ch * f.gr_r * SLOT_KWH
        assert f.soc_end_kwh >= soc0 + charged - 1e-6


# Unwetter-Reserve (Plan Unwetter-Reserve, Entscheidungen 5 und 6)

_STORM = StormWarning(
    warn_id=7,
    version=1,
    kind="gewitter",
    level=2,
    begin=at("2026-10-16T12:00:00+00:00"),  # 14:00 Ortszeit
    end=at("2026-10-16T14:00:00+00:00"),  # 16:00 Ortszeit
    text="Gewitter",
)
_WINDOW = range(44, 56)  # 13:00 (eine Stunde Vorlauf) bis 16:00 Ortszeit
_FLOOR_KWH = 0.4 * _BIG_KWH
_TOL_KWH = 0.005 * _BIG_KWH  # 0,5 Prozentpunkte


def _storm(
    *,
    soc_pct: float,
    prices: list[float] | None = None,
    load: list[float] | None = None,
    pv: list[float] | None = None,
    grid_charge: bool = False,
    battery: BatteryState | None = None,
    vehicles: list[PlanVehicleInput] | None = None,
    warning: StormWarning | None = _STORM,
    car_battery: bool = False,
) -> PlanInputs:
    """Akku 96 kWh, Reserve 20 %, Ziel 40 %, ohne PV und ohne Restwert; 96 Slots ab 02:00
    Ortszeit, Haus 600 W, 30 ct und 10 ct von 02:00 bis 04:00; mit Warnung Gewitter (orange) von
    14:00 bis 16:00 Ortszeit, die Untergrenzen je Slot wie ``assemble_inputs``."""
    prices = prices if prices is not None else [10.0] * 8 + [30.0] * 88
    count = len(prices)
    starts = [at(SCENARIO_START) + i * SLOT for i in range(count)]
    warnings = [warning] if warning is not None else []
    floors = storm_floors(
        starts, warnings, StormSettings(enabled=True), reserve_pct=20.0, soc_max_pct=100.0
    )
    return scenario(
        prices=prices,
        pv=pv if pv is not None else [0.0] * count,
        load=load if load is not None else [600.0] * count,
        soc_pct=soc_pct,
        grid_charge=grid_charge,
        battery=battery or state(usable_kwh=_BIG_KWH),
        vehicles=vehicles,
        car_battery=car_battery,
        storm_floors=floors,
        storm_warnings=warnings,
    )


def test_storm_floors_lie_in_the_window() -> None:
    inputs = _storm(soc_pct=60.0)
    floors = [s.storm_floor_pct for s in inputs.slots]
    assert [t for t, f in enumerate(floors) if f is not None] == list(_WINDOW)
    assert {f for f in floors if f is not None} == {40.0}


def test_holds_battery_for_storm_window() -> None:
    # Haus 2500 W in teuren Blöcken: ohne Warnung entlädt der Plan vor dem Fenster weit unter
    # 40 % und am Ende bis 20 %. Mit Warnung hält er im Fenster 40 %; was er vor dem Fenster
    # entladen darf (60 % → 40 %), nimmt er in den teuren Slots ganz und rundet nur in einem
    # 600-W-Slot im Fenster, damit die Nachrechnung um weniger als 0,5 Prozentpunkte abweicht.
    prices = [10.0] * 8 + [30.0] * 28 + [22.0] * 8 + [28.0] * 12 + [21.0] * 40
    load = [600.0] * 8 + [2500.0] * 28 + [2500.0] * 8 + [600.0] * 12 + [2500.0] * 40
    without = solve(_storm(soc_pct=60.0, prices=prices, load=load, warning=None))
    assert without.status == "optimal"
    assert min(without.flows[t].soc_end_kwh for t in _WINDOW) < _FLOOR_KWH - 3.0
    assert without.flows[-1].soc_end_kwh == pytest.approx(_RESERVE_KWH, abs=0.01)

    inputs = _storm(soc_pct=60.0, prices=prices, load=load)
    result = solve(inputs)
    assert result.status == "optimal"
    assert result.storm_shortfall_kwh == pytest.approx(0.0, abs=1e-6)
    assert all(f.gr_s == 0 for f in result.flows)  # SOC₀ über dem Ziel: kein Aufladen
    for t in _WINDOW:
        assert result.flows[t].soc_end_kwh >= _FLOOR_KWH - 1e-6
    assert result.flows[-1].soc_end_kwh == pytest.approx(_RESERVE_KWH, abs=0.01)
    intents = derive_intents(inputs, result)
    steps = resimulate(inputs, intents)
    for t in _WINDOW:
        assert steps[t].soc_end_kwh >= _FLOOR_KWH - _TOL_KWH
    assert check_plan(inputs, result, intents, steps) == []


def test_storm_charge_with_switch_off() -> None:
    inputs = _storm(soc_pct=22.0)
    result = solve(inputs)
    assert result.status == "optimal"
    assert all(f.gr_b == 0 and f.gr_r == 0 for f in result.flows)
    charged = sum(params_of(inputs).eta_ch * f.gr_s * SLOT_KWH for f in result.flows)
    assert charged == pytest.approx(_FLOOR_KWH - 0.22 * _BIG_KWH, abs=0.01)
    # die Untergrenze gilt am Ende jedes Slots ab 13:00 (eine Stunde Vorlauf)
    assert all(result.flows[t].soc_end_kwh >= _FLOOR_KWH - 1e-6 for t in _WINDOW)
    intents = derive_intents(inputs, result)
    charging = [t for t, i in enumerate(intents) if i.kind is BatteryIntentKind.NETZLADEN]
    assert charging and all(intents[t].reserve_recharge for t in charging)
    assert all(0 < intents[t].grid_charge_w <= RESERVE_RECHARGE_MAX_W for t in charging)
    # zuerst die billigen Slots (02:00 bis 04:00), mit voller Leistung
    assert [intents[t].grid_charge_w for t in range(8)] == [RESERVE_RECHARGE_MAX_W] * 8
    assert max(charging) <= _WINDOW[-1]
    steps = resimulate(inputs, intents)
    for t in _WINDOW:
        assert steps[t].soc_end_kwh >= _FLOOR_KWH - _TOL_KWH
    assert check_plan(inputs, result, intents, steps) == []


def test_storm_charge_caps_follow_window_and_soc() -> None:
    caps = storm_charge_caps_w(_storm(soc_pct=22.0))
    assert caps == [RESERVE_RECHARGE_MAX_W] * 56 + [0.0] * 40
    assert storm_charge_caps_w(_storm(soc_pct=40.0)) == [0.0] * 96
    assert storm_charge_caps_w(_storm(soc_pct=22.0, warning=None)) == [0.0] * 96
    limited = _storm(
        soc_pct=22.0, battery=state(usable_kwh=_BIG_KWH, max_grid_charge_setpoint_w=2600.0)
    )
    assert storm_charge_caps_w(limited)[0] == 2000.0  # 2600 W Sollwert − 600 W Haus


# billig in der Nacht (02:00 bis 04:00), teuer am Morgen vor der PV (04:00 bis 08:00), sonst 30 ct
_MORNING_PRICES = [10.0] * 8 + [40.0] * 16 + [30.0] * 72


def test_pv_fills_storm_reserve_first() -> None:
    # Haus 1500 W und ein teurer Morgen: Nachts aufladen und den Akku am Morgen leeren lohnte
    # sich, wenn das Aufgeladene vor dem Fenster wieder hinaus dürfte; die PV füllt die
    # Untergrenze umsonst
    pv = pv_arc(SCENARIO_START, 96, 8000.0)
    inputs = _storm(soc_pct=22.0, pv=pv, prices=_MORNING_PRICES, load=[1500.0] * 96)
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_s for f in result.flows) < 1
    intents = derive_intents(inputs, result)
    assert all(i.kind is not BatteryIntentKind.NETZLADEN for i in intents)
    steps = resimulate(inputs, intents)
    assert all(steps[t].soc_end_kwh >= _FLOOR_KWH - _TOL_KWH for t in _WINDOW)


def test_storm_charge_is_no_arbitrage() -> None:
    # Ruling R9 (Befund des Reviews): Ohne die Bedingung „bleibt im Akku“ lud der Plan nachts
    # 1,9 kWh zu 10 ct, entlud sie am Morgen zu 40 ct und war um 06:00 wieder an der Reserve; die
    # PV füllte die Untergrenze trotzdem (Ziel 80,8 ct mit Warnung gegen 125,1 ct ohne).
    pv = pv_arc(SCENARIO_START, 96, 9000.0)
    kw: dict[str, Any] = {"soc_pct": 22.0, "pv": pv, "prices": _MORNING_PRICES}
    kw["load"] = [1500.0] * 96
    without = solve(_storm(**kw, warning=None))
    inputs = _storm(**kw)
    result = solve(inputs)
    assert result.status == without.status == "optimal"
    assert result.objective_ct is not None and without.objective_ct is not None
    assert result.objective_ct >= without.objective_ct - 0.01
    assert sum(f.gr_s for f in result.flows) < 1
    intents = derive_intents(inputs, result)
    assert all(i.kind is not BatteryIntentKind.NETZLADEN for i in intents)
    assert result.storm_shortfall_kwh == pytest.approx(0.0, abs=1e-6)


def test_storm_charge_stays_until_the_window_ends() -> None:
    # Aufgeladenes verlässt den Akku nicht vor dem Ende des letzten Fensters: SOC am Slotende
    # mindestens Reserve plus das bis dahin Aufgeladene; danach darf das Haus es verbrauchen
    inputs = _storm(soc_pct=22.0, prices=_MORNING_PRICES, load=[1500.0] * 96)
    result = solve(inputs)
    assert result.status == "optimal"
    eta_ch = params_of(inputs).eta_ch
    charged = 0.0
    for t, f in enumerate(result.flows[: _WINDOW[-1] + 1]):
        charged += eta_ch * f.gr_s * SLOT_KWH
        assert f.soc_end_kwh >= _RESERVE_KWH + charged - 1e-6, t
    assert charged == pytest.approx(_FLOOR_KWH - 0.22 * _BIG_KWH, abs=0.01)
    assert result.flows[-1].soc_end_kwh < _FLOOR_KWH - 5.0  # danach nimmt es das Haus


def test_storm_charge_boundary_between_reserve_and_floor() -> None:
    # Ruling I1: Liegt der SOC zu Beginn zwischen Reserve und Untergrenze, hält der Plan das
    # Aufgeladene nur über ``min(SOC₀, Reserve)``, nicht über dem SOC₀. Die Probe: nachts 5,6 kWh
    # zu 10 ct aufladen (höchstens 3 kW, höchstens bis zur Untergrenze), und der Akku gibt vor dem
    # Fenster Energie über der Reserve ans Haus ab (SOC fällt unter SOC₀ + Aufgeladenes), die PV
    # und das Aufladen füllen ihn zum Fenster auf.
    inputs = _storm(
        soc_pct=30.0,
        prices=_MORNING_PRICES,
        load=[1500.0] * 96,
        pv=pv_arc(SCENARIO_START, 96, 2500.0),
    )
    soc0 = 0.30 * _BIG_KWH
    result = solve(inputs)
    assert result.status == "optimal"
    eta_ch = params_of(inputs).eta_ch
    charged = 0.0
    gave_house = False
    for t, f in enumerate(result.flows[: _WINDOW[-1] + 1]):
        assert f.gr_s + f.gr_r <= RESERVE_RECHARGE_MAX_W + 1e-6, t
        charged += eta_ch * f.gr_s * SLOT_KWH
        assert f.soc_end_kwh >= _RESERVE_KWH + charged - 1e-6, t  # die Basis ist die Reserve
        if t < _WINDOW[0] and f.soc_end_kwh < soc0 + charged - 0.5:
            gave_house = True
    assert 1.0 < charged <= _FLOOR_KWH - soc0 + 1e-6  # nie mehr als bis zur Untergrenze
    assert gave_house  # Entladung über der Reserve vor dem Fenster ist erlaubt
    assert all(result.flows[t].soc_end_kwh >= _FLOOR_KWH - _TOL_KWH for t in _WINDOW)


def test_no_battery_to_car_with_storm_charge() -> None:
    # Ruling R7: Kann der Plan für die Unwetter-Reserve aufladen, gibt der Akku keinem Auto etwas;
    # die Plausibilität zählt das Aufladen als Netzenergie im Akku (``car_grid``). Ohne Warnung
    # nimmt das Auto, das erst am Abend kommt, PV-Energie aus dem Akku.
    pv = pv_arc(SCENARIO_START, 96, 9000.0)
    car = _tesla(expected=True, arrival_at=slot(72), departure_at=slot(95), need_kwh=10.0)
    kw: dict[str, Any] = {"soc_pct": 22.0, "pv": pv, "vehicles": [car], "car_battery": True}
    without = solve(_storm(**kw, warning=None))
    assert battery_kwh(without) > 1.0
    inputs = _storm(**kw)
    result = solve(inputs)
    assert result.status == "optimal"
    assert all(not f.ev_battery for f in result.flows)
    assert sum(f.gr_s for f in result.flows) > 0 or result.flows[_WINDOW[0]].pv_b > 0
    intents = derive_intents(inputs, result)
    issues = check_plan(inputs, result, intents, resimulate(inputs, intents))
    assert not any("Akku ins Auto" in i for i in issues)


def test_battery_to_car_stays_with_floor_above_the_soc() -> None:
    # Liegt der SOC zu Beginn schon über der Untergrenze, gibt es kein Aufladen und Akku ins
    # Auto bleibt wie ohne Warnung möglich
    pv = pv_arc(SCENARIO_START, 96, 9000.0)
    car = _tesla(expected=True, arrival_at=slot(72), departure_at=slot(95), need_kwh=10.0)
    result = solve(_storm(soc_pct=60.0, pv=pv, vehicles=[car], car_battery=True))
    assert result.status == "optimal"
    assert battery_kwh(result) > 1.0


def test_storm_already_running_stays_feasible() -> None:
    # Die Warnung läuft schon, der Akku steht unter der Reserve: Das Ziel ist nicht zu erreichen,
    # der Plan bleibt lösbar, lädt so schnell wie erlaubt und meldet die Fehlmenge.
    running = _STORM.model_copy(
        update={"begin": at(SCENARIO_START), "end": at(SCENARIO_START) + 16 * SLOT}
    )
    inputs = _storm(soc_pct=18.0, prices=[30.0] * 96, warning=running)
    assert inputs.slots[0].storm_floor_pct == 40.0
    result = solve(inputs)
    assert result.status == "optimal"
    for f in result.flows:
        assert f.gr_r + f.gr_s <= RESERVE_RECHARGE_MAX_W + 1e-6
    assert result.storm_shortfall_kwh > 0
    assert sum(f.gr_r + f.gr_s for f in result.flows[:16]) == pytest.approx(
        16 * RESERVE_RECHARGE_MAX_W, rel=1e-6
    )
    intents = derive_intents(inputs, result)
    charging = [i for i in intents if i.kind is BatteryIntentKind.NETZLADEN]
    assert charging and all(
        i.reserve_recharge and i.grid_charge_w <= RESERVE_RECHARGE_MAX_W for i in charging
    )
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []


def test_no_storm_charge_after_window() -> None:
    # Nach dem Fenster wäre es billiger, aber dort gibt es kein Aufladen für die Reserve mehr
    prices = [30.0] * 56 + [2.0] * 40
    inputs = _storm(soc_pct=22.0, prices=prices)
    result = solve(inputs)
    assert result.status == "optimal"
    assert all(f.gr_s == 0 for f in result.flows[_WINDOW[-1] + 1 :])
    assert sum(f.gr_s for f in result.flows[: _WINDOW[0]]) > 0
    assert all(f.gr_b == 0 for f in result.flows)


def test_storm_charge_respects_phase_limit() -> None:
    # Der Tesla muss im billigen Slot 0 voll laden (3680 W je Phase), das Haus nimmt 200 W je
    # Phase: Fürs Aufladen bleiben dort 3 · 490 W, der Rest kommt später.
    tesla = _tesla(departure_at=slot(1), need_kwh=11040.0 * SLOT_KWH)
    inputs = _storm(
        soc_pct=22.0,
        prices=[5.0] + [30.0] * 95,
        battery=state(usable_kwh=_BIG_KWH, grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[tesla],
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert car_w(result.flows[0]) == pytest.approx(11040.0)
    assert 1000.0 < result.flows[0].gr_s <= 3 * (4370 - 200 - 3680) + 1
    for f in result.flows:
        for phase in PHASES:
            assert phase_grid_w(inputs, f, phase) <= PHASE_LIMIT_W + 1


def test_storm_charge_counts_in_the_known_peak() -> None:
    # Mit einem erwarteten Auto rechnet die Spitze den Bezug selbst nach; das Aufladen zählt mit.
    expected = _tesla(
        key="egolf", expected=True, arrival_at=slot(90), departure_at=slot(95), need_kwh=1.0
    )
    inputs = _storm(soc_pct=22.0, vehicles=[expected]).model_copy(
        update={"peak_months": [month("2026-10", 0.0)]}
    )
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_s for f in result.flows) > 0
    top_w = max(f.gr_h + f.gr_s for f in result.flows)
    assert top_w > 700.0  # mehr als das Haus allein
    assert result.peaks_kw["2026-10"] >= top_w / 1000 - 1e-6


def _old_inputs() -> PlanInputs:
    """Nachladen auf die Reserve, ein Auto, Phasengrenze, PV und Restwert, ohne Warnung."""
    prices = [30.0] * 4 + [10.0] * 8 + [30.0] * 36 + [8.0] * 8 + [40.0] * 40
    car = _tesla(departure_at=slot(20))
    return scenario(
        prices=prices,
        pv=pv_arc(SCENARIO_START, len(prices), 1500.0),
        load=[600.0] * len(prices),
        soc_pct=16.0,
        battery=state(usable_kwh=_BIG_KWH, grid_phase_import_w=PHASE_LIMIT_W),
        vehicles=[car],
        end_value_ct=20.0,
        feed_in=6.0,
    )


def test_old_inputs_without_storm_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gespeicherte Eingangsdaten ohne die Felder der Unwetter-Reserve lösen wie vorher: dasselbe
    # Ziel wie vor der Unwetter-Reserve (gemessen auf 1c45f41), und das Modell hat keine Variable
    # und keine Bedingung dafür.
    data = _old_inputs().model_dump(mode="json")
    assert data.pop("storm_warnings") == []
    assert data["planning"].pop("storm") is not None
    for item in data["slots"]:
        assert item.pop("storm_floor_pct") is None
    inputs = PlanInputs.model_validate(data)
    seen: list[pulp.LpProblem] = []
    original = pulp.LpProblem.solve

    def record(self: pulp.LpProblem, *args: Any, **kwargs: Any) -> Any:
        seen.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pulp.LpProblem, "solve", record)
    result = solve(inputs)
    assert result.status == "optimal"
    assert result.objective_ct == pytest.approx(382.9000590712005, abs=1e-6)
    assert sum(f.gr_r for f in result.flows) > 1000.0
    assert all(f.gr_s == 0 for f in result.flows) and result.storm_shortfall_kwh == 0.0
    names = [v.name for v in seen[0].variables()] + [c.name for c in seen[0].constraints()]
    assert not [n for n in names if "unwetter" in n or n.startswith(("gr_s", "fehl_s"))]
