import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta

import pytest

from bindaems.app.forecast.load import LoadProfile, LoadSample
from bindaems.app.learning.features import (
    BASE_FEATURES,
    ISSUE_HOURS,
    MAX_LEAD,
    FeatureInputs,
    feature_names,
    feature_rows,
    issuances,
    profile_cache,
    training_set,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots, slot_start, slots_between

NAN = float("nan")


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def column(rows: Sequence[Sequence[float]], names: Sequence[str], name: str) -> list[float]:
    index = names.index(name)
    return [row[index] for row in rows]


def same(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> bool:
    """Gleiche Zeilen, wobei leere Werte (``NaN``) einander entsprechen."""
    return len(a) == len(b) and all(
        len(x) == len(y)
        and all(u == v or (math.isnan(u) and math.isnan(v)) for u, v in zip(x, y, strict=True))
        for x, y in zip(a, b, strict=True)
    )


def filled(start: datetime, end: datetime, value: float = 100.0) -> dict[datetime, float]:
    """Wert je Slot; er wächst mit der Zeit, damit jeder Slot einen eigenen Wert hat."""
    return {slot: value + (slot - start) / SLOT for slot in slots_between(start, end)}


def make_inputs(
    *,
    load_w: Mapping[datetime, float] | None = None,
    temp_c: Mapping[datetime, float] | None = None,
    sensors: Mapping[str, Mapping[datetime, float]] | None = None,
    profile: LoadProfile | None = None,
) -> FeatureInputs:
    return FeatureInputs(
        load_w=load_w or {},
        temp_c=temp_c or {},
        sensors=sensors or {},
        profile_for=lambda _day: profile,
    )


def test_feature_names_append_sensors_in_order() -> None:
    assert feature_names(["sensor.a", "person.b"])[-2:] == ("ha:sensor.a", "ha:person.b")
    assert feature_names(["sensor.a", "person.b"])[:-2] == BASE_FEATURES
    assert feature_names([]) == BASE_FEATURES


def test_base_features_are_fixed() -> None:
    assert BASE_FEATURES == (
        "lead_h",
        "minute_of_day",
        "weekday",
        "day_type",
        "holiday",
        "month",
        "temp_c",
        "temp_day_c",
        "lag_1d_w",
        "lag_7d_w",
        "recent_w",
        "profile_w",
    )
    assert ISSUE_HOURS == (0, 6, 12, 18)
    assert MAX_LEAD == timedelta(hours=72)


@pytest.mark.parametrize("day", [date(2026, 10, 25), date(2026, 3, 29), date(2026, 7, 1)])
def test_issuances_on_dst_days(day: date) -> None:
    start = local(day, 0)
    found = issuances(start, local(day + timedelta(days=1), 0))
    assert len(found) == 4
    assert [t.astimezone(LOCAL_TZ).hour for t in found] == [0, 6, 12, 18]
    assert all(t.tzinfo is UTC and t.astimezone(LOCAL_TZ).minute == 0 for t in found)
    assert found == sorted(found)


def test_issuances_are_half_open_and_cross_days() -> None:
    start = local(date(2026, 10, 24), 6)
    end = local(date(2026, 10, 26), 6)
    found = issuances(start, end)
    assert found[0] == start
    assert end not in found
    assert [t.astimezone(LOCAL_TZ).hour for t in found] == [6, 12, 18, 0, 6, 12, 18, 0]
    assert issuances(start, start) == []
    assert issuances(local(date(2026, 10, 24), 7), local(date(2026, 10, 24), 11)) == []


def test_calendar_features_use_local_time() -> None:
    names = feature_names([])
    # 25.10. 23:30 UTC ist der 26.10. 00:30 Uhr lokal: Feiertag (Montag), obwohl UTC Sonntag ist
    slot = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    rows = feature_rows(slot - timedelta(hours=2), [slot], make_inputs(), names)
    (row,) = rows
    value = dict(zip(names, row, strict=True))
    assert value["minute_of_day"] == 30
    assert value["weekday"] == 0
    assert value["day_type"] == 2
    assert value["holiday"] == 1
    assert value["month"] == 10
    assert value["lead_h"] == pytest.approx(2.0)
    # Werktag, Samstag, Sonntag ohne Feiertag
    for day, expected_type, holiday in [
        (date(2026, 10, 27), 0, 0),
        (date(2026, 10, 31), 1, 0),
        (date(2026, 11, 8), 2, 0),
    ]:
        (row,) = feature_rows(local(day, 6), [local(day, 12)], make_inputs(), names)
        value = dict(zip(names, row, strict=True))
        assert (value["day_type"], value["holiday"]) == (expected_type, holiday)
        assert value["minute_of_day"] == 720
        assert value["weekday"] == day.weekday()
    # 31.10. 23:30 UTC = 01.11. 00:30 lokal: Allerheiligen, Monat 11
    (row,) = feature_rows(
        local(date(2026, 10, 31), 12),
        [datetime(2026, 10, 31, 23, 30, tzinfo=UTC)],
        make_inputs(),
        names,
    )
    value = dict(zip(names, row, strict=True))
    assert (value["month"], value["holiday"], value["day_type"]) == (11, 1, 2)


def test_lead_is_hours_from_issuance_and_may_be_slightly_negative() -> None:
    names = ["lead_h"]
    issued = datetime(2026, 10, 20, 6, 7, tzinfo=UTC)  # der Slot der Ausgabe beginnt um 06:00
    slots = [slot_start(issued), slot_start(issued) + 3 * SLOT, issued + timedelta(hours=72)]
    rows = feature_rows(issued, slots, make_inputs(), names)
    assert column(rows, names, "lead_h") == pytest.approx([-7 / 60, 0.75 - 7 / 60, 72.0 - 0.0])


def test_lag_1d_is_empty_beyond_one_day() -> None:
    names = feature_names([])
    issued = local(date(2026, 10, 20), 6)
    load = filled(issued - timedelta(days=9), issued + timedelta(days=4))
    inputs = make_inputs(load_w=load)
    near = issued + timedelta(hours=3)
    far = issued + timedelta(hours=30)
    rows = feature_rows(issued, [near, far], inputs, names)
    lag_1d = column(rows, names, "lag_1d_w")
    lag_7d = column(rows, names, "lag_7d_w")
    assert lag_1d[0] == load[near - timedelta(days=1)]
    assert math.isnan(lag_1d[1])
    # sieben Tage vorher ist auch bei 30 h Vorlauf bekannt
    assert lag_7d == [load[near - timedelta(days=7)], load[far - timedelta(days=7)]]


def test_lag_1d_needs_the_slot_to_end_before_the_issuance() -> None:
    names = ["lag_1d_w"]
    issued = local(date(2026, 10, 20), 6)
    load = filled(issued - timedelta(days=3), issued + timedelta(days=3))
    last_known = issued + timedelta(hours=24) - SLOT  # lag-Slot endet genau zur Ausgabe
    first_unknown = issued + timedelta(hours=24)  # lag-Slot beginnt zur Ausgabe
    rows = feature_rows(issued, [last_known, first_unknown], make_inputs(load_w=load), names)
    assert rows[0] == [load[last_known - timedelta(days=1)]]
    assert math.isnan(rows[1][0])


def test_lag_follows_the_local_clock_over_dst() -> None:
    names = ["lag_1d_w", "lag_7d_w"]
    # Herbstumstellung 25.10.2026: 01:00 lokal am 26.10. liegt 25 h nach 01:00 lokal am 25.10.
    issued = local(date(2026, 10, 26), 0)
    load = filled(issued - timedelta(days=10), issued + timedelta(days=2))
    inputs = make_inputs(load_w=load)
    slot = local(date(2026, 10, 26), 1)
    (row,) = feature_rows(issued, [slot], inputs, names)
    assert row == [load[local(date(2026, 10, 25), 1)], load[local(date(2026, 10, 19), 1)]]
    assert local(date(2026, 10, 25), 1) == slot - timedelta(hours=25)


def test_lag_is_empty_where_the_local_time_does_not_exist() -> None:
    names = ["lag_1d_w", "lag_7d_w"]
    # Frühjahrsumstellung 29.03.2026: 02:30 lokal gibt es an diesem Tag nicht
    issued = local(date(2026, 3, 30), 0)
    load = filled(issued - timedelta(days=10), issued + timedelta(days=2))
    slot = local(date(2026, 3, 30), 2, 30)
    (row,) = feature_rows(issued, [slot], make_inputs(load_w=load), names)
    assert math.isnan(row[0])
    assert row[1] == load[local(date(2026, 3, 23), 2, 30)]
    # am Tag der Umstellung selbst: sieben Tage vorher gibt es 02:30, einen Tag vorher auch
    issued = local(date(2026, 3, 29), 0)
    (row,) = feature_rows(
        issued, [local(date(2026, 3, 29), 3, 30)], make_inputs(load_w=load), names
    )
    assert row == [load[local(date(2026, 3, 28), 3, 30)], load[local(date(2026, 3, 22), 3, 30)]]


def test_lag_uses_the_first_of_two_equal_local_times() -> None:
    names = ["lag_1d_w"]
    # 25.10.2026 02:15 lokal gibt es zweimal; der Folgetag nimmt das erste Mal (00:15 UTC)
    issued = local(date(2026, 10, 26), 0)
    load = filled(issued - timedelta(days=3), issued + timedelta(days=1))
    (row,) = feature_rows(
        issued, [local(date(2026, 10, 26), 2, 15)], make_inputs(load_w=load), names
    )
    assert row == [load[datetime(2026, 10, 25, 0, 15, tzinfo=UTC)]]


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(minutes=7)])
def test_no_value_after_issuance_is_used(offset: timedelta) -> None:
    sensor = "sensor.a"
    names = feature_names([sensor])
    issued = local(date(2026, 10, 20), 12) + offset
    cutoff = slot_start(issued)
    slots = slots_between(cutoff, issued + MAX_LEAD)
    start = issued - timedelta(days=9)
    stop = issued + timedelta(days=4)
    load = filled(start, stop)
    held = filled(start, stop, 20.0)
    temp = filled(start, stop, 5.0)
    before = make_inputs(load_w=load, temp_c=temp, sensors={sensor: held})
    # ab dem Slot der Ausgabe ändern sich Last und Sensor, auch die Slots, die als Lag taugen würden
    after = make_inputs(
        load_w={s: (v if s < cutoff else v + 1e6) for s, v in load.items()},
        temp_c=temp,
        sensors={sensor: {s: (v if s < cutoff else v - 1e6) for s, v in held.items()}},
    )
    assert same(
        feature_rows(issued, slots, before, names), feature_rows(issued, slots, after, names)
    )
    # und die Werte davor wirken tatsächlich
    changed = make_inputs(
        load_w={s: (v if s >= cutoff - SLOT else v + 1.0) for s, v in load.items()},
        temp_c=temp,
        sensors={sensor: held},
    )
    assert not same(
        feature_rows(issued, slots, before, names), feature_rows(issued, slots, changed, names)
    )


