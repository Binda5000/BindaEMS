from datetime import UTC, date, datetime, time, timedelta

from bindaems.app.learning.driving_data import PresencePoint, VehicleHistory
from bindaems.app.learning.driving_days import DayRecord, day_records
from bindaems.shared.timeutil import LOCAL_TZ

MON = date(2026, 11, 23)
TUE = date(2026, 11, 24)
TODAY = date(2026, 11, 30)


def at(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def pt(day: date, clock: str, home: bool) -> PresencePoint:
    return PresencePoint(at(day, clock), home)


def history(
    *presence: PresencePoint,
    seed: bool | None = True,
    sessions: list[tuple[datetime, datetime | None]] | None = None,
    soc: list[tuple[datetime, float]] | None = None,
    source: str = "ha",
) -> VehicleHistory:
    return VehicleHistory(
        "tesla",
        source,  # type: ignore[arg-type]
        list(presence),
        seed,
        sessions or [],
        soc or [],
    )


def record(hist: VehicleHistory, day: date, today: date = TODAY) -> DayRecord:
    found = [r for r in day_records(hist, today=today) if r.day == day]
    assert len(found) == 1
    return found[0]


def test_commute_day() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "07:10"), 80.0), (at(MON, "17:50"), 62.0)],
    )
    rec = record(hist, MON)
    assert not rec.away
    assert rec.weekday == 0
    assert rec.departure == at(MON, "07:20")
    assert rec.departure_soc == 80.0
    assert rec.return_at == at(MON, "17:45")
    assert rec.arrival == at(MON, "17:45")
    assert rec.return_soc == rec.arrival_soc == 62.0
    assert rec.consumption_pp == 18.0


def test_departure_uses_unplug_within_30_min() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        sessions=[(at(MON - timedelta(days=1), "18:00"), at(MON, "07:05"))],
    )
    assert record(hist, MON).departure == at(MON, "07:05")


def test_departure_ignores_unplug_older_than_30_min() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        sessions=[(at(MON - timedelta(days=1), "18:00"), at(MON, "06:40"))],
    )
    assert record(hist, MON).departure == at(MON, "07:20")


def test_day_at_home_is_no_trip() -> None:
    rec = record(history(pt(MON, "07:20", False), pt(MON, "17:45", True)), TUE)
    assert not rec.away
    assert rec.departure is None
    assert rec.return_at is None
    assert rec.consumption_pp is None


def test_multi_day_trip_marks_days_away() -> None:
    hist = history(
        pt(MON, "08:00", False),
        pt(MON + timedelta(days=3), "19:00", True),
        soc=[(at(MON, "07:50"), 90.0), (at(MON + timedelta(days=3), "19:05"), 30.0)],
    )
    first = record(hist, MON)
    assert first.departure == at(MON, "08:00")
    assert first.return_at is None
    assert first.arrival is None
    assert first.consumption_pp is None
    assert record(hist, TUE).away
    assert record(hist, TUE + timedelta(days=1)).away
    back = record(hist, MON + timedelta(days=3))
    assert back.away
    assert back.departure is None


def test_noon_stop_does_not_split_the_trip() -> None:
    hist = history(
        pt(MON, "07:30", False),
        pt(MON, "12:00", True),
        pt(MON, "13:00", False),
        pt(MON, "18:00", True),
        soc=[(at(MON, "07:20"), 70.0), (at(MON, "12:10"), 50.0), (at(MON, "18:05"), 40.0)],
    )
    rec = record(hist, MON)
    assert rec.departure == at(MON, "07:30")
    assert rec.return_at == at(MON, "18:00")
    assert rec.return_soc == 40.0
    assert rec.consumption_pp == 30.0


def test_departure_after_midnight_belongs_to_new_day() -> None:
    hist = history(
        pt(MON, "18:00", True),
        pt(TUE, "00:30", False),
        pt(TUE, "06:00", True),
        soc=[(at(TUE, "00:20"), 80.0), (at(TUE, "06:05"), 66.0)],
    )
    # Der Montag endet zu Hause und bekommt die Abfahrt nicht
    mon = record(hist, MON)
    assert mon.departure is None
    assert mon.return_at is None
    tue = record(hist, TUE)
    assert not tue.away
    assert tue.departure == at(TUE, "00:30")
    assert tue.arrival == at(TUE, "06:00")
    # Rückkehr ist der Aufenthalt, der die folgende Nacht umfasst, auch nach einer Abfahrt vor 03:00
    assert tue.return_at == at(TUE, "06:00")
    assert tue.return_soc == 66.0
    assert tue.consumption_pp == 14.0


def test_arrival_before_midnight_stay_through_midnight() -> None:
    hist = history(
        pt(MON, "07:00", False),
        pt(MON, "19:00", True),
        pt(TUE, "00:30", False),
        pt(TUE, "05:00", True),
    )
    mon = record(hist, MON)
    assert mon.return_at == at(MON, "19:00")
    assert mon.arrival == at(MON, "19:00")
    assert record(hist, TUE).departure == at(TUE, "00:30")


