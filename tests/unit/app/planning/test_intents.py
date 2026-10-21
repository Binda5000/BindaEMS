"""Tests der Absichten aus den Flüssen (Präzisierung 7): Netzladen gerundet, Halten bei
zurückgehaltener Entladung, sonst Eigenverbrauch."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs
from tests.plan_helpers import (
    SCENARIO_START,
    at,
    flow,
    flows_result,
    month,
    planned_car,
    pv_arc,
    resimulate,
    scenario,
    small_battery,
)

from bindaems.app.planning.battery_model import SLOT_H, eta, params_of
from bindaems.app.planning.intents import GRID_CHARGE_MIN_W, _drop_unprofitable, derive_intents
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.app.planning.plausibility import R1_TOL_KWH, export_while_charged_kwh
from bindaems.app.planning.service import plan_once
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, IntentPlan, intent_plan
from bindaems.shared.timeutil import SLOT


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
    lo_kwh = params_of(inputs).lo_kwh
    assert export_while_charged_kwh(resimulate(inputs, intents), 1, lo_kwh) <= R1_TOL_KWH


def test_r1_trim_is_logged() -> None:
    # Dieselben Flüsse wie oben: Das Netzladen in Slot 1 wird von 4000 W auf 2900 W gekürzt
    # (1,1 kW über 15 min = 0,275 kWh); das steht als Warnung im Log, damit ein Fehler des
    # Optimierers sichtbar bleibt. Ohne Kürzung (nächster Test) gibt es keinen Eintrag.
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

    with capture_logs() as logs:
        derive_intents(inputs, flows_result(flows))
    trimmed = [entry for entry in logs if entry.get("code") == "plan_r1_trimmed"]
    assert len(trimmed) == 1
    assert trimmed[0]["log_level"] == "warning"
    assert trimmed[0]["slot"] == inputs.slots[1].start.isoformat()
    assert trimmed[0]["cut_kwh"] == pytest.approx(1100 * SLOT_H / 1000)
    assert trimmed[0]["cut_kwh"] > R1_TOL_KWH


def test_r1_trim_below_the_tolerance_is_not_logged() -> None:
    # Wie ``test_grid_charge_is_cut_only_by_the_charge_left_before_the_feed_in``, nur lädt der
    # Optimierer in Slot 0 weniger (1840 W, gerundet 1800 W): Die Kürzung auf 1500 W beträgt
    # 0,075 kWh, also nicht mehr als ``R1_TOL_KWH`` (0,1 kWh), und steht nicht im Log.
    inputs = scenario(
        prices=[10.0, 40.0, 40.0, 10.0],
        pv=[0.0, 0.0, 0.0, 9000.0],
        load=[500.0, 2000.0, 1000.0, 500.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
        end_value_ct=0.0,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    lo_kwh = params_of(inputs).lo_kwh
    soc = lo_kwh + 1840 * SLOT_H / 1000 * efficiency
    flows = [flow(gr_h=500.0, gr_b=1840.0, soc_end_kwh=soc)]
    soc -= 800 * SLOT_H / 1000 / efficiency
    flows.append(flow(gr_h=1200.0, bt_h=800.0, soc_end_kwh=soc))
    discharge_w = (soc - (lo_kwh + 0.05)) * efficiency / SLOT_H * 1000
    flows.append(flow(gr_h=1000.0 - discharge_w, bt_h=discharge_w, soc_end_kwh=lo_kwh + 0.05))
    flows.append(
        flow(pv_h=500.0, pv_b=5000.0, pv_x=3500.0, soc_end_kwh=lo_kwh + 0.05 + 1.25 * efficiency)
    )

    with capture_logs() as logs:
        intents = derive_intents(inputs, flows_result(flows))
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1500.0)
    assert (1800.0 - 1500.0) * SLOT_H / 1000 <= R1_TOL_KWH
    assert [entry for entry in logs if entry.get("code") == "plan_r1_trimmed"] == []


def test_r1_trim_over_several_rounds_is_logged_once_per_slot() -> None:
    # Slot 0 lädt 4 kW aus dem Netz, Slot 1 2 kW; danach speist die PV in Slot 2 ein. Slot 1
    # (0,5 kWh gekürzt) kommt in der ersten Runde dran, Slot 0 braucht danach zwei Runden und
    # wird insgesamt von 4000 W auf 400 W gekürzt (0,9 kWh): ein Eintrag je Slot, nicht je Runde.
    inputs = scenario(
        prices=[10.0] * 3,
        pv=[2000.0, 9000.0, 2000.0],
        load=[2000.0, 1000.0, 2000.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
        end_value_ct=30.0,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    soc = 2.0
    flows = []
    for pv_w, load_w, grid_w, discharge_w in (
        (2000.0, 2000.0, 4000.0, 0.0),
        (9000.0, 1000.0, 2000.0, 0.0),
        (2000.0, 2000.0, 0.0, 1000.0),
    ):
        soc = max(
            2.0, min(10.0, soc + (grid_w * efficiency - discharge_w / efficiency) * SLOT_H / 1000)
        )
        flows.append(
            flow(
                gr_b=grid_w,
                bt_h=discharge_w,
                gr_h=max(0.0, load_w - discharge_w - pv_w),
                pv_h=min(pv_w, load_w),
                soc_end_kwh=soc,
            )
        )

    with capture_logs() as logs:
        intents = derive_intents(inputs, flows_result(flows))
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=400.0)
    trimmed = [entry for entry in logs if entry.get("code") == "plan_r1_trimmed"]
    assert [entry["slot"] for entry in trimmed] == [
        inputs.slots[1].start.isoformat(),
        inputs.slots[0].start.isoformat(),
    ]
    assert [entry["cut_kwh"] for entry in trimmed] == [pytest.approx(0.5), pytest.approx(0.9)]


def test_no_r1_trim_is_not_logged() -> None:
    # Wie ``test_grid_charge_survives_rounding_above_the_reserve``: nichts wird gekürzt.
    inputs = scenario(
        prices=[10.0, 40.0, 40.0],
        pv=[0.0, 0.0, 9000.0],
        load=[500.0, 2000.0, 500.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    lo_kwh = params_of(inputs).lo_kwh
    soc = lo_kwh + 2000 * SLOT_H / 1000 * efficiency
    flows = [flow(gr_h=500.0, gr_b=2000.0, soc_end_kwh=soc)]
    soc_after = lo_kwh + 0.05
    discharge_w = (soc - soc_after) * efficiency / SLOT_H * 1000
    flows.append(flow(gr_h=2000.0 - discharge_w, bt_h=discharge_w, soc_end_kwh=soc_after))
    flows.append(flow(pv_h=500.0, pv_x=8500.0, soc_end_kwh=soc_after))

    with capture_logs() as logs:
        derive_intents(inputs, flows_result(flows))
    assert [entry for entry in logs if entry.get("code") == "plan_r1_trimmed"] == []


def test_grid_charge_survives_rounding_above_the_reserve() -> None:
    # Der Optimierer lädt in Slot 0 aus dem Netz, entlädt in Slot 1 bis 0,05 kWh über die Reserve
    # (2 kWh) und speist erst in Slot 2 PV ein. Die Nachrechnung mit Eigenverbrauch leert den Akku
    # in Slot 1 ganz (Last 2 kW, der Akku liefert weniger); die Einspeisung danach liegt hinter
    # dem Fenster, das Netzladen bleibt ungekürzt. Mit dem Rest bis zum Horizontende gerechnet
    # (alte Regel R1) fiele es weg.
    inputs = scenario(
        prices=[10.0, 40.0, 40.0],
        pv=[0.0, 0.0, 9000.0],
        load=[500.0, 2000.0, 500.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    lo_kwh = params_of(inputs).lo_kwh
    soc = lo_kwh + 2000 * SLOT_H / 1000 * efficiency
    flows = [flow(gr_h=500.0, gr_b=2000.0, soc_end_kwh=soc)]
    soc_after = lo_kwh + 0.05
    discharge_w = (soc - soc_after) * efficiency / SLOT_H * 1000
    flows.append(flow(gr_h=2000.0 - discharge_w, bt_h=discharge_w, soc_end_kwh=soc_after))
    flows.append(flow(pv_h=500.0, pv_x=8500.0, soc_end_kwh=soc_after))

    intents = derive_intents(inputs, flows_result(flows))
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000)
    steps = resimulate(inputs, intents)
    assert steps[1].soc_end_kwh <= lo_kwh + 0.1  # der Akku ist leer, bevor die PV einspeist
    assert steps[2].export_w > 0
    assert export_while_charged_kwh(steps, 0, lo_kwh) <= R1_TOL_KWH


def test_grid_charge_is_cut_only_by_the_charge_left_before_the_feed_in() -> None:
    # Die Rundung macht aus der Teilentladung in Slot 1 ``HALTEN``; die Nachrechnung kommt vor
    # der PV in Slot 3 rund 0,2 kWh über der Reserve an und speist dort danach 0,875 kWh ein
    # (PV über der Ladeleistung). Gekürzt wird nur um die Ladung, die vor der Einspeisung noch
    # über der Reserve liegt (rund 430 W), nicht um die ganze Einspeisung: Das Netzladen bleibt.
    inputs = scenario(
        prices=[10.0, 40.0, 40.0, 10.0],
        pv=[0.0, 0.0, 0.0, 9000.0],
        load=[500.0, 2000.0, 1000.0, 500.0],
        battery=small_battery(),
        soc_pct=20.0,
        grid_charge=True,
        end_value_ct=0.0,
    )
    efficiency = eta(inputs.planning.roundtrip_pct)
    lo_kwh = params_of(inputs).lo_kwh
    soc = lo_kwh + 2000 * SLOT_H / 1000 * efficiency
    flows = [flow(gr_h=500.0, gr_b=2000.0, soc_end_kwh=soc)]
    soc -= 800 * SLOT_H / 1000 / efficiency
    flows.append(flow(gr_h=1200.0, bt_h=800.0, soc_end_kwh=soc))
    discharge_w = (soc - (lo_kwh + 0.05)) * efficiency / SLOT_H * 1000
    flows.append(flow(gr_h=1000.0 - discharge_w, bt_h=discharge_w, soc_end_kwh=lo_kwh + 0.05))
    flows.append(
        flow(pv_h=500.0, pv_b=5000.0, pv_x=3500.0, soc_end_kwh=lo_kwh + 0.05 + 1.25 * efficiency)
    )

    intents = derive_intents(inputs, flows_result(flows))
    assert intents[0].kind is BatteryIntentKind.NETZLADEN
    assert GRID_CHARGE_MIN_W <= intents[0].grid_charge_w < 2000
    assert export_while_charged_kwh(resimulate(inputs, intents), 0, lo_kwh) <= R1_TOL_KWH


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
    # Der Plan gilt nur 45 min; der gestrichene Slot liegt dahinter. Damit der Teil prüft, was an
    # den core ginge, gilt der Plan hier für den ganzen Horizont (der core nimmt höchstens
    # ``MAX_INTENT_PLAN_SLOTS`` Slots).
    sendable = plan.model_copy(update={"id": 1, "valid_until": inputs.slots[-1].start + SLOT})
    sent = IntentPlan.model_validate_json(
        intent_plan(sendable, grid_charge_allowed=True).model_dump_json()
    )
    by_start = {slot.start: slot.intent for slot in sent.slots}
    in_sent = [index for index in dropped if inputs.slots[index].start in by_start]
    assert in_sent, "ein gestrichener Slot muss im gesendeten Plan liegen"
    for index in in_sent:
        intent = by_start[inputs.slots[index].start]
        assert intent.kind in (BatteryIntentKind.HALTEN, BatteryIntentKind.EIGENVERBRAUCH)
        assert intent.grid_charge_w == 0


def test_grid_charge_spent_at_once_is_dropped_with_fixed_flows() -> None:
    # Wie oben, mit fest vorgegebenen Flüssen: 100 W Netzladen zu 10 ct in Slot 0. Die Slots 1 bis 3
    # brauchen je 90 W; die Absicht ist jeweils Eigenverbrauch (unter 100 W kein Halten), und die
    # Nachrechnung entlädt die 0,02 kWh schon in Slot 1 zu 10 ct. Der teure Slot 3 (40 ct) bekommt
    # nichts mehr, obwohl der Optimierer die Energie dafür vorsah. Das Netzladen fällt weg.
    inputs = scenario(
        prices=[10.0, 10.0, 10.0, 40.0],
        pv=[0.0] * 4,
        load=[0.0, 90.0, 90.0, 90.0],
        soc_pct=20.0,
        grid_charge=True,
    )
    flows = [
        flow(gr_b=100.0, soc_end_kwh=6.025),
        flow(gr_h=90.0, soc_end_kwh=6.025),
        flow(gr_h=90.0, soc_end_kwh=6.025),
        flow(bt_h=90.0, soc_end_kwh=6.0025),
    ]
    intents = derive_intents(inputs, flows_result(flows))
    assert intents == [BatteryIntent()] * 4


def test_grid_charge_that_pays_through_the_end_value_is_kept() -> None:
    # 20 ct jetzt, 20 ct später: Der Spread rechnet sich nicht. Mit Restwert 30 ct schon
    # (0,94 · 30 − 20 ≥ 2), und die Ladung bleibt bis zum Horizontende im Akku. Ohne Restwert fällt
    # das Netzladen weg. Direkter Test von ``_drop_unprofitable``.
    plain = [BatteryIntent(), BatteryIntent()]
    charging = [
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000),
        BatteryIntent(),
    ]

    def drop(end_value_ct: float) -> list[BatteryIntent]:
        inputs = scenario(
            prices=[20.0] * 2,
            pv=[0.0] * 2,
            load=[1000.0, 1000.0],
            soc_pct=50.0,
            grid_charge=True,
            end_value_ct=end_value_ct,
        )
        return _drop_unprofitable(inputs, charging, plain)

    assert drop(30.0) == charging
    assert drop(0.0) == plain


# Geplante Autos (Plan 4b, Präzisierung 12)


def test_resimulation_counts_the_car_taking_pv() -> None:
    # Netzladen um 02:00 für den teuren Abend; um 02:45 nimmt das Auto 4,5 kW der PV, der Akku
    # den Rest. Ohne das Auto speiste die Nachrechnung 4,5 kW ein, und Regel R1 striche das
    # Netzladen.
    car = planned_car(departure_at=at(SCENARIO_START) + 4 * SLOT, need_kwh=1.2)
    inputs = scenario(
        prices=[10.0, 40.0, 40.0, 20.0],
        pv=[0.0, 0.0, 0.0, 10000.0],
        load=[500.0, 2000.0, 2000.0, 500.0],
        battery=small_battery(),
        soc_pct=40.0,
        grid_charge=True,
        vehicles=[car],
    )
    result = flows_result(
        [
            flow(gr_h=500, gr_b=4000),
            flow(bt_h=2000),
            flow(bt_h=2000),
            flow(pv_h=500, pv_b=5000, ev={"tesla": (4500.0, 0.0)}),
        ]
    )
    intents = derive_intents(inputs, result)
    assert intents[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=4000)


def test_hold_compares_with_the_house_load_without_the_car() -> None:
    # Der Akku deckt das Haus (500 W) voll, das Auto lädt 7 kW aus dem Netz: Eigenverbrauch
    car = planned_car(departure_at=at(SCENARIO_START) + 2 * SLOT)
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[500.0] * 2, vehicles=[car])
    result = flows_result([flow(bt_h=500, ev={"tesla": (0.0, 7000.0)})] * 2)
    assert [i.kind for i in derive_intents(inputs, result)] == [
        BatteryIntentKind.EIGENVERBRAUCH
    ] * 2
