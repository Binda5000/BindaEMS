"""Tests des Optimierers für den Akku (Präzisierungen 5 und 6): Netzladen mit Marge und Schalter,
Regel R1, Reserve, Leistungspreis je Monat, Planträgheit und Fehler des Solvers."""

from __future__ import annotations

import random
from typing import Any, NoReturn

import pulp
import pytest
from tests.plan_helpers import month, scenario, small_battery

from bindaems.app.planning.inputs import PeakMonth, PlanInputs
from bindaems.app.planning.optimizer import grid_charge_cap_w, solve


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


def test_r1_blocks_grid_charge_before_pv_surplus() -> None:
    # 10-kWh-Akku, 5 kW Ladeleistung: 8,5 kW Überschuss erzwingen später Einspeisung
    kw: dict[str, Any] = {"battery": small_battery(), "soc_pct": 20.0, "grid_charge": True}
    blocked = scenario(
        prices=[5.0] * 4 + [40.0] * 4 + [8.0] * 8,
        pv=[0.0] * 8 + [9000.0] * 8,
        load=[500.0] * 4 + [3000.0] * 4 + [500.0] * 8,
        **kw,
    )
    free = scenario(
        prices=[5.0] * 4 + [40.0] * 4 + [8.0] * 8,
        pv=[0.0] * 16,
        load=[500.0] * 4 + [3000.0] * 4 + [500.0] * 8,
        **kw,
    )
    assert all(f.gr_b < 1 for f in solve(blocked).flows)
    assert sum(f.gr_b for f in solve(free).flows[:4]) > 0


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
