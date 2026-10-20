from datetime import UTC, date, datetime, time, timedelta

from bindaems.app.learning.driving_days import DayRecord
from bindaems.app.learning.driving_profile import Confidence, VehicleProfile, WeekdayProfile
from bindaems.app.learning.driving_quality import HitRates, hit_rates
from bindaems.shared.timeutil import LOCAL_TZ

TODAY = date(2026, 12, 1)  # ein Dienstag
DAY = date(2026, 11, 24)  # ein Dienstag davor
RESERVE = 20.0


def local(day: date, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def weekday_profile(
    weekday: int,
    *,
    departure_min: int | None = 450,
    departure_confidence: Confidence | None = "high",
    target: int | None = 60,
    target_confidence: Confidence | None = "high",
) -> WeekdayProfile:
    return WeekdayProfile(
        weekday=weekday,
        basis="weekday",
        days=8,
        trips=8,
        trip_share=1.0,
        departure_min=departure_min,
        departure_p50_min=departure_min,
        departure_p80_min=departure_min,
        departure_confidence=departure_confidence,
        arrival_p50_min=None,
        arrival_soc_p50=None,
        consumption_p50_pp=None,
        consumption_p80_pp=None,
        target_soc_pct=target,
        target_clamped=False,
        target_confidence=target_confidence,
    )


def profile(**kwargs) -> VehicleProfile:
    """Alle sieben Wochentage mit denselben Vorschlägen."""
    return VehicleProfile(
        key="tesla",
        source="tessie",
        data_from=local(TODAY - timedelta(days=60), "00:00"),
        data_to=local(TODAY - timedelta(days=1), "00:00"),
        days=50,
        weekdays=[weekday_profile(w, **kwargs) for w in range(7)],
        presence=[[0.0] * 96 for _ in range(7)],
    )


def record(
    day: date = DAY,
    *,
    departure: str | None = "07:30",
    consumption: float | None = 30.0,
    away: bool = False,
    weekday: int | None = None,
) -> DayRecord:
    return DayRecord(
        day=day,
        weekday=day.weekday() if weekday is None else weekday,
        away=away,
        departure=None if departure is None else local(day, departure),
        departure_soc=None,
        arrival=None,
        arrival_soc=None,
        return_at=None,
        return_soc=None,
        consumption_pp=consumption,
    )


def evening_before(day: date = DAY, clock: str = "03:40") -> datetime:
    return local(day - timedelta(days=1), clock)


def rates(records, snapshots) -> HitRates:
    return hit_rates(records, snapshots, RESERVE, today=TODAY)


def test_departure_hit_when_not_earlier() -> None:
    snapshot = [(evening_before(), profile(departure_min=450))]  # Vorschlag 07:30
    on_time = rates([record(departure="07:30")], snapshot)
    later = rates([record(departure="08:10")], snapshot)
    earlier = rates([record(departure="07:29")], snapshot)
    assert (on_time.departure_n, on_time.departure_hits) == (1, 1)
    assert (later.departure_n, later.departure_hits) == (1, 1)
    assert (earlier.departure_n, earlier.departure_hits) == (1, 0)


def test_suggestion_of_the_evening_before_counts() -> None:
    old = profile(departure_min=420)  # 07:00 – hätte die Fahrt um 07:10 getroffen
    mid = profile(departure_min=480)  # 08:00 – der Stand vom Vorabend: Fahrt um 07:10 zu früh
    new = profile(departure_min=360)
    snapshots = [
        (local(DAY - timedelta(days=5), "03:40"), old),
        (local(DAY - timedelta(days=1), "23:59"), mid),
        (local(DAY, "00:00"), new),  # genau Mitternacht gilt nicht: strikt davor
        (local(DAY, "03:40"), new),  # am Tag selbst berechnet
    ]
    result = rates([record(departure="07:10")], snapshots)
    assert (result.departure_n, result.departure_hits) == (1, 0)


def test_snapshot_order_does_not_matter() -> None:
    snapshots = [
        (local(DAY - timedelta(days=1), "03:40"), profile(departure_min=480)),
        (local(DAY - timedelta(days=9), "03:40"), profile(departure_min=420)),
    ]
    forward = rates([record(departure="07:10")], snapshots)
    backward = rates([record(departure="07:10")], snapshots[::-1])
    assert forward == backward
    assert (forward.departure_n, forward.departure_hits) == (1, 0)


def test_day_without_earlier_snapshot_is_not_rated() -> None:
    snapshots = [(local(DAY, "03:40"), profile())]
    assert rates([record()], snapshots) == HitRates(0, 0, 0, 0)
    assert rates([record()], []) == HitRates(0, 0, 0, 0)


def test_low_confidence_days_are_not_rated() -> None:
    low = profile(departure_confidence="low", target_confidence="low")
    none = profile(departure_confidence=None, target_confidence=None)
    for snapshot in (low, none):
        assert rates([record()], [(evening_before(), snapshot)]) == HitRates(0, 0, 0, 0)
    medium = profile(departure_confidence="medium", target_confidence="medium")
    assert rates([record()], [(evening_before(), medium)]).departure_n == 1
    assert rates([record()], [(evening_before(), medium)]).target_n == 1


def test_no_suggestion_is_not_rated() -> None:
    empty = profile(departure_min=None, target=None)
    assert rates([record()], [(evening_before(), empty)]) == HitRates(0, 0, 0, 0)


def test_target_hit_needs_reserve_left() -> None:
    snapshot = [(evening_before(), profile(target=60))]
    exact = rates([record(consumption=40.0)], snapshot)  # 60 - 40 = 20 = Puffer
    short = rates([record(consumption=40.5)], snapshot)
    generous = rates([record(consumption=10.0)], snapshot)
    negative = rates([record(consumption=-5.0)], snapshot)  # unterwegs geladen
    assert (exact.target_n, exact.target_hits) == (1, 1)
    assert (short.target_n, short.target_hits) == (1, 0)
    assert (generous.target_n, generous.target_hits) == (1, 1)
    assert (negative.target_n, negative.target_hits) == (1, 1)


def test_unknown_consumption_rates_the_departure_only() -> None:
    result = rates([record(consumption=None)], [(evening_before(), profile())])
    assert result == HitRates(1, 1, 0, 0)


def test_away_and_tripless_days_are_not_trip_days() -> None:
    snapshot = [(evening_before(), profile())]
    records = [record(away=True, departure=None), record(departure=None)]
    assert rates(records, snapshot) == HitRates(0, 0, 0, 0)


def test_only_the_last_28_days_count() -> None:
    first = TODAY - timedelta(days=28)
    snapshot = [(local(first - timedelta(days=3), "03:40"), profile())]
    inside = [record(first), record(TODAY - timedelta(days=1))]
    outside = [record(first - timedelta(days=1)), record(TODAY)]
    assert rates(inside + outside, snapshot).departure_n == 2
    assert hit_rates(inside + outside, snapshot, RESERVE, today=TODAY, days=3).departure_n == 1


def test_weekday_profile_of_the_record_is_rated() -> None:
    # ein Feiertag zählt als Wochentag 6: dessen Vorschlag gilt, nicht der des Kalenderwochentags
    weekdays = [weekday_profile(w, departure_min=450) for w in range(7)]
    weekdays[6] = weekday_profile(6, departure_min=600)
    snap = profile().model_copy(update={"weekdays": weekdays})
    holiday = record(DAY, departure="09:00", weekday=6)
    assert rates([holiday], [(evening_before(), snap)]).departure_hits == 0
    assert rates([record(DAY, departure="09:00")], [(evening_before(), snap)]).departure_hits == 1


def test_departure_is_compared_in_local_time() -> None:
    day = date(2026, 11, 3)  # Winterzeit: 07:00 lokal = 06:00 UTC
    snapshot = [(local(day - timedelta(days=1), "03:40"), profile(departure_min=420))]
    assert rates([record(day, departure="07:00")], snapshot).departure_hits == 1


def test_percent_rounds_and_is_none_without_days() -> None:
    assert HitRates(0, 0, 0, 0).departure_pct is None
    assert HitRates(0, 0, 0, 0).target_pct is None
    assert HitRates(19, 17, 17, 16).departure_pct == 89
    assert HitRates(19, 17, 17, 16).target_pct == 94