def test_recent_is_mean_of_last_hour() -> None:
    names = ["recent_w"]
    issued = local(date(2026, 10, 20), 12)
    cutoff = slot_start(issued)
    load = {cutoff - 5 * SLOT: 1000.0, cutoff: 5000.0}
    load |= {cutoff - 4 * SLOT: 100.0, cutoff - 3 * SLOT: 200.0}
    load |= {cutoff - 2 * SLOT: 300.0, cutoff - SLOT: 400.0}
    rows = feature_rows(issued, [cutoff, cutoff + 10 * SLOT], make_inputs(load_w=load), names)
    assert rows == [[250.0], [250.0]]


def test_recent_uses_the_slots_that_exist() -> None:
    names = ["recent_w"]
    issued = local(date(2026, 10, 20), 12)
    cutoff = slot_start(issued)
    load = {cutoff - 3 * SLOT: 100.0, cutoff - SLOT: 300.0}
    assert feature_rows(issued, [cutoff], make_inputs(load_w=load), names) == [[200.0]]
    only_old = {cutoff - 5 * SLOT: 100.0}
    (row,) = feature_rows(issued, [cutoff], make_inputs(load_w=only_old), names)
    assert math.isnan(row[0])


def test_recent_load_ignores_nan() -> None:
    names = ["recent_w"]
    issued = local(date(2026, 10, 20), 12)
    cutoff = slot_start(issued)
    load = {cutoff - 3 * SLOT: 100.0, cutoff - 2 * SLOT: math.nan, cutoff - SLOT: 300.0}
    assert feature_rows(issued, [cutoff], make_inputs(load_w=load), names) == [[200.0]]
    all_nan = {cutoff - SLOT: math.nan}
    (row,) = feature_rows(issued, [cutoff], make_inputs(load_w=all_nan), names)
    assert math.isnan(row[0])


