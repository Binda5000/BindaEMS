from datetime import date, datetime

import pytest
from tests.app_helpers import ingest

from bindaems.app.forecast.archive import RecordValue

DAY = date(2026, 10, 15)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def fill_load_day(archive, ledger) -> None:
    s1, s2 = at("2026-10-15T08:00:00+00:00"), at("2026-10-15T08:15:00+00:00")
    archive.record(
        "load",
        at("2026-10-15T05:00:00+00:00"),
        {s1: RecordValue(1000, 1000), s2: RecordValue(500, 500)},
    )
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 200})  # 800 W
    ingest(ledger, "2026-10-15T08:15:00+00:00", {"grid>house": 175})  # 700 W


def test_metrics_for_a_day(quality, archive, ledger):
    fill_load_day(archive, ledger)
    assert quality.update(DAY) == 1
    [row] = quality.summary(DAY, DAY)
    assert (row.kind, row.horizon, row.n, row.mae_w, row.bias_w) == ("load", "h0_6", 2, 200.0, 0.0)
    assert row.rel_mae_pct == pytest.approx(26.67, abs=0.01)


def test_pv_night_slots_are_ignored(quality, archive, ledger):
    night = at("2026-10-15T00:00:00+00:00")
    archive.record("pv", at("2026-10-14T21:00:00+00:00"), {night: RecordValue(0, 0)})
    ingest(ledger, "2026-10-15T00:00:00+00:00", {"grid>house": 100})
    assert quality.update(DAY) == 0
    assert quality.summary(DAY, DAY) == []


def test_pv_counts_slots_with_forecast_or_actual_above_threshold(quality, archive, ledger):
    forecast_only = at("2026-10-15T09:00:00+00:00")  # Prognose 500 W, Ist 0 W
    actual_only = at("2026-10-15T09:15:00+00:00")  # Prognose 0 W, Ist 400 W
    neither = at("2026-10-15T09:30:00+00:00")  # Prognose 50 W, Ist 40 W
    archive.record(
        "pv",
        at("2026-10-15T06:00:00+00:00"),
        {
            forecast_only: RecordValue(500, 600),
            actual_only: RecordValue(0, 0),
            neither: RecordValue(50, 60),
        },
    )
    ingest(ledger, "2026-10-15T09:00:00+00:00", {"pv>house": 0.001})
    ingest(ledger, "2026-10-15T09:15:00+00:00", {"pv>house": 100})  # 400 W
    ingest(ledger, "2026-10-15T09:30:00+00:00", {"pv>house": 10})  # 40 W
    assert quality.update(DAY) == 1
    [row] = quality.summary(DAY, DAY)
    assert (row.kind, row.horizon, row.n) == ("pv", "h0_6", 2)
    assert row.mae_w == pytest.approx((500 - 0.004 + 400) / 2)
    assert row.bias_w == pytest.approx((500 - 0.004 - 400) / 2)


def test_slots_without_actual_are_ignored(quality, archive, ledger):
    s1, s2 = at("2026-10-15T08:00:00+00:00"), at("2026-10-15T08:15:00+00:00")
    archive.record(
        "load", at("2026-10-15T05:00:00+00:00"), {s1: RecordValue(1, 1), s2: RecordValue(1, 1)}
    )
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 1}, covered_s=300.0)  # unvollständig
    assert quality.update(DAY) == 0


def test_update_replaces_the_day(quality, archive, ledger):
    fill_load_day(archive, ledger)
    quality.update(DAY)
    quality.update(DAY)
    assert [r.n for r in quality.summary(DAY, DAY)] == [2]


def test_update_removes_rows_that_no_longer_apply(quality, archive, ledger, engine):
    fill_load_day(archive, ledger)
    quality.update(DAY)
    archive.cleanup(at("2026-10-16T00:00:00+00:00"))
    assert quality.update(DAY) == 0
    assert quality.summary(DAY, DAY) == []


def test_update_only_touches_its_own_day(quality, archive, ledger):
    fill_load_day(archive, ledger)
    quality.update(DAY)
    assert quality.update(date(2026, 10, 14)) == 0
    assert [r.n for r in quality.summary(DAY, DAY)] == [2]


def test_summary_sums_over_days_and_orders_rows(quality, archive, ledger):
    fill_load_day(archive, ledger)
    # zweiter Tag: PV h0_6 und Last h6_24
    s1, s2 = at("2026-10-16T09:00:00+00:00"), at("2026-10-16T09:15:00+00:00")
    archive.record("pv", at("2026-10-16T06:00:00+00:00"), {s1: RecordValue(1000, 1000)})
    archive.record("load", at("2026-10-15T16:00:00+00:00"), {s2: RecordValue(300, 300)})
    ingest(ledger, "2026-10-16T09:00:00+00:00", {"pv>grid": 200})  # PV 800 W
    ingest(ledger, "2026-10-16T09:15:00+00:00", {"grid>house": 50})  # Last 200 W
    quality.update(DAY)
    quality.update(date(2026, 10, 16))
    rows = quality.summary(DAY, date(2026, 10, 16))
    assert [(r.kind, r.horizon, r.n) for r in rows] == [
        ("pv", "h0_6", 1),
        ("load", "h0_6", 2),
        ("load", "h6_24", 1),
    ]
    assert rows[0].mae_w == 200.0 and rows[0].bias_w == 200.0
    assert rows[0].rel_mae_pct == pytest.approx(25.0)
    assert rows[2].mae_w == 100.0 and rows[2].rel_mae_pct == pytest.approx(50.0)


def test_relative_error_is_none_without_actual_energy(quality, archive, ledger):
    s = at("2026-10-15T08:00:00+00:00")
    archive.record("load", at("2026-10-15T05:00:00+00:00"), {s: RecordValue(100, 100)})
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 0})
    quality.update(DAY)
    [row] = quality.summary(DAY, DAY)
    assert row.mae_w == 100.0 and row.rel_mae_pct is None


def test_quality_without_records_is_empty(quality):
    assert quality.summary(date(2026, 10, 1), date(2026, 10, 15)) == []


def test_quality_rates_shadow_kinds(quality, archive, ledger):
    """Alle vier Arten zählen; das Ist der drei ``load*`` ist die Hauslast, nicht die PV."""
    slot = at("2026-10-15T08:00:00+00:00")
    issued = at("2026-10-15T05:00:00+00:00")
    for kind, watts in (("load_model", 900), ("load_profile", 600), ("load", 900), ("pv", 1000)):
        archive.record(kind, issued, {slot: RecordValue(watts, watts)})
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 200, "pv>grid": 250})  # 800 / 1000 W
    assert quality.update(DAY) == 4
    rows = quality.summary(DAY, DAY)
    assert [(r.kind, r.horizon, r.n, r.mae_w, r.bias_w) for r in rows] == [
        ("pv", "h0_6", 1, 0.0, 0.0),
        ("load", "h0_6", 1, 100.0, 100.0),
        ("load_profile", "h0_6", 1, 200.0, -200.0),
        ("load_model", "h0_6", 1, 100.0, 100.0),
    ]
