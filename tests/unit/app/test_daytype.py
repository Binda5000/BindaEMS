from datetime import UTC, date, datetime, time

from bindaems.app.daytype import day_type, slot_key


def test_day_types() -> None:
    assert day_type(date(2026, 10, 16)) == "workday"  # Freitag
    assert day_type(date(2026, 10, 17)) == "saturday"
    assert day_type(date(2026, 10, 18)) == "sunday"
    assert day_type(date(2026, 10, 26)) == "sunday"  # Nationalfeiertag, Montag
    assert day_type(date(2026, 12, 8)) == "sunday"  # Mariä Empfängnis


def test_holiday_on_saturday_is_sunday() -> None:
    assert day_type(date(2026, 12, 26)) == "sunday"  # Stephanstag, Samstag


def test_slot_key_uses_local_time() -> None:
    assert slot_key(datetime(2026, 10, 16, 22, 15, tzinfo=UTC)) == ("saturday", time(0, 15))


def test_slot_key_on_autumn_dst_day_repeats_0200() -> None:
    first = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # 02:00 MESZ
    second = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)  # 02:00 MEZ
    assert slot_key(first) == slot_key(second) == ("sunday", time(2, 0))
