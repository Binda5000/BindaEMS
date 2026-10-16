from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine

from bindaems.app.forecast.archive import (
    Actual,
    ForecastArchive,
    RecordValue,
    actual_from_ledger,
    actuals,
    horizon_class,
)
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.timeutil import SLOT


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def make_ledger_slot(
    flows: dict[str, float], covered_s: float, start: str = "2026-10-15T08:00:00+00:00"
) -> LedgerSlot:
    return LedgerSlot(
        slot_start=at(start),
        covered_s=covered_s,
        flows_wh=flows,
        counters={},
        import_wh=0.0,
        export_wh=0.0,
        import_price_ct=None,
        origin="stream",
    )


@pytest.fixture
def archive(engine: Engine) -> ForecastArchive:
    return ForecastArchive(engine)


def test_horizon_classes():
    assert horizon_class(timedelta(minutes=-1)) is None
    assert horizon_class(timedelta(0)) == "h0_6"
    assert horizon_class(timedelta(hours=6)) == "h6_24"
    assert horizon_class(timedelta(hours=24)) == "h24"


def test_horizon_class_edges():
    assert horizon_class(timedelta(hours=6, seconds=-1)) == "h0_6"
    assert horizon_class(timedelta(hours=24, seconds=-1)) == "h6_24"
    assert horizon_class(timedelta(days=3)) == "h24"


