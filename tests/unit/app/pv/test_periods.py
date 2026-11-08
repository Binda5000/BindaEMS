from datetime import date

from bindaems.app.pv.periods import PvBucket, buckets, period_energy, unassigned

TODAY = date(2026, 11, 8)


def test_buckets() -> None:
    week = buckets("week", date(2026, 11, 2), date(2026, 11, 8))
    assert [b.start.day for b in week] == [2, 3, 4, 5, 6, 7, 8] and all(
        b.start == b.end for b in week
    )
    assert len(buckets("month", date(2027, 2, 1), date(2027, 2, 28))) == 28
    year = buckets("year", date(2026, 1, 1), date(2026, 12, 31))
    assert len(year) == 12
    assert year[2] == PvBucket(date(2026, 3, 1), date(2026, 3, 31))
    assert buckets("day", TODAY, TODAY) == [PvBucket(TODAY, TODAY)]


def test_month_today_from_cache_past_from_table() -> None:
    month = buckets("month", date(2026, 11, 1), date(2026, 11, 30))
    daily = {1: {date(2026, 11, 7): (12.3, None)}}
    result = period_energy(daily, {1: 4.5}, month, TODAY)[1]
    assert result.kwh[6:] == [12.3, 4.5] + [None] * 22
    assert result.kwh[:6] == [None] * 6
    assert result.sum_kwh == 16.8
    assert result.first_known == date(2026, 11, 7)


def test_future_days_do_not_count() -> None:
    month = buckets("month", date(2026, 11, 1), date(2026, 11, 30))
    daily = {1: {date(2026, 11, 9): (99.0, None)}}
    result = period_energy(daily, {1: None}, month, TODAY)[1]
    assert result.sum_kwh is None and result.first_known is None


def test_year_before_first_data_is_none() -> None:
    year = buckets("year", date(2026, 1, 1), date(2026, 12, 31))
    daily = {
        1: {date(2026, 10, 18): (5.0, None), date(2026, 10, 19): (6.0, None)},
        2: {date(2026, 10, 3): (None, "kein Verlauf seit Mitternacht")},
    }
    result = period_energy(daily, {1: 1.0, 2: None}, year, TODAY)
    one = result[1]
    assert one.kwh[:9] == [None] * 9
    assert one.kwh[9] == 11.0 and one.kwh[10] == 1.0 and one.kwh[11] is None
    assert one.first_known == date(2026, 10, 18) and one.sum_kwh == 12.0
    assert result[2].sum_kwh is None and result[2].note == "kein Verlauf seit Mitternacht"
    assert all(v is None for v in result[2].kwh)


def test_zero_is_a_value() -> None:
    week = buckets("week", date(2026, 11, 2), date(2026, 11, 8))
    result = period_energy({1: {date(2026, 11, 2): (0.0, None)}}, {1: None}, week, TODAY)[1]
    assert result.kwh[0] == 0.0 and result.sum_kwh == 0.0


def test_unassigned_from_total() -> None:
    week = buckets("week", date(2026, 11, 2), date(2026, 11, 8))
    daily = {
        0: {date(2026, 11, 2): (30.0, None), date(2026, 11, 3): (30.0, None)},
        1: {date(2026, 11, 2): (27.5, None)},
    }
    result = period_energy(daily, {0: None, 1: None}, week, TODAY)
    kwh, total = unassigned(result[0], [result[1]])
    assert kwh[0] == 2.5 and kwh[1] is None and total == 2.5
