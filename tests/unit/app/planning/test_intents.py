"""Tests der Absichten aus den Flüssen (Präzisierung 7): Netzladen gerundet, Halten bei
zurückgehaltener Entladung, sonst Eigenverbrauch."""

from __future__ import annotations

import pytest
from tests.plan_helpers import (
    flow,
    flows_result,
    month,
    pv_arc,
    resimulate,
    scenario,
    small_battery,
)

from bindaems.app.planning.battery_model import SLOT_H, eta
from bindaems.app.planning.intents import GRID_CHARGE_MIN_W, derive_intents
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_after_kwh
from bindaems.app.planning.service import plan_once
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, IntentPlan, intent_plan


def test_grid_charge_becomes_netzladen_rounded() -> None:
    # Restwert 30 ct: Das Netzladen rechnet sich für die Zeit nach dem Horizont.
    inputs = scenario(
        prices=[10.0] * 3, pv=[0.0] * 3, load=[1000.0] * 3, grid_charge=True, end_value_ct=30.0
    )
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


def test_grid_charge_is_cut_so_the_resimulation_keeps_rule_r1() -> None:
    # Der Optimierer entlädt in Slot 0 nur 900 W (Halten, die Nachrechnung behält 0,24 kWh
    # mehr), lädt in Slot 1 4 kW aus dem Netz und füllt den Akku danach mit PV genau bis 10 kWh,
    # ohne Einspeisung (Regel R1 bindet). Ungekürzt speiste die Nachrechnung am Ende rund
    # 0,26 kWh ein; das Netzladen wird um diese Energie gekürzt (auf 100 W aufgerundet). Mit dem
    # Restwert von 30 ct rechnet es sich für die Zeit nach dem Horizont.
    pv = [0.0, 0.0] + [4586.0] * 4
    inputs = scenario(
        prices=[20.0] * 6,
        pv=pv,
        load=[2000.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        battery=small_battery(),
        soc_pct=50.0,
        grid_charge=True,
        end_value_ct=30.0,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    soc = 5.0 - 900 * SLOT_H / 1000 / efficiency
    flows = [flow(gr_h=1100.0, bt_h=900.0, soc_end_kwh=soc)]
    soc += 4000 * SLOT_H / 1000 * efficiency
    flows.append(flow(gr_b=4000.0, soc_end_kwh=soc))
    for pv_w in pv[2:]:
        soc = min(10.0, soc + pv_w * SLOT_H / 1000 * efficiency)
        flows.append(flow(pv_b=pv_w, soc_end_kwh=soc))
    assert abs(soc - 10.0) < 0.01  # der Optimierer füllt den Akku genau

    intents = derive_intents(inputs, flows_result(flows))
    assert intents[0].kind == BatteryIntentKind.HALTEN
    assert intents[1] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2900)
    assert export_after_kwh(resimulate(inputs, intents), 1) <= R1_TOL_KWH


def test_grid_charge_without_room_left_becomes_halten() -> None:
    # Schon ohne Netzladen ist der Akku nach der PV voll und speist rund 0,7 kWh ein: Das Netzladen
    # in Slot 0 fällt ganz weg; der Slot hält den Akku, wie der Optimierer es wollte.
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0, 5000.0],
        load=[0.0, 0.0],
        battery=small_battery(),
        soc_pct=95.0,
        grid_charge=True,
    )
    flows = [flow(gr_b=200.0, soc_end_kwh=9.55), flow(pv_b=1918.0, pv_x=3082.0, soc_end_kwh=10.0)]
    intents = derive_intents(inputs, flows_result(flows))
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.HALTEN)


# --- Netzladen, das sich in der Nachrechnung nicht rechnet (Nachtrag 18.10.2026) -------------


@pytest.mark.parametrize(
    ("load0_w", "kind"),
    [(1000.0, BatteryIntentKind.HALTEN), (50.0, BatteryIntentKind.EIGENVERBRAUCH)],
)
def test_unprofitable_grid_charge_is_dropped(load0_w: float, kind: BatteryIntentKind) -> None:
    # 2 kW Netzladen zu 20 ct, verbraucht im nächsten Slot zu 20 ct: 0,88 · 18 − 20 < 2. Der Slot
    # bekommt die Absicht wie ohne Netzladen: Halten ab 100 W Eigenverbrauch, sonst Eigenverbrauch.
    inputs = scenario(
        prices=[20.0] * 2, pv=[0.0] * 2, load=[load0_w, 1000.0], soc_pct=50.0, grid_charge=True
    )
    flows = [flow(gr_b=2000.0, gr_h=load0_w), flow(bt_h=1000.0)]
    assert derive_intents(inputs, flows_result(flows))[0] == BatteryIntent(kind=kind)


def test_grid_charge_against_a_peak_is_kept() -> None:
    # Mit Leistungspreis darf Netzladen einer Spitze ausweichen; wie in der Plausibilität bleibt es.
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0] * 2,
        load=[1000.0, 1000.0],
        soc_pct=50.0,
        grid_charge=True,
        peak_months=[month("2026-10", 2.0)],
    )
    flows = [flow(gr_b=2000.0, gr_h=1000.0), flow(bt_h=1000.0)]
    assert derive_intents(inputs, flows_result(flows))[0] == BatteryIntent(
        kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000
    )


def test_grid_charge_spent_at_once_by_the_rounding_is_dropped() -> None:
    # Gezielte Suche zum Befund vom 18.10.2026: Der Optimierer lädt billig (10 ct) aus dem Netz für
    # 08:00–09:00 (45 ct). In einem billigen Slot dazwischen fehlen unter 100 W; dort sieht er keine
    # Entladung vor, die Absicht wird aber Eigenverbrauch (Halten erst ab 100 W). Die Nachrechnung
    # verbraucht die geladene Energie dann sofort zu 10 ct. Dieses Netzladen fällt weg, der Plan
    # besteht und geht als gewöhnliche Absicht an den core.
    start = "2026-10-16T04:00:00+00:00"
    inputs = scenario(
        prices=[10.0] * 8 + [45.0] * 4 + [10.0] * 4,
        pv=pv_arc(start, 16, 441.0),
        load=[300.0] * 16,
        soc_pct=20.0,
        grid_charge=True,
        start=start,
        end_value_ct=6.642748367682174,
    )
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    dropped = [
        index
        for index, flows in enumerate(result.flows)
        if flows.gr_b >= GRID_CHARGE_MIN_W
        and intents[index].kind is not BatteryIntentKind.NETZLADEN
    ]
    assert dropped
    plan = plan_once(inputs, result)
    assert plan.source == "optimizer", plan.issues
    sent = IntentPlan.model_validate_json(
        intent_plan(plan.model_copy(update={"id": 1}), grid_charge_allowed=True).model_dump_json()
    )
    by_start = {slot.start: slot.intent for slot in sent.slots}
    for index in dropped:
        intent = by_start.get(inputs.slots[index].start)
        if intent is not None:
            assert intent.kind in (BatteryIntentKind.HALTEN, BatteryIntentKind.EIGENVERBRAUCH)
            assert intent.grid_charge_w == 0
