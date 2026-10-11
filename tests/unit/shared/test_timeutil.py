from datetime import UTC, date, datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.shared.timeutil import (
    LOCAL_TZ,
    AnchoredClock,
    Clock,
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


def test_anchored_clock_ignores_wall_jumps() -> None:
    walls = iter([T0, T0 - timedelta(hours=1)])
    mono = [100.0]
    clock = AnchoredClock(wall=lambda: next(walls), monotonic=lambda: mono[0])
    mono[0] = 105.0
    assert clock.now() == T0 + timedelta(seconds=5)
    assert clock.now() == T0 + timedelta(seconds=5)  # die Wanduhr wird nicht neu gelesen


def test_anchored_clock_reads_wall_and_monotonic_once_at_start() -> None:
    reads = {"wall": 0, "monotonic": 0}

    def wall() -> datetime:
        reads["wall"] += 1
        return T0

    def monotonic() -> float:
        reads["monotonic"] += 1
        return 42.0

    clock = AnchoredClock(wall=wall, monotonic=monotonic)
    assert reads == {"wall": 1, "monotonic": 1}  # der Konstruktor setzt den Anker
    clock.now()
    clock.now()
    assert reads == {"wall": 1, "monotonic": 3}  # danach liest now() nur noch die monotone Uhr


def test_anchored_clock_follows_the_monotonic_clock() -> None:
    mono = [1000.0]
    clock = AnchoredClock(wall=lambda: T0, monotonic=lambda: mono[0])
    assert clock.now() == T0
    mono[0] += 0.25
    assert clock.now() == T0 + timedelta(milliseconds=250)
    mono[0] += 3600.0
    assert clock.now() == T0 + timedelta(hours=1, milliseconds=250)


def test_anchored_clock_gives_utc() -> None:
    vienna = T0.astimezone(LOCAL_TZ)
    clock = AnchoredClock(wall=lambda: vienna, monotonic=lambda: 0.0)
    assert clock.now() == T0 and clock.now().utcoffset() == timedelta(0)


def test_anchored_clock_refuses_a_naive_wall_time() -> None:
    with pytest.raises(ValueError, match="naiv"):
        AnchoredClock(wall=lambda: datetime(2026, 10, 8, 10, 0), monotonic=lambda: 0.0)


def test_anchored_clock_defaults_are_the_real_clocks() -> None:
    clock: Clock = AnchoredClock()
    assert abs(clock.now() - datetime.now(UTC)) < timedelta(seconds=5)
    assert clock.now().tzinfo is not None
