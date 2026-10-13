from datetime import UTC, datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.control.tesla_policy import MovingAverage, WakeQuota

NAN, INF = float("nan"), float("inf")


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- Gleitendes Mittel (Design 6.5, 7.2) ---


def test_average_over_the_window() -> None:
    average = MovingAverage(90.0)
    for s, value in ((0, 10.0), (30, 16.0), (60, 7.0)):
        average.add(T0 + timedelta(seconds=s), value)
    assert average.value(T0 + timedelta(seconds=60)) == 11.0
    assert average.value(T0 + timedelta(seconds=91)) == 11.5


def test_nothing_is_known_without_values() -> None:
    average = MovingAverage(90.0)
    assert average.value(T0) is None


def test_a_value_counts_up_to_and_including_the_window() -> None:
    average = MovingAverage(90.0)
    average.add(at(0), 10.0)
    average.add(at(90), 20.0)
    assert average.value(at(90)) == 15.0  # now − t = 90 s: gerade noch im Fenster
    assert average.value(at(90.001)) == 20.0  # der erste ist 90,001 s alt
    assert average.value(at(180)) == 20.0  # der zweite ist genau 90 s alt
    assert average.value(at(180.001)) is None


def test_the_average_is_not_weighted_by_time() -> None:
    average = MovingAverage(90.0)
    for s, value in ((0, 8.0), (1, 8.0), (2, 8.0), (80, 16.0)):
        average.add(at(s), value)
    assert average.value(at(80)) == 10.0  # (8 + 8 + 8 + 16) / 4


def test_reading_changes_nothing() -> None:
    average = MovingAverage(90.0)
    average.add(at(0), 10.0)
    average.add(at(60), 20.0)
    assert average.value(at(100)) == 20.0  # der erste ist verfallen
    assert average.value(at(60)) == 15.0  # eine frühere Frage sieht ihn wieder
    assert average.value(at(100)) == 20.0


def test_adding_forgets_what_is_too_old() -> None:
    average = MovingAverage(90.0)
    for s in range(0, 300, 10):
        average.add(at(s), float(s))
    # Im Fenster liegen die Werte 200 bis 290 (Mittel 245); die älteren sind verworfen. Das zeigt
    # eine frühere Frage: Zu 150 s lägen auch die Werte 60 bis 190 im Fenster, wären sie noch da.
    assert average.value(at(290)) == 245.0
    assert average.value(at(150)) == 245.0


def test_clear_starts_a_new_window() -> None:
    average = MovingAverage(90.0)
    average.add(at(0), 16.0)
    average.clear()
    assert average.value(at(0)) is None
    average.add(at(1), 6.0)
    assert average.value(at(1)) == 6.0  # vom Wert vor dem Leeren bleibt nichts
    average.clear()
    average.clear()  # ohne Werte schadet es nicht
    assert average.value(at(2)) is None


def test_the_average_does_not_drift_over_a_long_run() -> None:
    # Ein mitgeführter Summenwert würde bei jedem Verfall Rundungsfehler sammeln; das Mittel
    # hängt nur von den Werten im Fenster ab.
    average = MovingAverage(90.0)
    for s in range(20_000):
        average.add(at(s), 0.1 * (s % 7))
    window = [0.1 * (s % 7) for s in range(19_999 - 90, 20_000)]
    assert average.value(at(19_999)) == pytest.approx(sum(window) / len(window), abs=1e-12)


@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_a_value_that_is_no_finite_number_is_not_kept(bad: float) -> None:
    # Ein einziges NaN würde sonst das Mittel des ganzen Fensters unbrauchbar machen.
    average = MovingAverage(90.0)
    average.add(at(0), 10.0)
    average.add(at(1), bad)
    average.add(at(2), 20.0)
    assert average.value(at(2)) == 15.0
    lonely = MovingAverage(90.0)
    lonely.add(at(0), bad)
    assert lonely.value(at(0)) is None


def test_a_window_of_zero_keeps_only_the_present() -> None:
    average = MovingAverage(0.0)
    average.add(at(0), 10.0)
    assert average.value(at(0)) == 10.0
    assert average.value(at(1)) is None


def test_values_at_the_same_moment_all_count() -> None:
    average = MovingAverage(90.0)
    average.add(at(0), 4.0)
    average.add(at(0), 8.0)
    assert average.value(at(0)) == 6.0


# --- Weckkontingent (Design 7.1; Review Focus 5) ---


def test_quota_needs_the_interval_and_counts_per_day() -> None:
    quota = WakeQuota(1800.0, 2)
    quota.note(T0, T0)
    assert not quota.allowed(T0 + timedelta(seconds=1799), T0 + timedelta(seconds=1799))
    later = T0 + timedelta(seconds=1800)
    assert quota.allowed(later, later)
    quota.note(later, later)
    end = T0 + timedelta(hours=2)
    assert not quota.allowed(end, end) and quota.today(end) == 2


def test_quota_resets_at_vienna_midnight() -> None:  # Review Focus 5
    quota = WakeQuota(0.0, 1)
    evening = datetime(2026, 10, 12, 21, 59, tzinfo=UTC)  # 23:59 Wiener Sommerzeit
    quota.note(evening, evening)
    assert not quota.allowed(evening + timedelta(seconds=30), evening + timedelta(seconds=30))
    midnight = evening + timedelta(seconds=60)
    assert quota.allowed(midnight, midnight)