def test_arrival_after_midnight_is_return_only() -> None:
    hist = history(pt(MON, "20:00", False), pt(TUE, "01:00", True))
    mon = record(hist, MON)
    assert mon.departure == at(MON, "20:00")
    assert mon.return_at == at(TUE, "01:00")
    assert mon.arrival is None


def test_holiday_counts_as_sunday() -> None:
    holiday = date(2026, 12, 8)  # Dienstag, Mariä Empfängnis
    hist = history(pt(holiday, "09:00", False), pt(holiday, "12:00", True))
    rec = record(hist, holiday, today=date(2026, 12, 15))
    assert holiday.weekday() == 1
    assert rec.weekday == 6


def test_return_soc_before_charging() -> None:
    plug = at(MON, "18:00")
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        sessions=[(plug, None)],
        soc=[(at(MON, "07:10"), 80.0), (at(MON, "18:06"), 75.0)],
    )
    rec = record(hist, MON)
    assert rec.return_soc is None
    assert rec.consumption_pp is None
    assert rec.departure_soc == 80.0
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        sessions=[(plug, None)],
        soc=[(at(MON, "07:10"), 80.0), (at(MON, "18:04"), 75.0)],
    )
    assert record(hist, MON).return_soc == 75.0


def test_return_soc_window_is_30_minutes() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "07:10"), 80.0), (at(MON, "18:20"), 70.0)],
    )
    assert record(hist, MON).return_soc is None


def test_departure_soc_looks_back_six_hours() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "01:00"), 99.0), (at(MON, "01:30"), 85.0), (at(MON, "07:00"), 83.0)],
    )
    assert record(hist, MON).departure_soc == 83.0
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "01:00"), 99.0)],
    )
    assert record(hist, MON).departure_soc is None


def test_consumption_can_be_negative() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "07:10"), 40.0), (at(MON, "17:50"), 55.0)],
    )
    assert record(hist, MON).consumption_pp == -15.0


def test_day_without_known_state_is_skipped() -> None:
    hist = history(pt(MON, "12:00", False), pt(TUE, "08:00", True), seed=None)
    days = [r.day for r in day_records(hist, today=TODAY)]
    assert MON not in days  # vor dem ersten Punkt ist der Zustand ab 00:00 unbekannt
    assert days[0] == TUE
    assert record(hist, TUE).away  # um 03:00 und 00:00 ist das Auto weg


def test_known_from_earlier_point_counts() -> None:
    hist = history(pt(MON, "00:00", True), pt(MON, "08:00", False), seed=None)
    assert record(hist, MON).departure == at(MON, "08:00")


def test_no_data_at_all() -> None:
    assert day_records(history(seed=None), today=TODAY) == []
    assert day_records(history(seed=None, source="sessions"), today=TODAY) == []


def test_seed_away_without_points() -> None:
    days = day_records(history(seed=False), today=TODAY)
    assert days
    assert all(r.away for r in days)
    assert days[-1].day == TODAY - timedelta(days=1)


def test_seed_home_without_points_covers_window() -> None:
    days = day_records(history(seed=True), today=TODAY)
    assert days[0].day == TODAY - timedelta(days=56)
    assert all(not r.away and r.departure is None for r in days)


def test_sessions_only_history() -> None:
    hist = history(
        seed=None,
        source="sessions",
        sessions=[
            (at(MON - timedelta(days=1), "18:30"), at(MON, "07:35")),
            (at(MON, "17:50"), at(MON + timedelta(days=1), "07:30")),
        ],
        soc=[(at(MON, "07:30"), 88.0), (at(MON, "17:52"), 71.0)],
    )
    days = [r.day for r in day_records(hist, today=TODAY)]
    assert days[0] == MON  # der Tag des ersten Ansteckvorgangs ab 00:00 unbekannt
    rec = record(hist, MON)
    assert rec.departure == at(MON, "07:35")
    assert rec.return_at == at(MON, "17:50")
    assert rec.departure_soc == 88.0
    assert rec.return_soc == 71.0
    assert rec.consumption_pp == 17.0


def test_sessions_only_with_open_session_counts_as_home() -> None:
    hist = history(
        seed=None,
        source="sessions",
        sessions=[(at(MON - timedelta(days=1), "18:30"), None)],
    )
    rec = record(hist, TODAY - timedelta(days=1))
    assert not rec.away
    assert rec.departure is None


def test_today_is_not_rated() -> None:
    hist = history(pt(MON, "07:20", False), pt(MON, "17:45", True))
    assert day_records(hist, today=MON)[-1].day == MON - timedelta(days=1)
    assert all(r.day < TODAY for r in day_records(hist, today=TODAY))


