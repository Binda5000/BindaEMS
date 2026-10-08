from datetime import UTC, date, datetime, timedelta

import pytest

from bindaems.shared.timeutil import (
    LOCAL_TZ,
    ManualClock,
    ensure_utc,
    local_day_slots,
    slot_end,
    slot_start,
    slots_between,
)


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1, 12, 0))


def test_ensure_utc_converts_vienna() -> None:
    assert ensure_utc(datetime(2026, 7, 1, 12, 0, tzinfo=LOCAL_TZ)) == datetime(
        2026, 7, 1, 10, 0, tzinfo=UTC
    )


def test_slot_start_floors_to_quarter_hour() -> None:
    assert slot_start(datetime(2026, 10, 8, 9, 44, 59, tzinfo=UTC)) == datetime(
        2026, 10, 8, 9, 30, tzinfo=UTC
    )


def test_slot_end() -> None:
    assert slot_end(datetime(2026, 10, 8, 9, 30, tzinfo=UTC)) == datetime(
        2026, 10, 8, 9, 45, tzinfo=UTC
    )


def test_slots_between_half_open() -> None:
    s = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert slots_between(s, s + timedelta(hours=1)) == [
        s + timedelta(minutes=m) for m in (0, 15, 30, 45)
    ]


def test_local_day_slots_normal_day() -> None:
    slots = local_day_slots(date(2026, 10, 8))
    assert len(slots) == 96 and slots[0] == datetime(2026, 10, 7, 22, 0, tzinfo=UTC)


def test_local_day_slots_dst_spring() -> None:
    slots = local_day_slots(date(2026, 3, 29))
    assert len(slots) == 92 and slots[0] == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)


def test_local_day_slots_dst_autumn() -> None:
    slots = local_day_slots(date(2026, 10, 25))
    assert len(slots) == 100 and slots[0] == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)


def test_manual_clock_advance() -> None:
    c = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    c.advance(1.5)
    assert c.now() == datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)