def test_quota_day_survives_the_dst_change() -> None:  # Review Focus 5
    quota = WakeQuota(0.0, 1)
    early = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)  # 00:30 MESZ am 25.10. (25-Stunden-Tag)
    quota.note(early, early)
    late = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)  # 23:30 MEZ am 25.10.
    assert not quota.allowed(late, late)
    next_day = late + timedelta(minutes=30)  # 00:00 MEZ am 26.10.
    assert quota.allowed(next_day, next_day)


def test_quota_day_is_the_vienna_day_in_summer_and_winter() -> None:
    # Um 22:00 UTC beginnt in Wien der nächste Tag, im Sommer (UTC+2), um 23:00 UTC im Winter.
    summer, winter = WakeQuota(0.0, 1), WakeQuota(0.0, 1)
    summer_noon = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    winter_noon = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    summer.note(summer_noon, summer_noon)
    winter.note(winter_noon, winter_noon)
    for quota, noon, until_midnight in (
        (summer, summer_noon, timedelta(hours=10)),
        (winter, winter_noon, timedelta(hours=11)),
    ):
        before, after = noon + until_midnight - timedelta(seconds=1), noon + until_midnight
        assert not quota.allowed(before, before)
        assert quota.allowed(after, after)


def test_quota_day_follows_the_wall_clock_and_the_interval_the_safety_clock() -> None:
    quota = WakeQuota(1800.0, 6)
    quota.note(at(0), T0)  # Safety-Uhr bei 0 s, Wanduhr 12:00 Wiener Zeit
    # Die Wanduhr springt um Tage, die Safety-Uhr nicht: Der Abstand zählt allein auf ``now``.
    assert not quota.allowed(at(1799), T0 + timedelta(days=30))
    assert quota.allowed(at(1800), T0 + timedelta(days=30))
    # Der Tag allein entscheidet über die Zahl: ein neuer Tag auf der Wanduhr, ein neuer Zähler.
    assert quota.today(T0) == 1 and quota.today(T0 + timedelta(days=30)) == 0


def test_quota_counts_each_note() -> None:
    quota = WakeQuota(0.0, 6)
    assert quota.today(T0) == 0
    for count in range(1, 7):
        assert quota.allowed(at(count), at(count))
        quota.note(at(count), at(count))
        assert quota.today(at(count)) == count
    assert not quota.allowed(at(7), at(7))
    assert quota.today(at(7)) == 6


def test_quota_today_is_zero_on_another_day() -> None:
    quota = WakeQuota(0.0, 2)
    quota.note(T0, T0)
    assert quota.today(T0) == 1
    assert quota.today(T0 + timedelta(days=1)) == 0  # morgen: noch nichts
    assert quota.today(T0 - timedelta(days=1)) == 0  # und gestern gilt der Zähler nicht


def test_a_new_day_starts_the_count_afresh() -> None:
    quota = WakeQuota(0.0, 2)
    quota.note(T0, T0)
    quota.note(at(1), at(1))
    tomorrow = T0 + timedelta(days=1)
    assert not quota.allowed(at(2), at(2)) and quota.allowed(tomorrow, tomorrow)
    quota.note(tomorrow, tomorrow)
    assert quota.today(tomorrow) == 1  # nicht 3
    assert quota.allowed(tomorrow, tomorrow)


def test_first_wake_needs_no_interval() -> None:
    quota = WakeQuota(1800.0, 6)
    assert quota.allowed(T0, T0)  # noch nie geweckt: auch zur Zeit null der Uhr
    assert quota.allowed(datetime(1970, 1, 1, tzinfo=UTC), datetime(1970, 1, 1, tzinfo=UTC))


def test_allowed_changes_nothing() -> None:
    quota = WakeQuota(1800.0, 1)
    for _ in range(5):
        assert quota.allowed(T0, T0)
    assert quota.today(T0) == 0
    quota.note(T0, T0)
    for _ in range(5):
        assert not quota.allowed(at(1), at(1))
    assert quota.today(T0) == 1


def test_no_wake_at_all_with_a_quota_of_zero() -> None:
    # ``wake: {interval_s: 0, per_day: 0}`` heißt: nie wecken
    quota = WakeQuota(0.0, 0)
    assert not quota.allowed(T0, T0)
    assert quota.today(T0) == 0


def test_the_interval_runs_across_midnight() -> None:
    quota = WakeQuota(1800.0, 6)
    evening = datetime(2026, 10, 12, 21, 50, tzinfo=UTC)  # 23:50 Wiener Zeit
    quota.note(evening, evening)
    after = evening + timedelta(minutes=20)  # 00:10 am nächsten Tag
    assert quota.today(after) == 0
    assert not quota.allowed(after, after)  # der Abstand von 30 min gilt auch über Mitternacht
    later = evening + timedelta(minutes=30)
    assert quota.allowed(later, later)


def test_the_interval_is_measured_from_the_last_note() -> None:
    quota = WakeQuota(1800.0, 6)
    quota.note(at(0), at(0))
    quota.note(at(2000), at(2000))  # ein späterer Weckstart verschiebt den Abstand
    assert not quota.allowed(at(2000 + 1799), at(2000 + 1799))
    assert quota.allowed(at(2000 + 1800), at(2000 + 1800))


def test_a_naive_wall_clock_is_a_programming_error() -> None:
    quota = WakeQuota(0.0, 1)
    with pytest.raises(ValueError, match="naiver Zeitpunkt"):
        quota.today(datetime(2026, 10, 8, 12, 0))
