from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from bindaems.app.forecast.openmeteo import WeatherSlot
from bindaems.app.history.influx import Series
from bindaems.app.learning.thermal_data import ThermalDataError, read_thermal_slots
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.influx.lineprotocol import epoch_ms

START = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
END = START + timedelta(hours=1)  # vier Viertelstunden
SLOT = timedelta(minutes=15)
DEVICE = "klima"
BITS = ("running", "heating", "cooling", "venting")


def _thermal(*rows: tuple[datetime, float | None, float | None, float | None]) -> Series:
    return Series(
        "thermal",
        {},
        ["time", "actual_c", "setpoint_c", "offset_k"],
        [[epoch_ms(ts), actual, setpoint, offset] for ts, actual, setpoint, offset in rows],
    )


def _means(name: str, field: str, rows: dict[datetime, float], **tags: str) -> Series:
    return Series(name, tags, ["time", field], [[epoch_ms(ts), v] for ts, v in rows.items()])


def _bit(bit: str, *rows: tuple[datetime, bool]) -> Series:
    return Series(
        "status",
        {"id": f"thermal.{DEVICE}.{bit}"},
        ["time", "value_bool"],
        [[epoch_ms(ts), value] for ts, value in rows],
    )


@pytest.fixture
def weather(engine) -> WeatherArchive:
    archive = WeatherArchive(engine)
    archive.record(
        [WeatherSlot(START + i * SLOT, 0.0, 10.0 + i) for i in range(4)],
        START + timedelta(hours=4),
    )
    return archive


def _basic(fake_reader) -> None:
    fake_reader.answer(
        '"thermal"',
        [_thermal(*((START + i * SLOT, 21.0 + i, 22.0, -1.0) for i in range(4)))],
    )


async def _read(fake_reader, weather):
    return await read_thermal_slots(fake_reader, weather, DEVICE, START, END)


def _seed(bit: str, *rows: tuple[datetime, bool]) -> Series:
    return Series(
        "status",
        {"id": f"thermal.{DEVICE}.{bit}"},
        ["time", "last"],
        [[epoch_ms(ts), value] for ts, value in rows],
    )


async def test_slots_are_quarter_hour_means(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "'thermal' AND",
        [_means("power", "p_w", {START + i * SLOT: 100.0 * i for i in range(4)})],
    )
    fake_reader.answer(
        "'pv'",
        [
            _means("power", "p_w", {START: 400.0}, id="a"),
            _means("power", "p_w", {START: 100.0, START + SLOT: 50.0}, id="dc"),
        ],
    )
    slots = await _read(fake_reader, weather)
    assert [s.start for s in slots] == [START + i * SLOT for i in range(4)]
    assert [s.actual_c for s in slots] == [21.0, 22.0, 23.0, 24.0]
    assert slots[0].setpoint_c == 22.0 and slots[0].offset_k == -1.0
    assert [s.power_w for s in slots] == [0.0, 100.0, 200.0, 300.0]
    assert [s.outdoor_c for s in slots] == [10.0, 11.0, 12.0, 13.0]
    # Viertelstunde 1 hat nur die Reihe dc: keine Teilsumme, sondern unbekannt
    assert [s.pv_w for s in slots] == [500.0, None, None, None]
    assert all(s.running is None and s.mode is None for s in slots)  # keine Bits gelesen
    text, database = fake_reader.queries[0]
    assert '"raw"."thermal"' in text and "GROUP BY time(15m)" in text and database is None
    assert f"time >= {epoch_ms(START)}ms AND time < {epoch_ms(END)}ms" in text


async def test_running_needs_80_percent(fake_reader, weather) -> None:
    _basic(fake_reader)
    # Viertelstunde 0: 12 min an (0.8), 1: 13,5 min (0.9), 2: 2 min (0.13), 3: 7,5 min (0.5)
    fake_reader.answer(
        'SELECT "value_bool"',
        [
            _bit(
                "running",
                (START, True),
                (START + timedelta(minutes=12), False),
                (START + SLOT + timedelta(seconds=90), True),
                (START + 2 * SLOT, False),
                (START + 2 * SLOT + timedelta(minutes=13), True),
                (START + 3 * SLOT, False),
                (START + 3 * SLOT + timedelta(seconds=450), True),
            )
        ],
    )
    slots = await _read(fake_reader, weather)
    assert [s.running for s in slots] == [True, True, False, None]


