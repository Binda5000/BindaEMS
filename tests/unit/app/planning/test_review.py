"""Tests von „Plan gegen Ist“: welcher Plan galt, Ist aus der Abrechnung (Plan 3c, Task 14)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.plan_helpers import at, sample_plan

from bindaems.app.ledger.service import LedgerSlot
from bindaems.app.planning.review import in_force, review
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind
from bindaems.shared.timeutil import local_day_slots

DAY = date(2026, 10, 16)


def ts(clock: str) -> datetime:
    """``HH:MM`` UTC am 16. 10. 2026."""
    return at(f"2026-10-16T{clock}:00+00:00")


def ledger_slot(
    clock: str,
    flows: dict[str, float] | None = None,
    *,
    import_wh: float = 0.0,
    export_wh: float = 0.0,
    price: float | None = 20.0,
) -> LedgerSlot:
    return LedgerSlot(
        slot_start=ts(clock),
        covered_s=900.0,
        flows_wh=flows or {},
        counters={},
        import_wh=import_wh,
        export_wh=export_wh,
        import_price_ct=price,
        origin="stream",
    )


def row(body: dict, clock: str) -> dict:
    """Die Zeile des Slots ``HH:MM`` (UTC)."""
    return next(item for item in body["slots"] if item["start"] == ts(clock).isoformat())


def test_newest_plan_created_before_the_slot_end_wins() -> None:
    first = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15", "10:30"])
    second = sample_plan("2026-10-16T10:16:00+00:00", ["10:15", "10:30"])
    chosen = in_force([second, first], [ts("10:00"), ts("10:15"), ts("10:30")])
    assert chosen[ts("10:00")][0].created_at == ts("10:01")
    assert chosen[ts("10:15")][0].created_at == ts("10:16")
    assert chosen[ts("10:30")][0].created_at == ts("10:16")
    assert chosen[ts("10:15")][1].start == ts("10:15")


def test_plan_created_within_the_slot_is_in_force_for_it() -> None:
    inside = sample_plan("2026-10-16T10:14:59+00:00", ["10:00", "10:15"])
    at_end = sample_plan("2026-10-16T10:15:00+00:00", ["10:00", "10:15"])
    assert in_force([inside], [ts("10:00")])[ts("10:00")][0] is inside
    assert ts("10:00") not in in_force([at_end], [ts("10:00")])  # erst mit dem Slotende erzeugt


def test_expired_plan_is_not_in_force() -> None:
    plan = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15", "10:30", "10:45", "11:00"])
    chosen = in_force([plan], [ts("10:45"), ts("11:00")])
    assert ts("10:45") in chosen  # beginnt vor valid_until (10:46)
    assert ts("11:00") not in chosen


def test_plan_without_the_slot_is_not_in_force() -> None:
    plan = sample_plan("2026-10-16T10:01:00+00:00", ["10:15"])
    assert in_force([plan], [ts("10:00")]) == {}


def test_older_plan_covers_when_the_newer_does_not_hold_the_slot() -> None:
    old = sample_plan("2026-10-16T10:01:00+00:00", ["10:00", "10:15"])
    new = sample_plan("2026-10-16T10:10:00+00:00", ["10:15"])
    chosen = in_force([old, new], [ts("10:00"), ts("10:15")])
    assert chosen[ts("10:00")][0] is old and chosen[ts("10:15")][0] is new


def test_actual_battery_power_from_flows() -> None:
    flows = {"pv>battery": 250.0, "grid>battery": 100.0, "battery>house": 50.0, "pv>house": 80.0}
    slots = [ledger_slot("10:00", flows, import_wh=120.0, export_wh=30.0, price=25.0)]
    body = review(DAY, [], slots, ts("10:30"))
    actual = row(body, "10:00")["actual"]
    assert actual["battery_w"] == pytest.approx(1200.0)  # (250 + 100 - 50) * 4
    assert actual["grid_charge_w"] == pytest.approx(400.0)
    assert actual["import_w"] == pytest.approx(480.0) and actual["export_w"] == pytest.approx(120.0)
    assert actual["import_cost_ct"] == pytest.approx(3.0)  # 0,12 kWh * 25 ct


def test_actual_cost_is_null_without_price() -> None:
    body = review(DAY, [], [ledger_slot("10:00", import_wh=100.0, price=None)], ts("10:30"))
    assert row(body, "10:00")["actual"]["import_cost_ct"] is None


def test_planned_fields_come_from_the_slot_and_the_plan() -> None:
    plan = sample_plan(
        "2026-10-16T10:01:00+00:00",
        ["10:00"],
        soc=(50.0, 54.0),
        intents=[
            {
                "intent": BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=3000.0),
                "charge_grid_w": 3000.0,
                "charge_pv_w": 500.0,
                "discharge_w": 200.0,
                "import_w": 4000.0,
                "export_w": 100.0,
                "import_ct": 10.0,
            }
        ],
    ).model_copy(update={"id": 7})
    body = review(DAY, [plan], [], ts("10:15"))
    assert row(body, "10:00")["planned"] == {
        "intent": "NETZLADEN",
        "grid_charge_w": 3000.0,
        "soc_end_pct": 54.0,
        "import_w": 4000.0,
        "export_w": 100.0,
        "battery_w": 3300.0,
        "import_cost_ct": 10.0,  # 4000 W über 15 min = 1 kWh à 10 ct
        "plan_id": 7,
        "source": "optimizer",
    }
    assert row(body, "10:00")["actual"] is None


def test_totals_only_over_slots_with_both() -> None:
    plan = sample_plan(
        "2026-10-16T09:50:00+00:00",
        ["10:00", "10:15", "10:30"],
        intents=[{"import_w": 4000.0, "import_ct": 10.0, "charge_grid_w": 2000.0}] * 3,
    )
    ledger = [
        ledger_slot("10:00", {"grid>battery": 300.0}, import_wh=500.0, export_wh=100.0, price=20.0),
        ledger_slot("10:30", {}, import_wh=800.0, price=20.0),
        ledger_slot("09:45", {}, import_wh=9000.0, price=20.0),  # ohne Plan
    ]
    body = review(DAY, [plan], ledger, ts("10:45"))
    starts = [item["start"] for item in body["slots"]]
    assert ts("10:15").isoformat() in starts  # Slot ohne Ist bleibt in der Liste
    assert body["totals"]["planned"] == {
        "import_kwh": 2.0,  # 10:00 und 10:30
        "export_kwh": 0.0,
        "grid_charge_kwh": 1.0,
        "import_cost_eur": 0.2,
    }
    assert body["totals"]["actual"] == {
        "import_kwh": 1.3,
        "export_kwh": 0.1,
        "grid_charge_kwh": 0.3,
        "import_cost_eur": 0.26,
    }


def test_totals_without_priced_slot_have_no_cost() -> None:
    plan = sample_plan("2026-10-16T09:50:00+00:00", ["10:00"])
    body = review(DAY, [plan], [ledger_slot("10:00", import_wh=100.0, price=None)], ts("10:30"))
    assert body["totals"]["planned"]["import_cost_eur"] is None
    assert body["totals"]["actual"]["import_cost_eur"] is None
    assert body["totals"]["actual"]["import_kwh"] == pytest.approx(0.1)


def test_future_slots_are_left_out() -> None:
    body = review(DAY, [], [], ts("10:20"))  # 10:00 ist vorbei, 10:15 läuft noch
    starts = [item["start"] for item in body["slots"]]
    assert starts[-1] == ts("10:00").isoformat() and len(starts) == 49  # ab 00:00 Ortszeit
    assert body["day"] == "2026-10-16"


def test_slot_count_of_a_whole_past_day_and_dst_days() -> None:
    late = datetime(2026, 11, 20, tzinfo=UTC)
    for day, count in [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100), (DAY, 96)]:
        body = review(day, [], [], late)
        assert len(body["slots"]) == count == len(local_day_slots(day))
        assert body["slots"][0]["start"] == local_day_slots(day)[0].isoformat()