def test_sensor_value_is_held_over_the_lead() -> None:
    sensor = "person.b"
    names = feature_names([sensor, "sensor.gone"])
    issued = local(date(2026, 10, 20), 12)
    cutoff = slot_start(issued)
    held = {cutoff - 2 * SLOT: 7.0, cutoff - SLOT: 1.0, cutoff: 9.0, cutoff + SLOT: 8.0}
    slots = [cutoff, cutoff + SLOT, cutoff + 50 * SLOT, cutoff + 287 * SLOT]
    rows = feature_rows(issued, slots, make_inputs(sensors={sensor: held}), names)
    assert column(rows, names, "ha:person.b") == [1.0] * 4
    # ein Sensor, der nicht in den Eingaben steht, ist leer
    assert all(math.isnan(v) for v in column(rows, names, "ha:sensor.gone"))


def test_sensor_without_value_before_the_issuance_is_empty() -> None:
    names = feature_names(["sensor.a"])
    issued = local(date(2026, 10, 20), 12)
    cutoff = slot_start(issued)
    sensors = {"sensor.a": {cutoff - 2 * SLOT: 3.0}}  # der Slot direkt davor fehlt
    (row,) = feature_rows(issued, [cutoff], make_inputs(sensors=sensors), names)
    assert math.isnan(row[names.index("ha:sensor.a")])