def test_record_keeps_issue_closest_to_class_middle(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    archive.record("pv", at("2026-10-16T04:00:00+00:00"), {slot: RecordValue(1000, 1000)})  # 16 h
    archive.record("pv", at("2026-10-16T08:00:00+00:00"), {slot: RecordValue(2000, 2000)})  # 12 h
    archive.record("pv", at("2026-10-16T17:00:00+00:00"), {slot: RecordValue(3000, 3000)})  # 3 h
    rows = {r.horizon: r.p50_w for r in archive.records("pv", slot, slot + SLOT)}
    assert rows == {"h6_24": 1000, "h0_6": 3000}


def test_record_tie_keeps_newer_issue(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    archive.record("pv", at("2026-10-16T04:00:00+00:00"), {slot: RecordValue(1000, 1000)})  # 16 h
    archive.record("pv", at("2026-10-16T06:00:00+00:00"), {slot: RecordValue(2000, 2000)})  # 14 h
    (row,) = archive.records("pv", slot, slot + SLOT)
    assert (row.p50_w, row.issued_at) == (2000, at("2026-10-16T06:00:00+00:00"))
    # eine ältere Ausgabe mit gleichem Abstand verdrängt die neuere nicht
    archive.record("pv", at("2026-10-16T04:00:00+00:00"), {slot: RecordValue(9, 9)})
    (row,) = archive.records("pv", slot, slot + SLOT)
    assert row.p50_w == 2000


def test_record_same_issue_again_overwrites(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    issued = at("2026-10-16T08:00:00+00:00")
    archive.record("pv", issued, {slot: RecordValue(1, 1)})
    assert archive.record("pv", issued, {slot: RecordValue(2, 5, "clear")}) == 1
    (row,) = archive.records("pv", slot, slot + SLOT)
    assert (row.p50_w, row.raw_w, row.clearness) == (2, 5, "clear")


def test_record_returns_number_of_written_rows(archive):
    a, b = at("2026-10-16T09:00:00+00:00"), at("2026-10-16T09:15:00+00:00")
    issued = at("2026-10-16T08:00:00+00:00")
    assert archive.record("load", issued, {a: RecordValue(1, 1), b: RecordValue(2, 2)}) == 2
    # später ausgegeben, aber weiter von der Klassenmitte (3 h) entfernt: wird nicht geschrieben
    assert archive.record("load", at("2026-10-16T08:30:00+00:00"), {a: RecordValue(5, 5)}) == 0
    assert archive.record("load", at("2026-10-16T08:00:00+00:00"), {}) == 0


def test_record_kinds_are_separate(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    issued = at("2026-10-16T17:00:00+00:00")
    archive.record("pv", issued, {slot: RecordValue(1, 2, "mixed")})
    archive.record("load", issued, {slot: RecordValue(3, 3)})
    (pv,) = archive.records("pv", slot, slot + SLOT)
    (load,) = archive.records("load", slot, slot + SLOT)
    assert (pv.p50_w, pv.raw_w, pv.clearness) == (1, 2, "mixed")
    assert (load.p50_w, load.raw_w, load.clearness) == (3, 3, None)
    assert pv.kind == "pv" and load.kind == "load"
    assert pv.slot_start == slot and pv.issued_at == issued and pv.horizon == "h0_6"


def test_records_filter_by_horizon_and_range(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    archive.record("pv", at("2026-10-16T17:00:00+00:00"), {slot: RecordValue(3, 3)})
    archive.record("pv", at("2026-10-16T08:00:00+00:00"), {slot: RecordValue(2, 2)})
    assert [r.horizon for r in archive.records("pv", slot, slot + SLOT, "h6_24")] == ["h6_24"]
    assert archive.records("pv", slot + SLOT, slot + 2 * SLOT) == []
    assert archive.records("pv", slot, slot) == []


def test_record_skips_slots_before_issue(archive):
    n = archive.record(
        "load",
        at("2026-10-16T10:07:00+00:00"),
        {at("2026-10-16T10:00:00+00:00"): RecordValue(500, 500)},
    )
    assert n == 0


def test_record_on_dst_day_keeps_both_0200_slots(archive):
    a, b = at("2026-10-25T00:00:00+00:00"), at("2026-10-25T01:00:00+00:00")
    archive.record(
        "load", at("2026-10-24T22:00:00+00:00"), {a: RecordValue(1, 1), b: RecordValue(2, 2)}
    )
    assert [r.p50_w for r in archive.records("load", a, b + SLOT)] == [1, 2]


def test_actual_from_complete_slot():
    slot = make_ledger_slot(
        {
            "pv>house": 250,
            "pv>grid": 500,
            "grid>house": 100,
            "battery>house": 50,
            "grid>wb:evcs": 1000,
        },
        covered_s=900,
    )
    assert actual_from_ledger(slot) == Actual(pv_w=3000.0, house_w=1600.0)


def test_incomplete_slot_has_no_actual():
    assert actual_from_ledger(make_ledger_slot({"pv>house": 250}, covered_s=600)) is None
    assert actual_from_ledger(make_ledger_slot({"pv>house": 250}, covered_s=854.9)) is None
    assert actual_from_ledger(make_ledger_slot({"pv>house": 250}, covered_s=855)) is not None


def test_actual_without_flows_is_zero():
    assert actual_from_ledger(make_ledger_slot({}, covered_s=900)) == Actual(0.0, 0.0)


def test_actuals_reads_complete_slots_from_the_ledger():
    complete = make_ledger_slot({"pv>house": 100}, 900, "2026-10-15T08:00:00+00:00")
    partial = make_ledger_slot({"pv>house": 100}, 300, "2026-10-15T08:15:00+00:00")

    class FakeLedger:
        def slots(self, start, end):
            self.args = (start, end)
            return [complete, partial]

    ledger = FakeLedger()
    start, end = at("2026-10-15T08:00:00+00:00"), at("2026-10-15T09:00:00+00:00")
    assert actuals(ledger, start, end) == {complete.slot_start: Actual(400.0, 400.0)}
    assert ledger.args == (start, end)


def test_cleanup_removes_old_records(archive):
    archive.record(
        "pv",
        at("2026-08-01T08:00:00+00:00"),
        {at("2026-08-01T09:00:00+00:00"): RecordValue(1, 1)},
    )
    assert archive.cleanup(at("2026-08-15T00:00:00+00:00")) == 1
    assert archive.cleanup(at("2026-08-15T00:00:00+00:00")) == 0
