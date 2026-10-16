"""Tests des Akkumodells im Viertelstundenraster (Präzisierung 8)."""

from __future__ import annotations

import math

import pytest
from tests.plan_helpers import at

from bindaems.app.planning.battery_model import (
    SLOT_H,
    BatteryParams,
    SimSlot,
    eta,
    simulate,
    step,
)
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind

T0 = at("2026-10-16T10:00:00+00:00")
P = BatteryParams(capacity_kwh=10.0, lo_kwh=2.0, hi_kwh=10.0, eta_ch=0.9, eta_dis=0.9)
EV, HOLD = BatteryIntent(), BatteryIntent(kind=BatteryIntentKind.HALTEN)


def grid(w: float) -> BatteryIntent:
    return BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=w)


def slot(pv: float, load: float, ct: float = 20.0, fi: float | None = 5.0) -> SimSlot:
    return SimSlot(T0, pv, load, ct, fi, 5000.0, 5000.0)


def test_self_consumption_charges_surplus() -> None:
    s = step(5.0, slot(3000, 1000), EV, P)
    assert (s.charge_pv_w, s.export_w, s.import_w) == (2000, 0, 0)
    assert s.soc_end_kwh == pytest.approx(5.45)  # 0,25 h · 2 kW · 0,9


def test_self_consumption_discharges_deficit() -> None:
    s = step(5.0, slot(0, 1000), EV, P)
    assert (s.discharge_w, s.import_w) == (1000, 0)
    assert s.soc_end_kwh == pytest.approx(5.0 - 0.25 / 0.9)


def test_hold_does_not_discharge_but_charges_from_pv() -> None:
    assert step(5.0, slot(0, 1000), HOLD, P).import_w == 1000
    assert step(5.0, slot(3000, 1000), HOLD, P).charge_pv_w == 2000


def test_grid_charge_adds_power_and_covers_deficit_from_grid() -> None:
    s = step(5.0, slot(0, 1000), grid(3000), P)
    assert (s.charge_grid_w, s.discharge_w, s.import_w) == (3000, 0, 4000)
    assert s.soc_end_kwh == pytest.approx(5.675)


def test_grid_charge_shares_the_charge_limit_with_pv() -> None:
    s = step(5.0, slot(5000, 1000), grid(3000), P)
    assert (s.charge_pv_w, s.charge_grid_w, s.import_w) == (4000, 1000, 1000)


def test_charge_limited_by_room() -> None:
    s = step(9.9, slot(6000, 1000), EV, P)
    assert s.charge_pv_w == pytest.approx(0.1 / 0.9 / 0.25 * 1000)
    assert s.soc_end_kwh == pytest.approx(10.0)


def test_soc_below_reserve_never_discharges() -> None:
    s = step(1.5, slot(0, 1000), EV, P)
    assert (s.discharge_w, s.import_w, s.soc_end_kwh) == (0, 1000, 1.5)


def test_cost_counts_import_and_feed_in() -> None:
    assert step(10.0, slot(3000, 1000, ct=30.0, fi=6.0), EV, P).cost_ct == pytest.approx(
        -0.25 * 2 * 6.0
    )
    assert step(2.0, slot(0, 1000, fi=None), EV, P).cost_ct == pytest.approx(0.25 * 1 * 20.0)


def test_simulate_chains_soc() -> None:
    steps = simulate(5.0, [slot(0, 1000)] * 2, [EV, EV], P)
    assert steps[1].soc_start_kwh == steps[0].soc_end_kwh


def test_eta_is_square_root_of_roundtrip() -> None:
    assert eta(81.0) == pytest.approx(0.9)
    assert eta(100.0) == 1.0
    assert SLOT_H == 0.25


def test_battery_w_is_net_charge() -> None:
    s = step(5.0, slot(5000, 1000), grid(3000), P)
    assert s.battery_w == pytest.approx(s.charge_pv_w + s.charge_grid_w - s.discharge_w)
    d = step(5.0, slot(0, 1000), EV, P)
    assert d.battery_w == pytest.approx(-1000)


def test_discharge_limited_by_power_and_energy_over_reserve() -> None:
    limited = SimSlot(T0, 0, 4000, 20.0, 5.0, 5000.0, 1500.0)
    s = step(8.0, limited, EV, P)
    assert (s.discharge_w, s.import_w) == (1500, 2500)
    near_reserve = step(2.1, slot(0, 4000), EV, P)  # nur 0,1 kWh über der Reserve
    assert near_reserve.discharge_w == pytest.approx(0.1 * 0.9 / 0.25 * 1000)
    assert near_reserve.soc_end_kwh == pytest.approx(2.0)
    assert near_reserve.import_w == pytest.approx(4000 - near_reserve.discharge_w)


def test_charge_limited_by_power_and_surplus_exported() -> None:
    limited = SimSlot(T0, 6000, 0, 20.0, 5.0, 2500.0, 5000.0)
    s = step(5.0, limited, EV, P)
    assert (s.charge_pv_w, s.export_w) == (2500, 3500)


def test_grid_charge_limited_by_room() -> None:
    s = step(9.9, slot(0, 1000), grid(3000), P)
    assert s.charge_grid_w == pytest.approx(0.1 / 0.9 / 0.25 * 1000)
    assert s.soc_end_kwh == pytest.approx(10.0)
    assert s.import_w == pytest.approx(1000 + s.charge_grid_w)


def test_grid_charge_with_surplus_exports_rest() -> None:
    s = step(5.0, slot(4000, 1000), grid(1500), P)
    assert (s.charge_pv_w, s.charge_grid_w, s.export_w, s.import_w) == (3000, 1500, 0, 1500)


def test_soc_above_hi_neither_charges() -> None:
    """Ein SOC über ``hi`` lädt nicht weiter und bleibt unverändert."""
    s = step(10.5, slot(3000, 1000), EV, P)
    assert s.charge_pv_w == 0
    assert s.export_w == 2000
    assert s.soc_end_kwh == 10.5


def test_energy_balance_holds() -> None:
    for intent in (EV, HOLD, grid(2000)):
        for pv, load in ((0, 1000), (3000, 1000), (800, 800), (7000, 200)):
            s = step(6.0, slot(pv, load), intent, P)
            lhs = pv + s.import_w + s.discharge_w
            rhs = load + s.export_w + s.charge_pv_w + s.charge_grid_w
            assert math.isclose(lhs, rhs, abs_tol=1e-9)


def test_simulate_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="gleich viele"):
        simulate(5.0, [slot(0, 1000)] * 2, [EV], P)
