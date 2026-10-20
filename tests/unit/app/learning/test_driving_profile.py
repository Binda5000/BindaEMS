from datetime import UTC, date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from bindaems.app.daytype import is_holiday
from bindaems.app.learning.driving_days import DayRecord
from bindaems.app.learning.driving_profile import (
    TripSuggestion,
    VehicleProfile,
    WeekdayProfile,
    build_profile,
    next_trip,
    percentile,
    presence_at,
)
from bindaems.shared.settings import DrivingSettings
from bindaems.shared.timeutil import LOCAL_TZ

TODAY = date(2026, 12, 1)
SETTINGS = DrivingSettings()


def local(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def minute_of_day(day: date, minutes: float) -> datetime:
    return local(day, "00:00") + timedelta(minutes=minutes)


def days_of(weekday: int, count: int, *, before: date = TODAY) -> list[date]:
    """Die letzten ``count`` Tage dieses Wochentags vor ``before``, ohne Feiertage."""
    found: list[date] = []
    day = before - timedelta(days=1)
    while len(found) < count:
        if day.weekday() == weekday and not is_holiday(day):
            found.append(day)
        day -= timedelta(days=1)
    return sorted(found)


def rec(
    day: date,
    *,
    departure: float | None = 450,
    consumption: float | None = None,
    arrival: float | None = None,
    arrival_soc: float | None = None,
    away: bool = False,
) -> DayRecord:
    """Ein Tag; ``departure`` und ``arrival`` in Minuten des lokalen Tages."""
    return DayRecord(
        day=day,
        weekday=day.weekday(),
        away=away,
        departure=None if departure is None else minute_of_day(day, departure),
        departure_soc=None,
        arrival=None if arrival is None else minute_of_day(day, arrival),
        arrival_soc=arrival_soc,
        return_at=None,
        return_soc=None,
        consumption_pp=consumption,
    )


def saturdays(departures: list[float | None], consumptions: list[float | None] | None = None):
    days = days_of(5, len(departures))
    values = consumptions or [None] * len(departures)
    return [
        rec(d, departure=dep, consumption=c)
        for d, dep, c in zip(days, departures, values, strict=True)
    ]


def profile_of(records: list[DayRecord], sessions=(), settings=SETTINGS) -> VehicleProfile:
    return build_profile("tesla", "ha", records, list(sessions), settings, today=TODAY)


def test_percentile_linear() -> None:
    assert percentile([420, 430, 440, 450, 460], 20) == 428.0
    assert percentile([460, 420, 440, 430, 450], 50) == 440.0
    assert percentile([10, 20], 50) == 15.0
    assert percentile([7], 80) == 7.0
    assert percentile([1, 2, 3], 0) == 1.0
    assert percentile([1, 2, 3], 100) == 3.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_departure_is_p20_floored_to_quarter() -> None:
    # P20 liegt bei 448 min = 07:28 und wird zu 07:15
    profile = profile_of(saturdays([448, 448, 450, 480, 500]))
    sat = profile.weekdays[5]
    assert sat.departure_min == 7 * 60 + 15
    assert sat.trips == 5
    assert sat.trip_share == 1.0
    assert sat.departure_p50_min == 450
    assert sat.departure_p80_min == 484


def test_no_trip_expected_below_half() -> None:
    sat = profile_of(saturdays([480, 500, None, None, None])).weekdays[5]
    assert sat.trips == 2
    assert sat.trip_share == 0.4
    assert sat.departure_min is None
    assert sat.departure_confidence is None


def test_half_of_the_days_is_enough_for_a_trip() -> None:
    sat = profile_of(saturdays([480, 500, 520, None, None, None])).weekdays[5]
    assert sat.trip_share == 0.5
    assert sat.departure_min is not None


def test_away_days_are_not_rated() -> None:
    records = [*saturdays([480, 480, 480]), rec(date(2026, 11, 7), departure=None, away=True)]
    sat = profile_of(records).weekdays[5]
    assert sat.days == 3
    assert sat.trip_share == 1.0


def test_group_used_with_fewer_than_three_days() -> None:
    records = [
        rec(day, departure=420 + 10 * weekday)
        for weekday in range(5)
        for day in days_of(weekday, 2)
    ]
    profile = profile_of(records)
    monday = profile.weekdays[0]
    assert monday.basis == "group"
    assert monday.days == 10
    assert monday.departure_min == 420  # P20 der zehn Fahrten
    assert profile.weekdays[4].basis == "group"
    # die Gruppe gilt auch für Tage ohne eigene Datensätze
    assert profile_of(records[:2]).weekdays[3].days == 2


def test_weekday_used_with_three_days() -> None:
    records = [rec(day, departure=420) for day in days_of(0, 3)]
    records += [rec(day, departure=600) for day in days_of(1, 3)]
    profile = profile_of(records)
    assert profile.weekdays[0].basis == "weekday"
    assert profile.weekdays[0].days == 3
    assert profile.weekdays[0].departure_min == 420
    assert profile.weekdays[1].departure_min == 600


def test_saturday_and_sunday_pool_only_themselves() -> None:
    profile = profile_of(saturdays([480, 480, 480]))
    assert profile.weekdays[6].days == 0
    assert profile.weekdays[6].departure_min is None
    assert profile.weekdays[0].days == 0


def test_holiday_records_feed_weekday_six() -> None:
    holidays = [date(2026, 11, 1), date(2025, 12, 25), date(2025, 12, 26)]
    records = [
        DayRecord(d, 6, False, minute_of_day(d, 600), None, None, None, None, None, None)
        for d in holidays
    ]
    assert profile_of(records).weekdays[6].departure_min == 600


def test_target_rounding_tolerates_float_noise() -> None:
    # P80 von 18,1, 18,1 und 29,6 ist 25,000000000000004 und darf nicht auf 30 aufrunden
    settings = DrivingSettings(return_reserve_pct=0, target_min_pct=20)
    sat = profile_of(saturdays([480] * 3, [29.6, 18.1, 18.1]), settings=settings).weekdays[5]
    assert sat.consumption_p80_pp is not None
    assert sat.consumption_p80_pp > 25.0
    assert sat.target_soc_pct == 25


def test_target_is_reserve_plus_p80_rounded_up_to_5() -> None:
    # 20 + 22,4 = 42,4 → 45, begrenzt auf mindestens 50
    sat = profile_of(saturdays([480] * 3, [22.4] * 3)).weekdays[5]
    assert sat.target_soc_pct == 50
    assert sat.target_clamped is True
    # 20 + 32,4 = 52,4 → 55, innerhalb der Grenzen
    sat = profile_of(saturdays([480] * 3, [32.4] * 3)).weekdays[5]
    assert sat.target_soc_pct == 55
    assert sat.target_clamped is False


def test_target_on_exact_multiple_of_five_is_not_rounded_up() -> None:
    sat = profile_of(saturdays([480] * 3, [35.0] * 3)).weekdays[5]
    assert sat.target_soc_pct == 55
    assert sat.target_clamped is False


def test_target_uses_p80_of_consumption() -> None:
    # P80 von 10, 20, 30, 40, 50 ist 42: 20 + 42 = 62 → 65
    sat = profile_of(saturdays([480] * 5, [10, 20, 30, 40, 50])).weekdays[5]
    assert sat.consumption_p80_pp == 42.0
    assert sat.consumption_p50_pp == 30.0
    assert sat.target_soc_pct == 65


def test_target_clamped_to_max() -> None:
    sat = profile_of(saturdays([480] * 3, [80.0] * 3)).weekdays[5]  # 20 + 80 = 100 → 90
    assert sat.target_soc_pct == 90
    assert sat.target_clamped is True


def test_target_follows_the_settings() -> None:
    settings = DrivingSettings(return_reserve_pct=10, target_min_pct=30, target_max_pct=60)
    sat = profile_of(saturdays([480] * 3, [31.0] * 3), settings=settings).weekdays[5]
    assert sat.target_soc_pct == 45  # 10 + 31 = 41 → 45


def test_negative_consumption_counts_as_zero_for_target() -> None:
    settings = DrivingSettings(target_min_pct=20)
    sat = profile_of(saturdays([480] * 3, [-10.0] * 3), settings=settings).weekdays[5]
    assert sat.consumption_p80_pp == -10.0  # das Profil behält den echten Wert
    assert sat.target_soc_pct == 20  # nur die Reserve
    assert sat.target_clamped is False
    clamped = profile_of(saturdays([480] * 3, [-10.0] * 3)).weekdays[5]
    assert clamped.target_soc_pct == 50
    assert clamped.target_clamped is True


def test_no_soc_gives_departure_but_no_target() -> None:
    sat = profile_of(saturdays([480] * 4)).weekdays[5]
    assert sat.departure_min == 480
    assert sat.target_soc_pct is None
    assert sat.target_clamped is False
    assert sat.target_confidence is None
    assert sat.consumption_p80_pp is None
    assert sat.arrival_soc_p50 is None


def test_arrival_values_come_from_the_rated_days() -> None:
    days = days_of(5, 3)
    records = [
        rec(days[0], arrival=1000, arrival_soc=40.0),
        rec(days[1], arrival=1020, arrival_soc=50.0),
        rec(days[2], arrival=None, arrival_soc=None),
    ]
    sat = profile_of(records).weekdays[5]
    assert sat.arrival_p50_min == 1010
    assert sat.arrival_soc_p50 == 45.0


@pytest.mark.parametrize(
    ("trips", "spread", "expected"),
    [
        (6, 60, "high"),
        (6, 61, "medium"),
        (6, 120, "medium"),
        (6, 121, "low"),
        (5, 0, "medium"),
        (3, 0, "medium"),
        (2, 0, "low"),
        (1, 0, "low"),
    ],
)
def test_departure_confidence_levels(trips: int, spread: float, expected: str) -> None:
    # sortiert ergeben die Werte P20 = Wert 1 und P80 = Wert 4 bei sechs Werten
    half = trips // 2
    times = [400.0] * half + [400.0 + spread] * (trips - half)
    sat = profile_of(saturdays(times)).weekdays[5]
    assert sat.departure_confidence == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([10, 10, 10, 25, 25, 25], "high"),  # Spanne 15 pp bei sechs Werten
        ([10, 10, 10, 26, 26, 26], "medium"),
        ([10, 10, 10, 40, 40, 40], "medium"),  # Spanne 30 pp
        ([10, 10, 10, 41, 41, 41], "low"),
        ([10, 10, 10, 10, 10], "medium"),  # nur fünf Werte
        ([10, 10, 10], "medium"),
        ([10, 10], "low"),
        ([10], "low"),
    ],
)
def test_target_confidence_levels(values: list[float], expected: str) -> None:
    sat = profile_of(saturdays([480] * len(values), values)).weekdays[5]
    assert sat.target_confidence == expected


