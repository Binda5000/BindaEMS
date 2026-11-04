"""Tests der Regeln für Auffälligkeiten eines Tages (Betrieb im Blick, Task 3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from tests.plan_helpers import at, sample_plan

from bindaems.app.ledger.service import LedgerSlot
from bindaems.app.ops.findings import DayInputs, Finding, MissedTarget, find
from bindaems.shared.plan import Plan
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots

DAY = date(2026, 11, 3)  # Normalzeit, lokal = UTC + 1


def utc(clock: str, day: str = "2026-11-03") -> datetime:
    """``HH:MM`` UTC am Tag."""
    return at(f"{day}T{clock}:00+00:00")


def slot(
    clock: str,
    flows: Mapping[str, float] | None = None,
    *,
    import_wh: float = 0.0,
    day: str = "2026-11-03",
) -> LedgerSlot:
    return LedgerSlot(
        slot_start=utc(clock, day),
        covered_s=900.0,
        flows_wh=flows or {},
        counters={},
        import_wh=import_wh,
        export_wh=0.0,
        import_price_ct=20.0,
        origin="stream",
    )


def plan(slots: Sequence[str], **changes: Any) -> Plan:
    """Plan von 05:00 UTC, einen Tag gültig, mit Slots ``HH:MM`` (UTC) und denselben Änderungen."""
    made = sample_plan("2026-11-03T05:00:00+00:00", list(slots), intents=[changes for _ in slots])
    return made.model_copy(update={"valid_until": utc("05:00", "2026-11-04")})


def inputs(**changes: Any) -> DayInputs:
    values: dict[str, Any] = {
        "day": DAY,
        "slots": [],
        "plans": [],
        "soc_min": {},
        "reserve_soc_pct": 20.0,
        "previous_peak_kw": None,
        "failures": {},
        "actuator_labels": {},
        "missed": [],
        "flagged_checks": [],
    }
    values.update(changes)
    return DayInputs(**values)


def only(findings: list[Finding], rule: str) -> list[Finding]:
    return [item for item in findings if item.rule == rule]


GRID = {"grid>battery": 2200.0}


def test_unplanned_grid_charge_like_3_november() -> None:
    # 22:30 und 22:45 Ortszeit = 21:30 und 21:45 UTC
    found = find(
        inputs(
            slots=[slot("21:30", GRID), slot("21:45", GRID)],
            plans=[plan(["21:30", "21:45"], charge_grid_w=0.0)],
            soc_min={utc("21:30"): 14.0, utc("21:45"): 14.5},
        )
    )
    [item] = only(found, "grid_charge_unplanned")
    assert item.level == "warn"
    assert item.start == utc("21:30")
    assert item.end == utc("22:00")
    assert item.text.startswith("Akku lud 22:30–23:00 mit bis zu 8,8 kW aus dem Netz (4,4 kWh)")
    assert "der Plan sah kein Netzladen vor" in item.text
    assert "Vermutlich lud Victron selbst nach (SOC 14 %, Reserve 20 %)." in item.text
    assert item.link == "/entscheidungen?range=2026-11-03&actuator=victron"
    assert item.data["kwh"] == 4.4
    assert item.data["max_kw"] == 8.8
    assert item.data["soc_min"] == 14.0


def test_grid_charge_without_low_soc_has_no_victron_hint() -> None:
    found = find(
        inputs(
            slots=[slot("21:30", GRID)],
            plans=[plan(["21:30"], charge_grid_w=0.0)],
            soc_min={utc("21:30"): 19.0},
        )
    )
    [item] = only(found, "grid_charge_unplanned")
    assert "Victron" not in item.text


def test_planned_grid_charge_is_no_finding() -> None:
    found = find(
        inputs(
            slots=[slot("21:30", GRID)],
            plans=[plan(["21:30"], charge_grid_w=5000.0)],
        )
    )
    assert only(found, "grid_charge_unplanned") == []


def test_slot_without_plan_counts_only_if_day_has_plans() -> None:
    charging = [slot("21:30", GRID)]
    assert only(find(inputs(slots=charging)), "grid_charge_unplanned") == []
    other_slot_planned = [plan(["10:00"], charge_grid_w=0.0)]
    found = find(inputs(slots=charging, plans=other_slot_planned))
    [item] = only(found, "grid_charge_unplanned")
    assert "kein Plan" in item.text


def test_small_grid_charge_below_1_kw_is_ignored() -> None:
    found = find(
        inputs(
            slots=[slot("21:30", {"grid>battery": 249.0})],
            plans=[plan(["21:30"], charge_grid_w=0.0)],
        )
    )
    assert only(found, "grid_charge_unplanned") == []


def test_runs_with_one_gap_slot_merge() -> None:
    planned = plan(["21:00", "21:15", "21:30", "21:45", "22:00"], charge_grid_w=0.0)
    one_gap = find(inputs(slots=[slot("21:00", GRID), slot("21:30", GRID)], plans=[planned]))
    [merged] = only(one_gap, "grid_charge_unplanned")
    assert (merged.start, merged.end) == (utc("21:00"), utc("21:45"))
    two_gaps = find(inputs(slots=[slot("21:00", GRID), slot("21:45", GRID)], plans=[planned]))
    assert len(only(two_gaps, "grid_charge_unplanned")) == 2


def test_below_reserve_with_margin() -> None:
    ok = find(inputs(soc_min={utc("21:15"): 18.5}))
    assert only(ok, "below_reserve") == []
    found = find(inputs(soc_min={utc("21:15"): 17.9, utc("21:30"): 14.0, utc("21:45"): 17.0}))
    [item] = only(found, "below_reserve")
    assert item.level == "warn"
    assert (item.start, item.end) == (utc("21:15"), utc("22:00"))
    assert item.text == (
        "Akku war 22:15–23:00 unter der Reserve (tiefster Stand 14 %, Reserve 20 %)."
    )


def test_soc_unknown_gives_not_checked() -> None:
    found = find(inputs(soc_min=None))
    [item] = only(found, "not_checked")
    assert item.text == "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar."
    assert item.start is None
    assert only(found, "below_reserve") == []


def test_month_peak_rise_with_split() -> None:
    flows = {"grid>battery": 2050.0, "grid>house": 225.0, "grid>wallbox.evcs": 0.0}
    found = find(
        inputs(
            slots=[
                slot("21:15", {"grid>house": 1000.0}, import_wh=1000.0),
                slot("21:30", flows, import_wh=2275.0),
            ],
            previous_peak_kw=6.4,
        )
    )
    [item] = only(found, "month_peak")
    assert item.level == "warn"
    assert item.start == utc("21:30")
    assert item.text == (
        "Neue Monatsspitze 9,1 kW um 22:30 (vorher 6,4 kW): Akku 8,2 kW, Haus 0,9 kW."
    )


def test_month_peak_below_rise_threshold_is_ignored() -> None:
    found = find(inputs(slots=[slot("21:30", {}, import_wh=1600.0)], previous_peak_kw=6.0))
    assert only(found, "month_peak") == []  # 6,4 kW, nur +0,4 kW


def test_no_month_peak_rule_on_first_of_month() -> None:
    found = find(inputs(slots=[slot("21:30", GRID, import_wh=2275.0)], previous_peak_kw=None))
    assert only(found, "month_peak") == []


def _deviation(planned_kwh: float, actual_kwh: float) -> list[Finding]:
    planned = plan(["10:00"], import_w=planned_kwh * 4000.0)
    found = find(inputs(slots=[slot("10:00", {}, import_wh=actual_kwh * 1000.0)], plans=[planned]))
    return only(found, "plan_deviation")


def test_plan_deviation_needs_both_thresholds() -> None:
    assert _deviation(10.0, 12.9) == []  # unter 3 kWh
    assert _deviation(20.0, 24.0) == []  # unter 25 % des Plans
    [item] = _deviation(10.0, 14.0)
    assert item.level == "info"
    assert item.text == "Netzbezug 14,0 kWh statt geplant 10,0 kWh (+4,0 kWh)."
    assert item.link == "/zeitplan"
    assert item.start is None


def test_missed_target_text() -> None:
    missed = MissedTarget("Tesla", 80.0, 71.0, at("2026-11-03T06:00:00+00:00"))
    [item] = only(find(inputs(missed=[missed])), "target_missed")
    assert item.level == "warn"
    assert item.text == "Tesla hat das Ziel 80 % um 07:00 verfehlt (erreicht: 71 %)."
    assert item.link == "/laden"
    unknown = MissedTarget("Tesla", 80.0, None, at("2026-11-03T06:00:00+00:00"))
    [other] = only(find(inputs(missed=[unknown])), "target_missed")
    assert other.text == "Tesla hat das Ziel 80 % um 07:00 verfehlt."


def test_command_failures_per_actuator_with_label() -> None:
    found = find(
        inputs(
            failures={"evcs": 3, "victron": 0, "twc": 1},
            actuator_labels={"evcs": "Wallbox EVCS"},
        )
    )
    items = only(found, "command_failures")
    assert [i.text for i in items] == [
        "3 Befehle an Wallbox EVCS gescheitert.",
        "1 Befehl an twc gescheitert.",
    ]
    assert items[0].level == "warn"
    assert items[0].link == "/entscheidungen?range=2026-11-03&actuator=evcs&result=error"


def test_flagged_counter_check() -> None:
    found = find(inputs(flagged_checks=[("Netzbezug", 2.4), ("Einspeisung", None)]))
    items = only(found, "counter_check")
    assert items[0].level == "info"
    assert items[0].text == "Abrechnung und Zähler weichen bei Netzbezug um 2,4 % ab."
    assert items[1].text == "Abrechnung und Zähler weichen bei Einspeisung ab."
    assert items[0].link == "/statistik"


def test_rules_on_dst_day_cover_100_slots() -> None:
    day = date(2026, 10, 25)
    starts = local_day_slots(day)
    assert len(starts) == 100
    ledger = [
        LedgerSlot(
            slot_start=start,
            covered_s=900.0,
            flows_wh=GRID,
            counters={},
            import_wh=0.0,
            export_wh=0.0,
            import_price_ct=None,
            origin="stream",
        )
        for start in starts
    ]
    planned = sample_plan(
        "2026-10-24T20:00:00+00:00", ["22:00"], intents=[{"charge_grid_w": 0.0}]
    ).model_copy(update={"valid_until": at("2026-10-26T00:00:00+00:00")})
    found = find(inputs(day=day, slots=ledger, plans=[planned], soc_min={s: 10.0 for s in starts}))
    [charge] = only(found, "grid_charge_unplanned")
    assert (charge.start, charge.end) == (starts[0], starts[-1] + SLOT)
    [low] = only(found, "below_reserve")
    assert (low.start, low.end) == (starts[0], starts[-1] + SLOT)
    assert charge.text.startswith("Akku lud 00:00–00:00")  # Ende 24:00 = 00:00 am Folgetag
    assert starts[0].astimezone(LOCAL_TZ).hour == 0


def test_findings_sorted_by_start_then_day_rules() -> None:
    found = find(
        inputs(
            slots=[slot("21:30", GRID), slot("10:00", {}, import_wh=0.0)],
            plans=[plan(["21:30"], charge_grid_w=0.0)],
            soc_min={utc("08:00"): 10.0},
            failures={"evcs": 1},
            flagged_checks=[("Netzbezug", 1.0)],
        )
    )
    assert [item.rule for item in found] == [
        "below_reserve",
        "grid_charge_unplanned",
        "command_failures",
        "counter_check",
    ]
