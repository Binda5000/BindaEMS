from datetime import UTC, date, datetime, timedelta

import pytest
from tests.unit.app.ledger.conftest import FakeInflux

from bindaems.app.history.influx import Series
from bindaems.app.ledger.counters import counter_tags, reading_near, readings_at, soc_near
from bindaems.shared.timeutil import LOCAL_TZ, local_day_slots

SIGNALS = [
    "grid.energy_import_kwh",
    "grid.energy_export_kwh",
    "pv.huawei.energy_kwh",
    "wallbox.evcs.total_kwh",
]


def midnights(first: date, count: int) -> list[datetime]:
    return [local_day_slots(first + timedelta(days=n))[0] for n in range(count)]


def test_counter_tags_follow_the_core_mapping() -> None:
    assert counter_tags("grid.energy_import_kwh") == ("grid", "grid", "import")
    assert counter_tags("grid.energy_export_kwh") == ("grid", "grid", "export")
    assert counter_tags("pv.huawei.energy_kwh") == ("pv", "huawei", "forward")
    assert counter_tags("wallbox.evcs.total_kwh") == ("wallbox", "evcs", "total")


@pytest.mark.parametrize(
    "signal",
    ["grid.energy_kwh", "load.haus.energy_kwh", "wallbox.evcs.session_kwh", "pv..energy_kwh", ""],
)
def test_counter_tags_unknown_signal(signal: str) -> None:
    assert counter_tags(signal) is None


async def test_readings_at_uses_one_query(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 8)
    influx.set_counters(
        {signal: {i: 100.0 + n for n, i in enumerate(instants)} for signal in SIGNALS}
    )
    result = await readings_at(influx, SIGNALS, instants)  # type: ignore[arg-type]
    assert len(influx.queries) == 1
    query = influx.queries[0]
    assert 'FROM "long"."energy"' in query and "GROUP BY time(30m)" in query
    assert 'last("kwh")' in query and "fill(none)" in query
    for tags in ("'grid'", "'huawei'", "'evcs'", "'forward'", "'total'"):
        assert tags in query
    assert len(result) == 4 * 8
    assert result[("grid.energy_import_kwh", instants[3])] == 103.0
    assert result[("wallbox.evcs.total_kwh", instants[7])] == 107.0


async def test_readings_at_missing_values_are_none(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 2)
    influx.set_counters({"grid.energy_import_kwh": {instants[0]: 5.0}})
    influx.series[0].values.append([0, None])  # ein Eimer ohne Wert
    result = await readings_at(influx, ["grid.energy_import_kwh", "pv.huawei.energy_kwh"], instants)  # type: ignore[arg-type]
    assert result[("grid.energy_import_kwh", instants[0])] == 5.0
    assert result[("grid.energy_import_kwh", instants[1])] is None
    assert result[("pv.huawei.energy_kwh", instants[0])] is None


async def test_readings_at_unknown_signals_need_no_query(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 2)
    result = await readings_at(influx, ["load.haus.energy_kwh"], instants)  # type: ignore[arg-type]
    assert influx.queries == []
    assert result == {("load.haus.energy_kwh", i): None for i in instants}


async def test_readings_at_rejects_instants_off_the_half_hour(influx: FakeInflux) -> None:
    with pytest.raises(ValueError, match="halben Stunde"):
        await readings_at(influx, SIGNALS, [datetime(2026, 10, 1, 10, 7, tzinfo=UTC)])  # type: ignore[arg-type]


async def test_midnight_readings_on_dst_change(influx: FakeInflux) -> None:
    # 25.10.2026: Mitternacht in Wien ist 22:00 UTC davor (MESZ), 23:00 UTC danach (MEZ)
    before, after = midnights(date(2026, 10, 25), 2)
    assert before == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert after == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert before.astimezone(LOCAL_TZ).hour == 0 and after.astimezone(LOCAL_TZ).hour == 0
    influx.set_counters({"grid.energy_import_kwh": {before: 500.0, after: 530.0}})
    result = await readings_at(influx, ["grid.energy_import_kwh"], [before, after])  # type: ignore[arg-type]
    assert result == {
        ("grid.energy_import_kwh", before): 500.0,
        ("grid.energy_import_kwh", after): 530.0,
    }
    # die Eimer sind die halben Stunden davor: 21:30 UTC und 22:30 UTC
    assert "time >= 1792877400000ms AND time < 1792969200000ms" in influx.queries[0]


async def test_reading_near_takes_the_last_value_before_and_the_first_after(
    influx: FakeInflux,
) -> None:
    t = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    influx.series = [Series("energy", {}, ["time", "last"], [[1, 42.5]])]
    assert await reading_near(influx, "grid.energy_import_kwh", t, before=True) == 42.5  # type: ignore[arg-type]
    assert await reading_near(influx, "grid.energy_import_kwh", t, before=False) == 42.5  # type: ignore[arg-type]
    last, first = influx.queries
    assert 'last("kwh")' in last and 'FROM "raw"."energy"' in last
    assert "time >= 1791532800000ms - 15m" not in last  # feste Zahlen, keine Ausdrücke
    assert "time >= 1791531900000ms AND time <= 1791532800000ms" in last
    assert 'first("kwh")' in first
    assert "time >= 1791532800000ms AND time <= 1791533700000ms" in first
    assert "'grid'" in last and "'import'" in last


async def test_reading_near_without_value_or_unknown_signal(influx: FakeInflux) -> None:
    t = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    assert await reading_near(influx, "grid.energy_import_kwh", t, before=True) is None  # type: ignore[arg-type]
    assert await reading_near(influx, "load.haus.energy_kwh", t, before=True) is None  # type: ignore[arg-type]
    assert len(influx.queries) == 1


async def test_soc_near_reads_the_battery_percentage(influx: FakeInflux) -> None:
    t = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    influx.series = [Series("soc", {}, ["time", "first"], [[1, 61.0]])]
    assert await soc_near(influx, t, before=False) == 61.0  # type: ignore[arg-type]
    [query] = influx.queries
    assert 'first("pct")' in query and 'FROM "raw"."soc"' in query and "'battery'" in query