def test_consumption_values_count_for_confidence_not_trip_days() -> None:
    # sechs Fahrten, aber nur zwei Verbrauchswerte
    sat = profile_of(saturdays([480] * 6, [10, 10, None, None, None, None])).weekdays[5]
    assert sat.departure_confidence == "high"
    assert sat.target_confidence == "low"


def test_group_basis_is_never_high() -> None:
    # zehn Fahrten, Spanne 0, aber kein Wochentag mit drei eigenen Tagen
    records = [rec(day, departure=450, consumption=20.0) for w in range(5) for day in days_of(w, 2)]
    monday = profile_of(records).weekdays[0]
    assert monday.basis == "group"
    assert monday.trips == 10
    assert monday.departure_confidence == "medium"
    # beim Ziel gibt es keine solche Einschränkung
    assert monday.target_confidence == "high"


def test_group_with_few_days_is_low() -> None:
    records = [rec(days_of(0, 1)[0], departure=450), rec(days_of(1, 1)[0], departure=450)]
    assert profile_of(records).weekdays[0].departure_confidence == "low"


def test_presence_share_per_quarter() -> None:
    mondays = days_of(0, 5)  # 2. bis 30. November
    sessions = [(local(d, "18:00"), local(d, "22:00")) for d in mondays[:3]]
    # Montag 16.11. bis Dienstag 17.11. über Nacht
    sessions.append((local(mondays[2], "23:00"), local(mondays[2] + timedelta(days=1), "07:00")))
    profile = profile_of([], sessions)
    monday = profile.presence[0]
    assert len(profile.presence) == 7
    assert all(len(row) == 96 for row in profile.presence)
    assert monday[19 * 4] == 0.6  # 19:00: drei von fünf Montagen
    assert monday[18 * 4] == 0.6
    assert monday[17 * 4 + 3] == 0.0  # 17:45
    assert monday[22 * 4] == 0.0  # 22:00 endet vor der Mitte der Viertelstunde 22:00
    assert monday[21 * 4 + 3] == 0.6
    assert monday[23 * 4 + 3] == 0.2  # nur die Nacht auf den 17.11.
    assert monday[12 * 4] == 0.0
    tuesday = profile.presence[1]  # vier Dienstage: der 3., 10., 17. und 24.11.
    assert tuesday[3 * 4] == 0.25
    assert tuesday[7 * 4 + 1] == 0.0  # 07:15: Vorgang schon beendet
    assert profile.presence[5] == [0.0] * 96


