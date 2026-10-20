"""Tests des Optimierers für den Akku (Präzisierungen 5 und 6): Netzladen mit Marge und Schalter,
Regel R1, Reserve, Leistungspreis je Monat, Planträgheit und Fehler des Solvers; dazu die geplanten
Autos (Plan 4b, Präzisierung 11): günstigste Slots, Vorrang vor dem Akku, Fehlmenge, Regel R2 und
Trägheit."""

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
    scenario,
    small_battery,
)

from bindaems.app.planning import optimizer
from bindaems.app.planning.battery_model import SLOT_H, params_of
from bindaems.app.planning.inputs import PeakMonth, PlanInputs
from bindaems.app.planning.optimizer import (
    R1_EMPTY_TOL_KWH,
    TIME_LIMIT_S,
    OptimizerResult,
    SlotFlows,
    grid_charge_cap_w,
    solve,
)
from bindaems.app.planning.vehicles import grid_allowed, horizon_need, need_kwh
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