def test_profile_feature_is_profile_of_issue_day() -> None:
    names = ["profile_w"]
    asked: list[date] = []
    profiles = {
        date(2026, 10, 20): LoadProfile({}, {time(13, 0): 111.0}, 50.0, 10),
        date(2026, 10, 21): LoadProfile({}, {time(13, 0): 222.0}, 60.0, 10),
    }

    def profile_for(day: date) -> LoadProfile | None:
        asked.append(day)
        return profiles.get(day)

    inputs = FeatureInputs({}, {}, {}, profile_for)
    issued = local(date(2026, 10, 20), 18)
    # das Profil des Ausgabetags gilt auch für Slots des Folgetags
    slots = [local(date(2026, 10, 20), 19), local(date(2026, 10, 21), 13)]
    rows = feature_rows(issued, slots, inputs, names)
    assert rows == [[50.0], [111.0]]
    assert set(asked) == {date(2026, 10, 20)}


def test_profile_feature_is_empty_without_a_profile() -> None:
    names = ["profile_w"]
    issued = local(date(2026, 10, 20), 18)
    (row,) = feature_rows(issued, [issued], make_inputs(profile=None), names)
    assert math.isnan(row[0])
    empty = LoadProfile({}, {}, None, 0)
    (row,) = feature_rows(issued, [issued], make_inputs(profile=empty), names)
    assert math.isnan(row[0])


def test_temperature_features() -> None:
    names = ["temp_c", "temp_day_c"]
    day = date(2026, 10, 21)
    slots = local_day_slots(day)
    temp = {slot: 10.0 for slot in slots[:48]} | {slot: 20.0 for slot in slots[48:90]}
    issued = local(date(2026, 10, 20), 18)
    mean = (48 * 10.0 + 42 * 20.0) / 90
    rows = feature_rows(issued, [slots[0], slots[60]], make_inputs(temp_c=temp), names)
    assert rows == [[10.0, mean], [20.0, mean]]
    # Slot ohne Temperatur: eigener Wert leer, Tagesmittel aus den bekannten
    (row,) = feature_rows(issued, [slots[95]], make_inputs(temp_c=temp), names)
    assert math.isnan(row[0])
    assert row[1] == mean