def test_presence_counts_days_from_the_first_session() -> None:
    # erster Vorgang am Montag, den 23.11.; davor zählt kein Tag
    mondays = days_of(0, 2)
    profile = profile_of([], [(local(mondays[1], "10:00"), local(mondays[1], "12:00"))])
    assert profile.presence[0][10 * 4 + 1] == 1.0
    assert profile.presence[0][13 * 4] == 0.0


def test_presence_uses_the_group_when_the_weekday_has_few_days() -> None:
    # Erster Vorgang am Dienstag, den 24.11.: Der Montag hat bis heute nur einen Tag (30.11.)
    tue, wed, thu = date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 26)
    sessions = [(local(d, "18:00"), local(d, "22:00")) for d in (tue, wed, thu)]
    profile = profile_of([], sessions)
    assert profile.presence[0][19 * 4] == 0.6  # drei der fünf Werktage
    assert profile.presence[5][19 * 4] == 0.0


def test_open_session_covers_the_days_up_to_today() -> None:
    sessions = [(local(date(2026, 11, 28), "18:00"), None)]
    profile = profile_of([], sessions)
    assert profile.presence[6][3 * 4] == 1.0  # Sonntag, 29.11.
    assert profile.presence[6][19 * 4] == 1.0
    assert profile.presence[5][17 * 4 + 3] == 0.0  # Samstag, 28.11., 17:45 davor
    assert profile.presence[5][19 * 4] == 1.0


