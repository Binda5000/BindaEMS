"""Schneeerkennung: reine Funktion aus Wetter, Messung und Handschalter (Entscheidungen 4, 5, 9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bindaems.app.forecast.openmeteo import SnowHour
from bindaems.app.forecast.snow import (
    NO_CUT,
    Observation,
    Override,
    SnowCut,
    measure_min_w,
    snow_cut,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT

KWP = 9.8
BIG = 1000.0  # ungekürzte P50, über MEASURE_MIN_W = 490


def local(day: int, hh: int, mm: int = 0) -> datetime:
    """Dezember 2026, Ortszeit Wien (UTC+1), als UTC."""
    return datetime(2026, 12, day, hh, mm, tzinfo=LOCAL_TZ).astimezone(UTC)


def hours(snow: dict[tuple[int, int], float], depth: float | None = 0.0) -> list[SnowHour]:
    """Stunden vom 1.12. 00:00 bis 6.12. 00:00 Ortszeit; ``snow`` nennt (Tag, Stunde) -> cm."""
    out = []
    t = local(1, 0)
    while t < local(6, 0):
        lt = t.astimezone(LOCAL_TZ)
        out.append(SnowHour(t, snow.get((lt.day, lt.hour), 0.0), depth))
        t += timedelta(hours=1)
    return out


def obs(
    start: datetime,
    ratios: list[float],
    *,
    expected: float = BIG,
    temp: float | None = -2.0,
) -> list[Observation]:
    return [
        Observation(start + i * SLOT, expected, expected * r, temp) for i, r in enumerate(ratios)
    ]


def day_slots(first: datetime, last: datetime) -> list[datetime]:
    out = []
    t = first
    while t <= last:
        out.append(t)
        t += SLOT
    return out


ALL_SLOTS = day_slots(local(3, 0), local(6, 0))
NIGHT_SNOW = {(3, 1): 1.0, (3, 2): 0.5, (3, 3): 0.5}  # 2 cm, erste Ereignisstunde 01:00


def cut(now, weather, observations=(), override=None, slots=ALL_SLOTS):
    return snow_cut(now, slots, weather, list(observations), total_kwp=KWP, override=override)


def test_measure_min_w() -> None:
    assert measure_min_w(9.8) == 490.0
    assert measure_min_w(2.0) == 300.0


def test_night_snowfall_cuts_today_and_tomorrow() -> None:
    res = cut(local(3, 5), hours(NIGHT_SNOW))
    assert res.state == "vermutet"
    assert res.factor == 0.1
    assert res.since == local(3, 1)
    expected = day_slots(local(3, 5), local(4, 23, 45))
    assert sorted(res.factors) == expected
    assert set(res.factors.values()) == {0.1}
    assert local(3, 4, 45) not in res.factors  # vergangen
    assert local(5, 0) not in res.factors


def covered_obs() -> list[Observation]:
    return obs(local(3, 8, 30), [0.05, 0.3, 0.35, 0.4, 0.3])


def test_measured_ratio_sets_factor() -> None:
    res = cut(local(3, 10), hours(NIGHT_SNOW), covered_obs())
    assert res.state == "gemessen"
    assert res.factor == 0.35
    assert res.factors[local(3, 10)] == 0.35
    assert res.factors[local(4, 23, 45)] == 0.35


def test_two_free_slots_lift_the_cut() -> None:
    weather = hours(NIGHT_SNOW)
    free = covered_obs() + obs(local(3, 12), [0.7, 0.8])
    assert cut(local(3, 12, 45), weather, free) == NO_CUT
    # ein einzelner freier Slot hebt nicht auf
    single = covered_obs() + obs(local(3, 12), [0.9, 0.4])
    res = cut(local(3, 12, 45), weather, single)
    assert res.state == "gemessen"


def test_future_snowfall_cuts_from_its_start() -> None:
    res = cut(local(3, 10), hours({(4, 14): 1.0, (4, 15): 0.25, (4, 16): 0.25}))
    assert res.state == "frei"
    assert res.factor == 1.0
    assert res.since is None
    assert sorted(res.factors) == day_slots(local(4, 14), local(5, 23, 45))
    assert set(res.factors.values()) == {0.1}


def test_light_snow_is_ignored() -> None:
    weather = hours({(3, 1): 0.3, (3, 4): 0.3, (3, 8): 0.2})
    assert cut(local(3, 10), weather) == NO_CUT


def test_zero_pv_without_snow_is_not_snow() -> None:
    res = cut(local(3, 12), hours({}, depth=0.0), obs(local(3, 10, 30), [0.0] * 5, temp=-2.0))
    assert res == NO_CUT


def test_measured_snow_without_forecast_snowfall() -> None:
    weather = hours({}, depth=0.05)
    res = cut(local(3, 12), weather, obs(local(3, 11), [0.05] * 3, temp=-1.0))
    assert res.state == "gemessen"
    assert res.since == local(3, 11)
    assert cut(local(3, 12), weather, obs(local(3, 11), [0.05] * 3, temp=6.0)) == NO_CUT
    assert cut(local(3, 12), weather, obs(local(3, 11), [0.05] * 3, temp=None)) == NO_CUT


def test_gap_breaks_consecutive_slots() -> None:
    weather = hours(NIGHT_SNOW)
    gap = obs(local(3, 12), [0.8]) + obs(local(3, 12, 30), [0.8])
    res = cut(local(3, 13), weather, gap)
    assert res.state != "frei"
    assert res.factor == 0.8


def test_slots_below_measure_min_do_not_count() -> None:
    weather = hours(NIGHT_SNOW)
    low = obs(local(3, 9), [1.0, 1.0, 1.0], expected=400.0)
    res = cut(local(3, 10), weather, low)
    assert res.state == "vermutet"
    assert res.factor == 0.1


def test_override_covered_and_free() -> None:
    clear = hours({})
    res = cut(local(3, 9), clear, override=Override("covered", local(3, 8)))
    assert res.state == "vermutet"
    assert res.since == local(3, 8)
    freed = obs(local(3, 9), [0.7, 0.8])
    assert cut(local(3, 9, 45), clear, freed, Override("covered", local(3, 8))) == NO_CUT

    snow = hours({(3, 1): 1.0, (3, 2): 0.5})
    assert cut(local(3, 8), snow, override=Override("free", local(3, 6))) == NO_CUT
    again = hours({(3, 1): 1.0, (3, 2): 0.5, (3, 7): 1.0})
    res = cut(local(3, 8), again, override=Override("free", local(3, 6)))
    assert res.state == "vermutet"
    assert res.since == local(3, 7)

    old = Override("covered", local(3, 8) - timedelta(hours=49))
    assert cut(local(3, 8), clear, override=old) == NO_CUT
    assert (
        cut(local(3, 8), snow, override=Override("free", local(3, 8) - timedelta(hours=49))).state
        == "vermutet"
    )


def test_overlapping_windows_use_the_smaller_factor() -> None:
    weather = hours({**NIGHT_SNOW, (4, 14): 1.0})
    res = cut(local(3, 10), weather, covered_obs())
    assert res.factors[local(3, 13)] == 0.35
    assert res.factors[local(4, 14)] == 0.1
    assert res.factors[local(5, 23, 45)] == 0.1
    assert local(6, 0) not in res.factors


def test_slot_outside_window_and_past_get_no_factor() -> None:
    res = cut(local(3, 5), hours(NIGHT_SNOW), slots=[local(3, 4), local(3, 5), local(5, 0)])
    assert res.factors == {local(3, 5): 0.1}


def test_snow_cut_is_frozen_value() -> None:
    assert NO_CUT == SnowCut("frei", 1.0, None, {})


def test_low_slots_before_free_since_do_not_cover() -> None:
    weather = hours({}, depth=0.05)
    seen = obs(local(3, 8), [0.05] * 3) + obs(local(3, 9), [0.8, 0.8])
    assert cut(local(3, 10), weather, seen) == NO_CUT


LONG_SLOTS = day_slots(local(3, 0), local(7, 0))


def test_every_future_snowfall_gets_its_own_window() -> None:
    weather = hours({**NIGHT_SNOW, (4, 6): 1.0, (5, 14): 1.0})
    res = cut(local(3, 3, 30), weather, slots=LONG_SLOTS)
    assert res.factors[local(5, 14)] == 0.1
    assert res.factors[local(6, 23, 45)] == 0.1
    assert local(7, 0) not in res.factors


def test_second_snowfall_during_ongoing_snowfall_is_cut() -> None:
    weather = hours({**NIGHT_SNOW, (3, 4): 0.5, (5, 14): 1.0})
    res = cut(local(3, 3, 30), weather, slots=LONG_SLOTS)
    assert res.state == "vermutet"
    assert res.factors[local(5, 14)] == 0.1
    assert local(5, 13, 45) not in res.factors
    assert res.factors[local(6, 23, 45)] == 0.1


def test_single_measurable_slot_is_still_assumed() -> None:
    res = cut(local(3, 10), hours(NIGHT_SNOW), obs(local(3, 9, 30), [0.5]))
    assert res.state == "vermutet"
    assert res.factor == 0.1


def test_factor_is_capped_at_one() -> None:
    seen = []
    for start, r in (
        (local(3, 9), 1.3),
        (local(3, 9, 30), 1.4),
        (local(3, 10), 1.5),
        (local(3, 10, 30), 1.6),
    ):
        seen += obs(start, [r])
    res = cut(local(3, 11), hours(NIGHT_SNOW), seen)
    assert res.state == "gemessen"
    assert res.factor == 1.0
    assert res.factors == {}