def test_missing_inputs_are_nan() -> None:
    names = feature_names(["sensor.a"])
    issued = local(date(2026, 10, 20), 6)
    (row,) = feature_rows(issued, [issued + timedelta(hours=1)], make_inputs(), names)
    value = dict(zip(names, row, strict=True))
    for name in ("temp_c", "temp_day_c", "lag_1d_w", "lag_7d_w", "recent_w", "profile_w"):
        assert math.isnan(value[name]), name
    assert math.isnan(value["ha:sensor.a"])
    assert not math.isnan(value["lead_h"])
    assert not math.isnan(value["minute_of_day"])


def test_feature_rows_follow_the_requested_names() -> None:
    issued = local(date(2026, 10, 20), 6)
    slot = issued + timedelta(hours=2)
    (row,) = feature_rows(issued, [slot], make_inputs(), ["month", "lead_h"])
    assert row == [10.0, 2.0]
    assert feature_rows(issued, [], make_inputs(), ["month"]) == []


def test_unknown_feature_name_is_rejected() -> None:
    issued = local(date(2026, 10, 20), 6)
    with pytest.raises(ValueError, match="unbekannt"):
        feature_rows(issued, [issued], make_inputs(), ["month", "wetter"])


def test_slots_without_actual_are_skipped() -> None:
    names = feature_names([])
    day = date(2026, 10, 20)
    issued = local(day, 6)
    cutoff = slot_start(issued)
    load = {cutoff: 10.0, cutoff + SLOT: 11.0, cutoff + 3 * SLOT: 13.0}
    load[cutoff + 2 * SLOT] = NAN  # kein brauchbarer Ist-Wert
    result = training_set(make_inputs(load_w=load), issued, issued + timedelta(hours=1), names)
    assert result.slots == [cutoff, cutoff + SLOT, cutoff + 3 * SLOT]
    assert result.targets == [10.0, 11.0, 13.0]
    assert result.issued == [issued] * 3
    assert len(result.rows) == 3
    assert all(len(row) == len(names) for row in result.rows)
    assert column(result.rows, names, "lead_h") == [0.0, 0.25, 0.75]


def test_training_set_covers_four_issuances_with_72_hours_each() -> None:
    names = ["lead_h", "minute_of_day"]
    day = date(2026, 10, 20)
    start = local(day, 0)
    end = local(day + timedelta(days=5), 0)
    load = filled(start - timedelta(days=1), end + timedelta(days=1))
    result = training_set(make_inputs(load_w=load), start, end, names)
    assert len(result.rows) == len(result.targets) == len(result.issued) == len(result.slots)
    assert sorted(set(result.issued)) == issuances(start, end)
    assert len(set(result.issued)) == 20
    for row, target, issued, slot in zip(
        result.rows, result.targets, result.issued, result.slots, strict=True
    ):
        assert target == load[slot]
        assert slot_start(issued) <= slot < min(issued + MAX_LEAD, end)
        assert row[0] == pytest.approx((slot - issued) / timedelta(hours=1))
    # die erste Ausgabe hat alle 288 Slots der 72 h
    assert result.issued.count(start) == 288
    # dieselben Zeilen wie feature_rows je Ausgabe
    mine = [row for row, issued in zip(result.rows, result.issued, strict=True) if issued == start]
    slots = [s for s, issued in zip(result.slots, result.issued, strict=True) if issued == start]
    assert same(mine, feature_rows(start, slots, make_inputs(load_w=load), names))


def test_training_set_for_one_day_has_the_shrinking_leads() -> None:
    day = date(2026, 10, 20)
    start = local(day, 0)
    end = local(day + timedelta(days=1), 0)
    load = filled(start - timedelta(days=1), end + timedelta(days=4))
    result = training_set(make_inputs(load_w=load), start, end, ["lead_h"])
    assert len(result.rows) == 96 + 72 + 48 + 24  # Slots vor ``end`` ab 00, 06, 12 und 18 Uhr