def test_presence_of_overlapping_sessions() -> None:
    sat = days_of(5, 1)[0]
    sessions = [
        (local(sat, "08:00"), local(sat, "12:00")),
        (local(sat, "09:00"), local(sat, "10:00")),
        (local(sat, "11:00"), local(sat, "13:00")),
    ]
    row = profile_of([], sessions).presence[5]
    assert row[10 * 4 + 2] == 1.0  # 10:30: das verschachtelte Ende liegt davor, das lange deckt ab
    assert row[11 * 4 + 2] == 1.0
    assert row[12 * 4 + 2] == 1.0
    assert row[13 * 4] == 0.0


def test_presence_counts_from_the_local_day_of_the_first_session() -> None:
    # Dienstag, 24.11., 00:30 lokal ist noch Montag, 23.11., 23:30 UTC
    tuesday = date(2026, 11, 24)
    first = (local(tuesday, "00:30"), local(tuesday, "02:00"))
    assert first[0].date() == date(2026, 11, 23)
    profile = profile_of([], [first])
    # Werktage 24., 25., 26., 27. und 30.11. (der Montag hat nur einen Tag): ein Treffer in fünf
    assert profile.presence[0][1 * 4] == 0.2
    assert profile.presence[1][1 * 4] == 0.2


def test_presence_needs_the_centre_of_the_quarter() -> None:
    sat = days_of(5, 1)[0]
    sessions = [
        (local(sat, "18:00"), local(sat, "18:07")),  # endet vor der Mitte 18:07:30
        (local(sat, "19:05"), local(sat, "19:10")),  # deckt die Mitte 19:07:30 ab
    ]
    row = profile_of([], sessions).presence[5]
    assert row[18 * 4] == 0.0
    assert row[19 * 4] == 1.0


def test_presence_without_sessions_is_zero() -> None:
    profile = profile_of(saturdays([480] * 3))
    assert profile.presence == [[0.0] * 96 for _ in range(7)]
    assert presence_at(profile, local(date(2026, 11, 30), "19:00")) == 0.0