async def test_flags_carry_last_status_value_into_window(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer("last(", [_seed("running", (START - timedelta(days=3), True))])
    slots = await _read(fake_reader, weather)
    assert [s.running for s in slots] == [True] * 4
    seed = fake_reader.texts("last(")[0]
    assert f"time < {epoch_ms(START)}ms" in seed and '"status"' in seed
    assert 'GROUP BY "id"' in seed


async def test_flag_before_first_point_is_unknown(fake_reader, weather) -> None:
    _basic(fake_reader)
    # kein Wert vor dem Fenster; die erste Änderung kommt mitten in der zweiten Viertelstunde
    fake_reader.answer(
        'SELECT "value_bool"',
        [_bit("running", (START + SLOT + timedelta(minutes=5), True))],
    )
    slots = await _read(fake_reader, weather)
    assert slots[0].running is None and slots[1].running is None  # Anfang der zweiten unbekannt
    assert slots[2].running is True and slots[3].running is True


async def test_modes_follow_core_precedence(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "last(",
        [
            _seed("running", (START - SLOT, True)),
            _seed("cooling", (START - SLOT, True)),
            _seed("heating", (START - SLOT, False)),
            _seed("venting", (START - SLOT, False)),
        ],
    )
    fake_reader.answer(
        'SELECT "value_bool"',
        [
            _bit("running", (START + 2 * SLOT, False)),
            _bit("heating", (START + SLOT, True)),
            _bit("venting", (START + SLOT, True)),
        ],
    )
    slots = await _read(fake_reader, weather)
    # 0: nur kühlen; 1 und 2 (bis 2*SLOT): heizen gewinnt vor lüften und kühlen; 3: aus
    assert [s.mode for s in slots] == ["kuehlen", "heizen", "aus", "aus"]


async def test_mixed_mode_slot_has_no_mode(fake_reader, weather) -> None:
    _basic(fake_reader)
    half = timedelta(minutes=7, seconds=30)
    fake_reader.answer(
        "last(",
        [
            _seed("running", (START - SLOT, True)),
            _seed("heating", (START - SLOT, True)),
            _seed("cooling", (START - SLOT, False)),
            _seed("venting", (START - SLOT, False)),
        ],
    )
    fake_reader.answer(
        'SELECT "value_bool"',
        [
            _bit("heating", (START + half, False)),
            _bit("cooling", (START + half, True)),
        ],
    )
    slots = await _read(fake_reader, weather)
    assert slots[0].running is True and slots[0].mode is None  # je zur Hälfte
    assert slots[1].mode == "kuehlen"
    assert slots[2].mode == "kuehlen" and slots[3].mode == "kuehlen"


async def test_unknown_bit_before_the_set_one_gives_no_mode(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "last(",
        [_seed("running", (START - SLOT, True)), _seed("cooling", (START - SLOT, True))],
    )  # ``heating`` und ``venting`` kennt niemand: eines von ihnen könnte vorgehen
    slots = await _read(fake_reader, weather)
    assert [s.mode for s in slots] == [None] * 4


async def test_running_without_any_mode_bit_has_no_mode(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer("last(", [_seed("running", (START - SLOT, True))])
    slots = await _read(fake_reader, weather)
    assert [s.running for s in slots] == [True] * 4
    assert [s.mode for s in slots] == [None] * 4


async def test_slot_without_outdoor_temp_is_missing(fake_reader, engine) -> None:
    archive = WeatherArchive(engine)
    archive.record(
        [WeatherSlot(START + i * SLOT, 0.0, 5.0) for i in (0, 2, 3)], START + timedelta(hours=4)
    )
    _basic(fake_reader)
    slots = await read_thermal_slots(fake_reader, archive, DEVICE, START, END)
    assert [s.start for s in slots] == [START, START + 2 * SLOT, START + 3 * SLOT]


async def test_slot_without_actual_temperature_is_missing(fake_reader, weather) -> None:
    fake_reader.answer(
        '"thermal"',
        [
            _thermal(
                (START, 21.0, None, None),
                (START + SLOT, None, 22.0, 0.0),
                (START + 2 * SLOT, float("nan"), 22.0, 0.0),
            )
        ],
    )
    slots = await _read(fake_reader, weather)
    assert [s.start for s in slots] == [START]
    assert slots[0].setpoint_c is None and slots[0].offset_k is None


async def test_pv_sums_all_planes(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "'pv'",
        [
            _means("power", "p_w", {START: 1000.0, START + SLOT: 10.0}, id="south"),
            _means("power", "p_w", {START: 250.0}, id="west"),
            _means("power", "p_w", {START: 50.0}, id="dc"),
        ],
    )
    slots = await _read(fake_reader, weather)
    assert slots[0].pv_w == 1300.0 and slots[1].pv_w is None and slots[2].pv_w is None
    text = fake_reader.texts("'pv'")[0]
    assert "\"phase\"='total'" in text and 'GROUP BY time(15m),"id"' in text


async def test_pv_with_a_missing_plane_is_unknown(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "'pv'",
        [
            _means("power", "p_w", {START: 1000.0, START + SLOT: 900.0}, id="south"),
            _means("power", "p_w", {START + SLOT: 100.0}, id="west"),
        ],
    )
    slots = await _read(fake_reader, weather)
    # Viertelstunde 0 kennt nur south: die Teilsumme 1000 W wäre zu klein, also kein Wert
    assert [s.pv_w for s in slots] == [None, 1000.0, None, None]


async def test_pv_without_dc_counts_it_as_zero(fake_reader, weather) -> None:
    _basic(fake_reader)
    fake_reader.answer(
        "'pv'",
        [
            _means("power", "p_w", {START: 1000.0, START + SLOT: 0.0}, id="south"),
            _means("power", "p_w", {START: 50.0}, id="dc"),
        ],
    )
    slots = await _read(fake_reader, weather)
    # wie Derived.pv_total_w im core: ohne dc zählt nur der Wechselrichter
    assert [s.pv_w for s in slots] == [1050.0, 0.0, None, None]


async def test_read_error_raises_thermal_data_error(fake_reader, weather, unreadable) -> None:
    fake_reader.answer('"thermal"', unreadable())
    with pytest.raises(ThermalDataError, match="nicht lesbar"):
        await _read(fake_reader, weather)


async def test_unreadable_status_raises_thermal_data_error(
    fake_reader, weather, unreadable
) -> None:
    _basic(fake_reader)
    fake_reader.answer('"status"', unreadable())
    with pytest.raises(ThermalDataError):
        await _read(fake_reader, weather)


async def test_unreadable_weather_raises_thermal_data_error(fake_reader) -> None:
    class Broken:
        def temperatures(self, start: datetime, end: datetime) -> dict[datetime, float]:
            raise OperationalError("select", {}, Exception("gesperrt"))

    _basic(fake_reader)
    with pytest.raises(ThermalDataError, match="Wetterarchiv"):
        await read_thermal_slots(fake_reader, Broken(), DEVICE, START, END)  # type: ignore[arg-type]


async def test_window_without_whole_slots_reads_nothing(fake_reader, weather) -> None:
    slots = await read_thermal_slots(
        fake_reader, weather, DEVICE, START + timedelta(minutes=1), START + timedelta(minutes=14)
    )
    assert slots == [] and fake_reader.queries == []


async def test_only_whole_slots_inside_window(fake_reader, weather) -> None:
    _basic(fake_reader)
    slots = await read_thermal_slots(
        fake_reader, weather, DEVICE, START + timedelta(minutes=1), END - timedelta(minutes=1)
    )
    assert [s.start for s in slots] == [START + SLOT, START + 2 * SLOT]
