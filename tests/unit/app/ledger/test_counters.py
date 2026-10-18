from datetime import UTC, date, datetime, timedelta

import pytest
from tests.unit.app.ledger.conftest import FakeInflux

from bindaems.app.history.influx import Series
from bindaems.app.ledger.counters import counter_tags, reading_near, readings_at, soc_near
from bindaems.shared.influx.lineprotocol import epoch_ms
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


SECOND = timedelta(seconds=1)
IMP = "grid.energy_import_kwh"


async def test_readings_at_uses_one_request(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 8)
    influx.set_counters(
        {signal: {i: 100.0 + n for n, i in enumerate(instants)} for signal in SIGNALS}
    )
    result = await readings_at(influx, SIGNALS, instants)  # type: ignore[arg-type]
    assert len(influx.queries) == 1
    statements = influx.queries[0].split(";")
    assert len(statements) == 2 * 8  # je Zeitpunkt davor und danach, nicht je Signal
    assert all('FROM "raw"."energy"' in st and "GROUP BY time" not in st for st in statements)
    assert 'last("kwh") AS "b3"' in statements[6] and 'first("kwh") AS "a3"' in statements[7]
    for source in ("'grid'", "'pv'", "'wallbox'"):
        assert source in statements[0]
    assert len(result) == 4 * 8
    assert result[(IMP, instants[3])] == 103.0
    assert result[("wallbox.evcs.total_kwh", instants[7])] == 107.0


async def test_readings_at_interpolates_between_the_values_around_the_instant(
    influx: FakeInflux,
) -> None:
    [instant] = midnights(date(2026, 10, 9), 1)
    influx.set_counters(
        {
            IMP: {
                instant - 100 * SECOND: 99.0,
                instant - 40 * SECOND: 100.0,  # letzter Wert davor
                instant + 20 * SECOND: 100.3,  # erster Wert ab dem Zeitpunkt
                instant + 80 * SECOND: 101.0,
            }
        }
    )
    result = await readings_at(influx, [IMP], [instant])  # type: ignore[arg-type]
    assert result[(IMP, instant)] == pytest.approx(100.2)
    windows = influx.queries[0].split(";")
    assert (
        f"time >= {epoch_ms(instant) - 1_800_000}ms AND time < {epoch_ms(instant)}ms" in windows[0]
    )
    assert (
        f"time >= {epoch_ms(instant)}ms AND time < {epoch_ms(instant) + 1_800_000}ms" in windows[1]
    )


async def test_readings_at_without_the_other_side_takes_a_close_value(influx: FakeInflux) -> None:
    first, second, third, fourth = midnights(date(2026, 10, 1), 4)
    influx.set_counters(
        {
            IMP: {
                first - 2 * 60 * SECOND: 5.0,  # 2 min davor, danach nichts: gilt
                second + 2 * 60 * SECOND: 6.0,  # 2 min danach, davor nichts: gilt
                third - 3 * 60 * SECOND: 7.0,  # 3 min davor: zu weit weg
                fourth + 29 * 60 * SECOND: 8.0,  # 29 min danach: zu weit weg
            }
        }
    )
    result = await readings_at(influx, [IMP], [first, second, third, fourth])  # type: ignore[arg-type]
    assert [result[(IMP, i)] for i in (first, second, third, fourth)] == [5.0, 6.0, None, None]


async def test_readings_at_takes_the_value_before_a_counter_reset(influx: FakeInflux) -> None:
    [instant] = midnights(date(2026, 10, 9), 1)
    influx.set_counters({IMP: {instant - 40 * SECOND: 900.0, instant + 20 * SECOND: 0.5}})
    result = await readings_at(influx, [IMP], [instant])  # type: ignore[arg-type]
    assert result[(IMP, instant)] == 900.0


async def test_readings_at_values_beyond_30_minutes_do_not_count(influx: FakeInflux) -> None:
    [instant] = midnights(date(2026, 10, 9), 1)
    influx.set_counters({IMP: {instant - 31 * 60 * SECOND: 1.0, instant + 30 * 60 * SECOND: 2.0}})
    result = await readings_at(influx, [IMP], [instant])  # type: ignore[arg-type]
    assert result[(IMP, instant)] is None