def test_presence_on_dst_days_maps_local_quarters() -> None:
    # Herbstumstellung am 25.10.2026: 02:00 bis 03:00 kommt zweimal vor
    fall = date(2026, 10, 25)
    session = (
        datetime(2026, 10, 24, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
    )
    profile = build_profile("tesla", "sessions", [], [session], SETTINGS, today=date(2026, 10, 26))
    row = profile.presence[fall.weekday()]
    assert len(row) == 96
    assert row[6] == 1.0  # 01:30
    assert row[7] == 1.0  # 01:45
    assert row[8] == 0.5  # 02:00: nur das erste Vorkommen ist angesteckt
    assert row[9] == 0.5
    assert row[10] == 0.0
    assert row[0] == 0.0
    assert row[95] == 0.0

    # Frühjahrsumstellung am 28.03.2027: 02:00 bis 03:00 gibt es nicht
    spring = (datetime(2027, 3, 28, 0, 30, tzinfo=UTC), datetime(2027, 3, 28, 1, 30, tzinfo=UTC))
    profile = build_profile("tesla", "sessions", [], [spring], SETTINGS, today=date(2027, 3, 29))
    row = profile.presence[6]
    assert row[6] == 1.0  # 01:30
    assert row[7] == 1.0  # 01:45
    assert row[8:12] == [0.0] * 4  # die Stunde gibt es an diesem Tag nicht
    assert row[12] == 1.0  # 03:00
    assert row[13] == 1.0
    assert row[14] == 0.0


def test_presence_at_skipped_quarters_is_filled_by_other_days() -> None:
    spring = (datetime(2027, 3, 27, 22, 0, tzinfo=UTC), datetime(2027, 4, 5, 22, 0, tzinfo=UTC))
    profile = build_profile("tesla", "sessions", [], [spring], SETTINGS, today=date(2027, 4, 5))
    assert profile.presence[6][8:12] == [1.0] * 4  # die übrigen Sonntage und der Ostermontag


def test_presence_at_reads_the_local_quarter() -> None:
    mondays = days_of(0, 5)
    sessions = [(local(d, "18:00"), local(d, "22:00")) for d in mondays[:3]]
    profile = profile_of([], sessions)
    assert presence_at(profile, local(date(2026, 12, 7), "19:07")) == 0.6
    assert presence_at(profile, local(date(2026, 12, 7), "19:07") + timedelta(minutes=8)) == 0.6
    assert presence_at(profile, local(date(2026, 12, 7), "17:59")) == 0.0
    assert presence_at(profile, local(date(2026, 12, 8), "19:00")) == 0.0  # Dienstag und Feiertag


def test_presence_at_on_a_holiday_reads_weekday_six() -> None:
    sundays = days_of(6, 4)
    sessions = [(local(d, "18:00"), local(d, "22:00")) for d in sundays]
    profile = profile_of([], sessions)
    assert presence_at(profile, local(date(2026, 12, 8), "19:00")) == 1.0  # Mariä Empfängnis
    assert presence_at(profile, local(date(2026, 12, 15), "19:00")) == 0.0  # gewöhnlicher Dienstag


def workweek_records(departure: float = 435.0) -> list[DayRecord]:
    return [rec(day, departure=departure) for w in range(5) for day in days_of(w, 3)]


def test_next_trip_skips_days_without_trip() -> None:
    profile = profile_of(workweek_records())
    friday_evening = local(date(2026, 12, 4), "18:00")
    trip = next_trip(profile, friday_evening)
    assert trip == TripSuggestion(
        date=date(2026, 12, 7),
        departure="07:15",
        departure_confidence="medium",
        target_soc_pct=None,
        target_confidence=None,
    )


def test_next_trip_carries_the_target() -> None:
    records = [rec(day, departure=435.0, consumption=30.0) for day in days_of(0, 3)]
    trip = next_trip(profile_of(records), local(date(2026, 12, 6), "12:00"))
    assert trip is not None
    assert trip.date == date(2026, 12, 7)
    assert trip.target_soc_pct == 50
    assert trip.target_confidence == "medium"


def test_next_trip_after_todays_departure_passed() -> None:
    profile = profile_of(workweek_records())
    monday = date(2026, 12, 7)
    before = next_trip(profile, local(monday, "07:00"))
    assert before is not None
    assert before.date == monday
    # Dienstag ist der Feiertag 8.12. (Wochentag 6 ohne Vorschlag): Es folgt der Mittwoch
    exact = next_trip(profile, local(monday, "07:15"))  # echt danach
    assert exact is not None
    assert exact.date == date(2026, 12, 9)
    after = next_trip(profile, local(monday, "07:30"))
    assert after is not None
    assert after.date == date(2026, 12, 9)


def test_next_trip_of_a_saturday_only_car_after_the_departure_is_next_saturday() -> None:
    profile = profile_of(saturdays([480, 480, 480]))
    trip = next_trip(profile, local(date(2026, 12, 5), "10:00"))  # Abfahrt 08:00 ist vorbei
    assert trip is not None
    assert trip.date == date(2026, 12, 12)
    assert trip.departure == "08:00"
    same_day = next_trip(profile, local(date(2026, 12, 5), "07:59"))
    assert same_day is not None
    assert same_day.date == date(2026, 12, 5)
    sunday = next_trip(profile, local(date(2026, 12, 6), "10:00"))  # Samstag in sechs Tagen
    assert sunday is not None
    assert sunday.date == date(2026, 12, 12)


def test_next_trip_without_any_suggestion() -> None:
    assert next_trip(profile_of([]), local(date(2026, 12, 5), "10:00")) is None


def test_next_trip_on_a_holiday_uses_weekday_six() -> None:
    sundays = [rec(day, departure=600.0) for day in days_of(6, 3)]
    profile = profile_of(sundays)
    trip = next_trip(profile, local(date(2026, 12, 7), "18:00"))  # Montag; Dienstag ist Feiertag
    assert trip is not None
    assert trip.date == date(2026, 12, 8)
    assert trip.departure == "10:00"


def test_next_trip_across_the_spring_change() -> None:
    # Sonntag 28.03.2027: Abfahrt 02:30 gibt es nicht und liegt am Übergang um 03:00
    records = [rec(day, departure=150.0) for day in days_of(6, 3, before=date(2027, 3, 21))]
    profile = profile_of(records)
    now = datetime(2027, 3, 28, 0, 59, tzinfo=UTC)
    trip = next_trip(profile, now)
    assert trip is not None
    assert trip.date == date(2027, 3, 28)
    assert trip.departure == "02:30"
    # 03:00 ist der Übergang selbst; um 01:00 UTC ist die Abfahrt erreicht, es folgt der Ostermontag
    later = next_trip(profile, datetime(2027, 3, 28, 1, 0, tzinfo=UTC))
    assert later is not None
    assert later.date == date(2027, 3, 29)


def test_profile_bounds_and_counts() -> None:
    records = [*saturdays([480, 480, 480]), rec(date(2026, 11, 3), departure=None, away=True)]
    profile = profile_of(sorted(records, key=lambda r: r.day))
    assert profile.days == 4
    assert profile.data_from == local(date(2026, 11, 3), "00:00")
    assert profile.data_to == local(TODAY, "00:00")
    assert profile.key == "tesla"
    assert profile.source == "ha"
    empty = profile_of([])
    assert empty.days == 0
    assert empty.data_from == empty.data_to
    assert [w.weekday for w in empty.weekdays] == list(range(7))
    assert all(w.departure_min is None and w.target_soc_pct is None for w in empty.weekdays)
    assert all(w.days == 0 and w.trip_share == 0.0 for w in empty.weekdays)


def test_profile_round_trips_through_json() -> None:
    records = saturdays([440, 450, 460, 470, 480, 490], [12.0, 18.0, 22.0, 30.0, 8.0, 15.0])
    records += workweek_records()
    sessions = [(local(days_of(0, 2)[0], "18:00"), None)]
    profile = profile_of(records, sessions)
    loaded = VehicleProfile.model_validate_json(profile.model_dump_json())
    assert loaded == profile
    assert loaded.weekdays[5] == profile.weekdays[5]
    assert isinstance(loaded.weekdays[5], WeekdayProfile)
    assert VehicleProfile.model_validate(profile.model_dump(mode="json")) == profile


def test_profile_rejects_a_wrong_shape() -> None:
    profile = profile_of([])
    data = profile.model_dump()
    with pytest.raises(ValidationError):
        VehicleProfile.model_validate({**data, "presence": data["presence"][:6]})
    with pytest.raises(ValidationError):
        VehicleProfile.model_validate({**data, "weekdays": data["weekdays"][:6]})
    short = [row[:95] for row in data["presence"]]
    with pytest.raises(ValidationError):
        VehicleProfile.model_validate({**data, "presence": short})