def test_open_departure_has_no_return() -> None:
    last = TODAY - timedelta(days=1)
    rec = record(history(pt(last, "07:20", False)), last)
    assert rec.departure == at(last, "07:20")
    assert rec.return_at is None


def test_spring_forward_day_uses_local_clock() -> None:
    day = date(2026, 3, 29)  # 02:00 wird 03:00
    hist = history(pt(day, "07:20", False), pt(day, "17:45", True))
    rec = record(hist, day, today=date(2026, 4, 5))
    assert rec.departure == datetime(2026, 3, 29, 5, 20, tzinfo=UTC)  # 07:20 MESZ


def test_autumn_day_after_night_departure() -> None:
    day = date(2026, 10, 25)  # 03:00 MESZ wird 02:00 MEZ
    hist = history(pt(day, "01:30", False), pt(day, "09:00", True))
    rec = record(hist, day, today=date(2026, 11, 2))
    assert rec.departure == at(day, "01:30")
    assert rec.return_at == at(day, "09:00")  # Rückkehr in die folgende Nacht


def test_presence_points_with_repeated_states() -> None:
    hist = history(
        pt(MON, "06:00", True),
        pt(MON, "07:20", False),
        pt(MON, "07:25", False),
        pt(MON, "17:45", True),
        pt(MON, "17:50", True),
    )
    rec = record(hist, MON)
    assert rec.departure == at(MON, "07:20")
    assert rec.return_at == at(MON, "17:45")


def test_overlapping_and_adjacent_sessions_form_one_stay() -> None:
    hist = history(
        seed=None,
        source="sessions",
        sessions=[
            (at(MON, "20:00"), at(TUE, "01:00")),
            (at(TUE, "00:30"), at(TUE, "12:00")),
            (at(TUE, "12:00"), at(TUE, "15:00")),
        ],
    )
    rec = record(hist, TUE)
    assert not rec.away
    assert rec.departure == at(TUE, "15:00")  # ohne Zusammenführen: 12:00


def test_empty_session_does_not_hide_the_stay() -> None:
    hist = history(
        seed=None,
        source="sessions",
        sessions=[(at(MON, "20:00"), at(TUE, "12:00")), (at(TUE, "02:00"), at(TUE, "02:00"))],
    )
    rec = record(hist, TUE)
    assert not rec.away
    assert rec.departure == at(TUE, "12:00")


def utc(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def test_summer_departure_after_local_midnight_belongs_to_new_day() -> None:
    # 00:30 MESZ am 15.7. ist 22:30 UTC am 14.7.; mit festem +01:00 läge sie noch am 14.7.
    hist = history(
        PresencePoint(utc(7, 14, 22, 30), False),
        PresencePoint(utc(7, 15, 4), True),
    )
    today = date(2026, 7, 20)
    assert record(hist, date(2026, 7, 14), today).departure is None
    rec = record(hist, date(2026, 7, 15), today)
    assert not rec.away
    assert rec.departure == utc(7, 14, 22, 30)
    assert rec.return_at == utc(7, 15, 4)


def test_spring_forward_day_is_23_hours_with_dst_anchor() -> None:
    # 29.3.2026: 03:00 MESZ ist 01:00 UTC, der Tag dauert 23 Stunden (28.3. 23:00 – 29.3. 22:00 UTC)
    hist = history(
        PresencePoint(utc(3, 29, 0, 30), True),
        PresencePoint(utc(3, 29, 1, 50), False),
        PresencePoint(utc(3, 29, 16), True),
        PresencePoint(utc(3, 29, 22, 30), False),  # 00:30 MESZ am 30.3.
        PresencePoint(utc(3, 30, 5), True),
        seed=False,
    )
    today = date(2026, 4, 5)
    sunday = record(hist, date(2026, 3, 29), today)
    assert not sunday.away  # um 03:00 MESZ (01:00 UTC) zu Hause; mit festem +01:00 wäre es weg
    assert sunday.departure == utc(3, 29, 1, 50)
    assert sunday.return_at == sunday.arrival == utc(3, 29, 16)
    monday = record(hist, date(2026, 3, 30), today)
    assert monday.departure == utc(3, 29, 22, 30)
    assert monday.return_at == utc(3, 30, 5)


def test_autumn_day_is_25_hours_with_winter_anchor() -> None:
    # 25.10.2026: der Tag beginnt 24.10. 22:00 UTC; 03:00 MEZ ist 02:00 UTC
    hist = history(
        PresencePoint(utc(10, 24, 22, 30), False),  # 00:30 MESZ am 25.10.
        PresencePoint(utc(10, 25, 5), True),
        seed=True,
    )
    today = date(2026, 11, 2)
    assert record(hist, date(2026, 10, 24), today).departure is None
    rec = record(hist, date(2026, 10, 25), today)
    assert not rec.away
    assert rec.departure == utc(10, 24, 22, 30)
    assert rec.arrival == utc(10, 25, 5)