async def test_readings_at_missing_values_are_none(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 2)
    influx.set_counters({IMP: {instants[0]: 5.0}})
    result = await readings_at(influx, [IMP, "pv.huawei.energy_kwh"], instants)  # type: ignore[arg-type]
    assert result[(IMP, instants[0])] == 5.0
    assert result[(IMP, instants[1])] is None
    assert result[("pv.huawei.energy_kwh", instants[0])] is None


async def test_readings_at_ignores_other_series_and_columns(influx: FakeInflux) -> None:
    [instant] = midnights(date(2026, 10, 9), 1)
    stamp = epoch_ms(instant)
    influx.series = [
        Series(
            "energy",
            {"source": "grid", "id": "grid", "direction": "import"},
            ["time", "a0"],
            [[stamp, 4.0]],
        ),
        Series(
            "energy",
            {"source": "pv", "id": "andere", "direction": "forward"},
            ["time", "a0"],
            [[stamp, 9.0]],
        ),
        Series(
            "energy",
            {"source": "grid", "id": "grid", "direction": "export"},
            ["time", "kwh"],
            [[stamp, 9.0]],
        ),
        Series(
            "energy",
            {"source": "grid", "id": "grid", "direction": "export"},
            ["time", "b0"],
            [[stamp, None]],
        ),
    ]
    result = await readings_at(  # type: ignore[arg-type]
        influx, [IMP, "grid.energy_export_kwh", "pv.huawei.energy_kwh"], [instant]
    )
    assert result == {
        (IMP, instant): 4.0,
        ("grid.energy_export_kwh", instant): None,
        ("pv.huawei.energy_kwh", instant): None,
    }


async def test_readings_at_unknown_signals_need_no_query(influx: FakeInflux) -> None:
    instants = midnights(date(2026, 10, 1), 2)
    result = await readings_at(influx, ["load.haus.energy_kwh"], instants)  # type: ignore[arg-type]
    assert influx.queries == []
    assert result == {("load.haus.energy_kwh", i): None for i in instants}


async def test_midnight_readings_on_dst_change(influx: FakeInflux) -> None:
    # 25.10.2026: Mitternacht in Wien ist 22:00 UTC davor (MESZ), 23:00 UTC danach (MEZ)
    before, after = midnights(date(2026, 10, 25), 2)
    assert before == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert after == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert before.astimezone(LOCAL_TZ).hour == 0 and after.astimezone(LOCAL_TZ).hour == 0
    influx.set_counters(
        {
            IMP: {
                before - 30 * SECOND: 499.9,
                before + 30 * SECOND: 500.1,
                after - 30 * SECOND: 529.9,
                after + 30 * SECOND: 530.1,
            }
        }
    )
    result = await readings_at(influx, [IMP], [before, after])  # type: ignore[arg-type]
    assert result[(IMP, before)] == pytest.approx(500.0)
    assert result[(IMP, after)] == pytest.approx(530.0)
    # die Fenster liegen um 22:00 und 23:00 UTC
    statements = influx.queries[0].split(";")
    assert "time >= 1792879200000ms AND time < 1792881000000ms" in statements[1]
    assert "time >= 1792969200000ms AND time < 1792971000000ms" in statements[3]


async def test_midnight_readings_on_spring_dst_change(influx: FakeInflux) -> None:
    # 29.03.2026: Mitternacht in Wien ist 23:00 UTC davor (MEZ), 22:00 UTC danach (MESZ)
    before, after = midnights(date(2026, 3, 29), 2)
    assert before == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
    assert after == datetime(2026, 3, 29, 22, 0, tzinfo=UTC)
    assert before.astimezone(LOCAL_TZ).hour == 0 and after.astimezone(LOCAL_TZ).hour == 0
    influx.set_counters(
        {
            IMP: {
                before - 30 * SECOND: 499.9,
                before + 30 * SECOND: 500.1,
                after - 30 * SECOND: 522.9,
                after + 30 * SECOND: 523.1,
            }
        }
    )
    result = await readings_at(influx, [IMP], [before, after])  # type: ignore[arg-type]
    assert result[(IMP, before)] == pytest.approx(500.0)
    assert result[(IMP, after)] == pytest.approx(523.0)
    # die Fenster liegen um 23:00 und 22:00 UTC
    statements = influx.queries[0].split(";")
    assert "time >= 1774737000000ms AND time < 1774738800000ms" in statements[0]
    assert "time >= 1774821600000ms AND time < 1774823400000ms" in statements[3]


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