def test_training_set_stops_at_end() -> None:
    names = ["lead_h"]
    day = date(2026, 10, 20)
    start = local(day, 0)
    end = local(day, 12)  # Ausgaben um 00:00 und 06:00
    load = filled(start - timedelta(days=1), start + timedelta(days=6))
    result = training_set(make_inputs(load_w=load), start, end, names)
    assert set(result.issued) == {local(day, 0), local(day, 6)}
    assert all(slot < end for slot in result.slots)
    # Ausgabe um 00:00: 48 Slots bis 12:00; um 06:00: 24 Slots
    assert len(result.rows) == 48 + 24
    # eine Ausgabe nach dem Ende gibt es nicht, ihre Slots aber auch nicht vor dem Ende
    assert training_set(make_inputs(load_w=load), start, start, names).rows == []


def test_training_set_uses_the_profile_of_the_issue_day() -> None:
    names = ["profile_w", "lag_1d_w"]
    day = date(2026, 10, 20)
    start = local(day, 0)
    asked: list[date] = []

    def profile_for(d: date) -> LoadProfile | None:
        asked.append(d)
        return LoadProfile({}, {}, float(d.day), 1)

    load = filled(start - timedelta(days=2), start + timedelta(days=5))
    inputs = FeatureInputs(load, {}, {}, profile_for)
    result = training_set(inputs, start, local(day + timedelta(days=1), 0), names)
    assert set(asked) == {day}
    assert set(column(result.rows, names, "profile_w")) == {20.0}


def test_profile_cache_uses_only_samples_before_local_midnight() -> None:
    day = date(2026, 10, 20)
    midnight = local(day, 0)
    early = [
        LoadSample(midnight - (i + 1) * SLOT, 100.0, "ledger")
        for i in range(10)  # der letzte Tag davor
    ]
    future = [LoadSample(midnight + i * SLOT, 9000.0, "ledger") for i in range(10)]
    only_past = profile_cache(early)
    with_future = profile_cache(early + future)
    profile = with_future(day)
    assert profile is not None and profile.overall == 100.0
    assert profile.samples == 10
    expected = only_past(day)
    assert expected is not None and expected.by_time == profile.by_time
    # die Probe um Mitternacht selbst gehört schon zum neuen Tag
    sample = LoadSample(midnight, 500.0, "ledger")
    boundary = profile_cache([sample])
    assert boundary(day) is None
    later = boundary(day + timedelta(days=1))
    assert later is not None and later.overall == 500.0


def test_profile_cache_is_memoised_per_local_day() -> None:
    samples = [
        LoadSample(local(date(2026, 10, 19), 12) + i * SLOT, 100.0 + i, "ledger") for i in range(8)
    ]
    cache = profile_cache(samples)
    first = cache(date(2026, 10, 20))
    assert first is not None
    assert cache(date(2026, 10, 20)) is first
    other = cache(date(2026, 10, 21))
    assert other is not first


def test_profile_cache_without_samples_has_no_profile() -> None:
    assert profile_cache([])(date(2026, 10, 20)) is None
    old = [LoadSample(local(date(2026, 1, 1), 12), 100.0, "ledger")]
    assert profile_cache(old)(date(2026, 10, 20)) is None  # älter als das 56-Tage-Fenster


def test_profile_cache_matches_build_profile() -> None:
    from bindaems.app.forecast.load import build_profile

    samples = [
        LoadSample(local(date(2026, 10, 1), 0) + i * SLOT, 100.0 + (i % 17) * 10, "ledger")
        for i in range(19 * 96)
    ]
    day = date(2026, 10, 20)
    midnight = local(day, 0)
    expected = build_profile([s for s in samples if s.slot_start < midnight], midnight)
    got = profile_cache(samples)(day)
    assert got is not None
    assert got.by_key == expected.by_key
    assert got.by_time == expected.by_time
    assert got.overall == expected.overall
    assert got.samples == expected.samples
