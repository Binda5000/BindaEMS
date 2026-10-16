"""Tests der Absichten aus den Flüssen (Präzisierung 7): Netzladen gerundet, Halten bei
zurückgehaltener Entladung, sonst Eigenverbrauch."""

from __future__ import annotations

import pytest
from tests.plan_helpers import flow, flows_result, scenario

from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import OptimizerResult
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind


def test_grid_charge_becomes_netzladen_rounded() -> None:
    inputs = scenario(prices=[10.0] * 3, pv=[0.0] * 3, load=[1000.0] * 3, grid_charge=True)
    result = flows_result([flow(gr_b=2340), flow(gr_b=60), flow(gr_b=7100)])  # GC = 8000 − 1000
    kinds = derive_intents(inputs, result)
    assert kinds[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2300)
    assert kinds[1].kind == BatteryIntentKind.EIGENVERBRAUCH
    assert kinds[2].grid_charge_w == 7000


def test_grid_charge_below_one_step_of_headroom_is_not_netzladen() -> None:
    inputs = scenario(prices=[10.0], pv=[0.0], load=[7950.0], grid_charge=True)  # GC = 50 W
    kind = derive_intents(inputs, flows_result([flow(gr_b=500, gr_h=7950)]))[0].kind
    assert kind != BatteryIntentKind.NETZLADEN


@pytest.mark.parametrize(
    ("bt_h", "kind"),
    [
        (0.0, "HALTEN"),
        (900.0, "HALTEN"),
        (1000.0, "EIGENVERBRAUCH"),
        (2000.0, "EIGENVERBRAUCH"),
    ],
)
def test_withheld_discharge_becomes_halten(bt_h: float, kind: str) -> None:
    inputs = scenario(prices=[20.0], pv=[0.0], load=[2000.0], soc_pct=80.0)
    result = flows_result([flow(bt_h=bt_h, gr_h=2000 - bt_h)])
    assert derive_intents(inputs, result)[0].kind == kind


def test_small_deficit_and_empty_battery_stay_eigenverbrauch() -> None:
    small = scenario(prices=[20.0], pv=[0.0], load=[80.0], soc_pct=80.0)
    assert (
        derive_intents(small, flows_result([flow(gr_h=80)]))[0].kind
        == BatteryIntentKind.EIGENVERBRAUCH
    )
    empty = scenario(prices=[20.0], pv=[0.0], load=[2000.0], soc_pct=20.0)
    assert (
        derive_intents(empty, flows_result([flow(gr_h=2000)]))[0].kind
        == BatteryIntentKind.EIGENVERBRAUCH
    )


def test_later_slots_use_the_end_soc_of_the_previous_flow() -> None:
    # Slot 0 entlädt den Akku bis zur Reserve; in Slot 1 gäbe es nichts mehr zu halten.
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[2000.0] * 2, soc_pct=21.0)
    empty = flow(bt_h=2000.0, soc_end_kwh=6.0)
    assert (
        derive_intents(inputs, flows_result([empty, flow(gr_h=2000.0)]))[1].kind
        == BatteryIntentKind.EIGENVERBRAUCH
    )
    full = flow(soc_end_kwh=20.0)
    assert (
        derive_intents(inputs, flows_result([full, flow(gr_h=2000.0)]))[1].kind
        == BatteryIntentKind.HALTEN
    )


def test_no_flows_means_no_intents() -> None:
    inputs = scenario(prices=[20.0], pv=[0.0], load=[1000.0])
    empty = OptimizerResult(
        status="infeasible", flows=(), peaks_kw={}, objective_ct=None, solve_ms=0
    )
    assert derive_intents(inputs, empty) == []
