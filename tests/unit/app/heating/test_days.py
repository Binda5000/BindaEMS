import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import Engine, insert
from sqlalchemy.exc import OperationalError
from tests.unit.app.learning.conftest import FakeReader

from bindaems.app.db.schema import heating_day
from bindaems.app.forecast.openmeteo import WeatherSlot
from bindaems.app.heating.days import HeatingDataError, HeatingDays
from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ManualClock, local_day_slots

DEVICE = "klima"
BURNER = f"thermal.{DEVICE}.oil_burner"
DAY = date(2026, 11, 3)
TODAY = DAY + timedelta(days=1)
AC_BITS = f"\"id\" = 'thermal.{DEVICE}.running'"
_WINDOW = re.compile(r"time >= (\d+)ms AND time < (\d+)ms")
_BEFORE = re.compile(r"time < (\d+)ms")


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def noon(day: date) -> ManualClock:
    return ManualClock(at(day, 12))


def _ms(value: datetime) -> int:
    return epoch_ms(value)


class Influx(FakeReader):
    """Brennerstatus und Lebenszeichen nach Zeitfenster; alles andere wie ``FakeReader``."""

    def __init__(self) -> None:
        super().__init__()
        self.burner: list[tuple[datetime, bool]] = []
        self.alive: set[datetime] = set()
        self.broken = False

    def live(self, slots: Iterable[datetime]) -> None:
        self.alive |= set(slots)

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        if self.broken:
            raise InfluxQueryError("InfluxDB: HTTP 500")
        if BURNER in q:
            self.queries.append((q, database))
            if q.startswith("SELECT last("):
                before = int(_BEFORE.search(q).group(1))  # type: ignore[union-attr]
                earlier = [(t, v) for t, v in self.burner if _ms(t) < before]
                if not earlier:
                    return []
                ts, value = max(earlier, key=lambda e: e[0])
                return [Series("status", {"id": BURNER}, ["time", "last"], [[_ms(ts), value]])]
            lo, hi = (int(g) for g in _WINDOW.search(q).groups())  # type: ignore[union-attr]
            rows = [[_ms(t), v] for t, v in self.burner if lo <= _ms(t) < hi]
            return [Series("status", {"id": BURNER}, ["time", "value_bool"], rows)] if rows else []
        if 'count("actual_c")' in q:
            self.queries.append((q, database))
            lo, hi = (int(g) for g in _WINDOW.search(q).groups())  # type: ignore[union-attr]
            rows = [[_ms(s), 40] for s in sorted(self.alive) if lo <= _ms(s) < hi]
            return [Series("thermal", {}, ["time", "n"], rows)] if rows else []
        return await super().query(q, database=database)


def _weather(engine: Engine, slots: Iterable[datetime], temp_c: float = 5.0) -> WeatherArchive:
    archive = WeatherArchive(engine)
    items = [WeatherSlot(slot, 0.0, temp_c) for slot in slots]
    if items:
        archive.record(items, max(item.slot_start for item in items))
    return archive


def _days(
    engine: Engine,
    influx: Influx,
    clock: ManualClock,
    *,
    weather: WeatherArchive | None = None,
    ac_power_known: bool = False,
) -> HeatingDays:
    return HeatingDays(
        engine,
        influx,  # type: ignore[arg-type]
        weather or WeatherArchive(engine),
        DEVICE,
        clock,
        ac_power_known=ac_power_known,
    )


def _store(engine: Engine, day: date, *, burner_end: bool | None, burner_h: float = 1.0) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(heating_day).values(
                day=day.isoformat(),
                burner_h=burner_h,
                known_h=24.0,
                day_h=24.0,
                burner_end=burner_end,
            )
        )


@pytest.fixture
def influx() -> Influx:
    reader = Influx()
    reader.live(local_day_slots(DAY))
    return reader


async def test_burner_hours_from_status_steps(engine: Engine, influx: Influx) -> None:
    # Brenner 06:00–08:00 und 18:00–19:30 an, Quelle lebt den ganzen Tag, 24 h Wetter
    influx.burner = [
        (at(DAY, 6), True),
        (at(DAY, 8), False),
        (at(DAY, 18), True),
        (at(DAY, 19, 30), False),
    ]
    days = _days(engine, influx, noon(TODAY), weather=_weather(engine, local_day_slots(DAY)))
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.burner_h == pytest.approx(3.5)
    assert heat_day.known_h == pytest.approx(24.0) and heat_day.complete
    assert heat_day.burner_end is False
    assert heat_day.day_h == 24.0 and heat_day.note is None
    assert heat_day.ac_heat_el_kwh is None  # ohne power_kw unbekannt
    alive = influx.texts('count("actual_c")')[0]
    assert '"raw"."thermal"' in alive and "\"id\" = 'klima'" in alive
    assert "GROUP BY time(15m) fill(none)" in alive
    assert f"time >= {_ms(at(DAY, 0))}ms AND time < {_ms(at(TODAY, 0))}ms" in alive
    assert influx.texts("mean(") == []  # ohne power_kw liest er die Klimaanlage nicht


async def test_dead_source_is_unknown_not_burning(engine: Engine, influx: Influx) -> None:
    # status: an ab 06:00, danach kein Wert; actual_c fehlt 07:00–08:00
    influx.burner = [(at(DAY, 6), True)]
    influx.alive -= {at(DAY, 7) + n * SLOT for n in range(4)}
    days = _days(engine, influx, noon(TODAY))
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.known_h == pytest.approx(23.0)
    assert heat_day.burner_h == pytest.approx(17.0)  # 06–07 und 08–24
    assert heat_day.burner_end is True
    # 23 von 24 h sind 95,8 % und reichen gerade noch (COMPLETE_SHARE 0,95)
    assert heat_day.complete


async def test_two_dead_hours_make_the_day_incomplete(engine: Engine, influx: Influx) -> None:
    influx.burner = [(at(DAY, 6), True)]
    influx.alive -= {at(DAY, 7) + n * SLOT for n in range(8)}
    heat_day = await _days(engine, influx, noon(TODAY)).compute(DAY, seed=False)
    assert heat_day.known_h == pytest.approx(22.0) and heat_day.burner_h == pytest.approx(16.0)
    assert not heat_day.complete


async def test_source_dead_all_day_is_unknown(engine: Engine) -> None:
    influx = Influx()
    heat_day = await _days(engine, influx, noon(TODAY)).compute(DAY, seed=True)
    assert heat_day.burner_h is None and heat_day.known_h == 0.0
    assert heat_day.note == "Keine Messwerte der Quelle"
    assert heat_day.burner_end is True  # der Zustand selbst ist bekannt


async def test_last_status_value_before_day_beats_seed(engine: Engine, influx: Influx) -> None:
    influx.burner = [(at(DAY, 0) - timedelta(days=30), True), (at(DAY, 12), False)]
    heat_day = await _days(engine, influx, noon(TODAY)).compute(DAY, seed=False)
    assert heat_day.burner_h == pytest.approx(12.0)
    last = influx.texts("last(")[0]
    assert f"time < {_ms(at(DAY, 0))}ms" in last


async def test_seed_from_stored_previous_day(engine: Engine, influx: Influx) -> None:
    # kein status-Wert in 90 Tagen; Vortag direkt in heating_day mit burner_end=False
    _store(engine, DAY - timedelta(days=1), burner_end=False)
    days = _days(engine, influx, noon(TODAY))
    assert await days.fill_missing(TODAY, 1) == 1
    assert days.rows(DAY, DAY)[0].burner_h == 0.0
    _store(engine, DAY - timedelta(days=3), burner_end=True)
    influx.live(local_day_slots(DAY - timedelta(days=2)))
    days2 = _days(engine, influx, noon(DAY))
    # ein anderer Vortag mit burner_end=True: der Brenner läuft durch
    assert await days2.fill_missing(DAY - timedelta(days=1), 1) == 1
    assert days2.rows(DAY - timedelta(days=2), DAY - timedelta(days=2))[0].burner_h == 24.0


async def test_no_seed_and_no_change_is_unknown(engine: Engine, influx: Influx) -> None:
    heat_day = await _days(engine, influx, noon(TODAY)).compute(DAY, seed=None)
    assert heat_day.burner_h is None and heat_day.note == "Brennerstatus fehlt"
    assert heat_day.known_h == 0.0 and heat_day.burner_end is None and not heat_day.complete


async def test_first_change_without_seed_counts_from_there(engine: Engine, influx: Influx) -> None:
    influx.burner = [(at(DAY, 12), True)]
    heat_day = await _days(engine, influx, noon(TODAY)).compute(DAY, seed=None)
    assert heat_day.known_h == pytest.approx(12.0) and heat_day.burner_h == pytest.approx(12.0)
    assert heat_day.burner_end is True


@pytest.mark.parametrize(("temp_c", "hdd"), [(5.0, 10.0), (20.0, 0.0)])
async def test_heating_degree_days_base_15(
    engine: Engine, influx: Influx, temp_c: float, hdd: float
) -> None:
    # 96 Viertelstunden mit 5 °C → 10 K·d; mit 20 °C → 0
    weather = _weather(engine, local_day_slots(DAY), temp_c)
    heat_day = await _days(engine, influx, noon(TODAY), weather=weather).compute(DAY, seed=False)
    assert heat_day.hdd_kd == pytest.approx(hdd)
    assert heat_day.outdoor_mean_c == pytest.approx(temp_c)


async def test_heating_degree_days_need_90_percent_of_slots(engine: Engine, influx: Influx) -> None:
    # nur 80 von 96 Viertelstunden im Wetterarchiv
    weather = _weather(engine, local_day_slots(DAY)[:80])
    heat_day = await _days(engine, influx, noon(TODAY), weather=weather).compute(DAY, seed=False)
    assert heat_day.hdd_kd is None and heat_day.outdoor_mean_c is None


async def test_heating_degree_days_with_90_percent_of_slots(engine: Engine, influx: Influx) -> None:
    weather = _weather(engine, local_day_slots(DAY)[:87])  # 87 ≥ 0,9 × 96
    heat_day = await _days(engine, influx, noon(TODAY), weather=weather).compute(DAY, seed=False)
    assert heat_day.hdd_kd == pytest.approx(87 * 10 * 0.25 / 24)


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 10, 25), 25.0), (date(2026, 3, 29), 23.0)])
async def test_dst_day_length(engine: Engine, day: date, hours: float) -> None:
    influx = Influx()
    influx.live(local_day_slots(day))
    weather = _weather(engine, local_day_slots(day))
    days = _days(engine, influx, noon(day + timedelta(days=1)), weather=weather)
    heat_day = await days.compute(day, seed=False)
    assert heat_day.day_h == hours and heat_day.complete
    assert heat_day.known_h == pytest.approx(hours)
    assert heat_day.hdd_kd == pytest.approx(10.0 * hours / 24)


def _thermal_day(influx: Influx) -> None:
    """Klimaanlage: Viertelstunden 0–3 heizen à 2000 W, 4–7 kühlen à 1500 W, danach aus."""
    slots = local_day_slots(DAY)
    influx.answer(
        'mean("actual_c")',
        [
            Series(
                "thermal",
                {},
                ["time", "actual_c", "setpoint_c", "offset_k"],
                [[_ms(s), 21.0, 22.0, 0.0] for s in slots],
            )
        ],
    )
    power = {slots[n]: 2000.0 for n in range(4)} | {slots[n]: 1500.0 for n in range(4, 8)}
    influx.answer(
        "'thermal' AND",
        [Series("power", {}, ["time", "p_w"], [[_ms(t), v] for t, v in power.items()])],
    )

    def seed(bit: str, value: bool) -> Series:
        return Series(
            "status",
            {"id": f"thermal.{DEVICE}.{bit}"},
            ["time", "last"],
            [[_ms(slots[0] - SLOT), value]],
        )

    def change(bit: str, ts: datetime, value: bool) -> Series:
        return Series(
            "status", {"id": f"thermal.{DEVICE}.{bit}"}, ["time", "value_bool"], [[_ms(ts), value]]
        )

    influx.answer(
        f'last("value_bool") FROM "status" WHERE ({AC_BITS}',
        [
            seed("running", True),
            seed("heating", True),
            seed("venting", False),
            seed("cooling", False),
        ],
    )
    influx.answer(
        f"WHERE ({AC_BITS}",  # die Änderungen; ``last(`` hat die Antwort davor
        [
            change("running", slots[8], False),
            change("heating", slots[4], False),
            change("cooling", slots[4], True),
        ],
    )


async def test_ac_heating_energy_counts_only_heating_slots(engine: Engine, influx: Influx) -> None:
    # 4 Slots heizen à 2000 W, 4 Slots kühlen à 1500 W → 2,0 kWh
    _thermal_day(influx)
    weather = _weather(engine, local_day_slots(DAY))
    days = _days(engine, influx, noon(TODAY), weather=weather, ac_power_known=True)
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.ac_heat_el_kwh == pytest.approx(2.0)
    assert heat_day.note is None


async def test_ac_energy_without_clear_mode_is_a_note(engine: Engine, influx: Influx) -> None:
    slots = local_day_slots(DAY)
    influx.answer(
        'mean("actual_c")',
        [Series("thermal", {}, ["time", "actual_c"], [[_ms(s), 21.0] for s in slots])],
    )
    influx.answer(
        "'thermal' AND",
        [Series("power", {}, ["time", "p_w"], [[_ms(s), 400.0] for s in slots[:4]])],
    )  # Betriebsbits unbekannt: 4 × 400 W × ¼ h = 0,4 kWh ohne klare Betriebsart
    weather = _weather(engine, slots)
    days = _days(engine, influx, noon(TODAY), weather=weather, ac_power_known=True)
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.ac_heat_el_kwh == 0.0
    assert heat_day.note == "Klimaanlage ohne klare Betriebsart: 0,4 kWh"


async def test_fill_is_ascending_and_skips_stored_and_today(engine: Engine, influx: Influx) -> None:
    today = DAY + timedelta(days=3)
    for n in range(4):
        influx.live(local_day_slots(DAY + timedelta(days=n)))
    _store(engine, DAY - timedelta(days=1), burner_end=False)
    _store(engine, DAY + timedelta(days=1), burner_end=False, burner_h=7.0)
    # am Abend von DAY geht der Brenner an und bleibt an; ein Wert vor DAY+2 fehlt in status
    # nicht, die Kette gilt aber auch über den gespeicherten Tag hinweg
    influx.burner = [(at(DAY, 18), True)]
    days = _days(engine, influx, noon(today))
    assert await days.fill_missing(today, 3) == 2
    stored = days.rows(DAY - timedelta(days=1), today)
    assert [d.day for d in stored] == [DAY + timedelta(days=n) for n in range(-1, 3)]
    assert stored[1].burner_h == pytest.approx(6.0) and stored[1].burner_end is True
    assert stored[2].burner_h == 7.0  # gespeichert, nicht neu gerechnet
    assert stored[3].burner_h == pytest.approx(24.0)
    windows = [
        int(_WINDOW.search(q).group(1))  # type: ignore[union-attr]
        for q in influx.texts('count("actual_c")')
    ]
    assert windows == [_ms(at(DAY, 0)), _ms(at(DAY + timedelta(days=2), 0))]
    assert await days.fill_missing(today, 3) == 0  # nichts mehr zu tun


async def test_fill_chains_end_state_of_computed_day(engine: Engine) -> None:
    influx = Influx()
    influx.live(local_day_slots(DAY))
    influx.live(local_day_slots(TODAY))
    _store(engine, DAY - timedelta(days=1), burner_end=True)
    days = _days(engine, influx, noon(TODAY + timedelta(days=1)))
    # ohne jeden Wert in status: DAY erbt „an“ vom gespeicherten Vortag, TODAY von DAY
    assert await days.fill_missing(TODAY + timedelta(days=1), 2) == 2
    assert [d.burner_h for d in days.rows(DAY, TODAY)] == [24.0, 24.0]


async def test_day_without_burner_value_is_stored_with_reason(
    engine: Engine, influx: Influx
) -> None:
    days = _days(engine, influx, noon(TODAY))
    assert await days.fill_missing(TODAY, 1) == 1
    (stored,) = days.rows(DAY, DAY)
    assert (
        stored.burner_h is None and stored.known_h == 0.0 and stored.note == "Brennerstatus fehlt"
    )
    assert await days.fill_missing(TODAY, 1) == 0  # wird nicht erneut gerechnet


async def test_read_error_writes_nothing(engine: Engine, influx: Influx) -> None:
    # Reader wirft InfluxQueryError → 0 gespeichert, Tag fehlt weiter
    influx.broken = True
    days = _days(engine, influx, noon(TODAY))
    with pytest.raises(HeatingDataError):
        await days.compute(DAY, seed=False)
    assert await days.fill_missing(TODAY, 3) == 0
    assert days.rows(DAY - timedelta(days=3), TODAY) == []
    influx.broken = False
    assert await days.fill_missing(TODAY, 3) == 3  # der nächste Lauf holt sie nach


async def test_error_stops_the_run_but_keeps_stored_days(engine: Engine, influx: Influx) -> None:
    class Flaky(WeatherArchive):
        def temperatures(self, start: datetime, end: datetime) -> dict[datetime, float]:
            if start >= at(DAY, 0):
                raise OperationalError("select", {}, Exception("gesperrt"))
            return {}

    days = _days(engine, influx, noon(TODAY), weather=Flaky(engine))
    assert await days.fill_missing(TODAY, 3) == 2  # DAY scheitert, der Lauf endet dort
    assert [d.day for d in days.rows(DAY - timedelta(days=3), TODAY)] == [
        DAY - timedelta(days=2),
        DAY - timedelta(days=1),
    ]


async def test_refresh_today_counts_until_now(engine: Engine) -> None:
    influx = Influx()
    influx.live(local_day_slots(TODAY))
    _store(engine, DAY, burner_end=True, burner_h=5.0)
    clock = ManualClock(at(TODAY, 12, 5))
    days = _days(engine, influx, clock)
    assert days.today() is None
    assert days.period(DAY, TODAY) == days.rows(DAY, DAY)  # heute noch nicht gerechnet
    await days.refresh_today()
    today = days.today()
    assert today is not None and today.day == TODAY
    assert today.day_h == 24.0 and today.known_h == pytest.approx(12.0)  # nur ganze Slots
    assert today.burner_h == pytest.approx(12.0)  # Anfangswert vom gespeicherten Vortag
    assert not today.complete
    assert [d.day for d in days.period(DAY, TODAY)] == [DAY, TODAY]
    assert [d.day for d in days.period(DAY, DAY)] == [DAY]
    assert days.period(TODAY, TODAY + timedelta(days=5)) == [today]
    assert days.rows(TODAY, TODAY) == []  # heute wird nie gespeichert


async def test_refresh_error_keeps_previous_value(engine: Engine) -> None:
    influx = Influx()
    influx.live(local_day_slots(TODAY))
    days = _days(engine, influx, noon(TODAY))
    await days.refresh_today()
    before = days.today()
    influx.broken = True
    await days.refresh_today()
    assert days.today() is before and before is not None


async def test_period_skips_stale_cache_after_midnight(engine: Engine) -> None:
    influx = Influx()
    clock = noon(DAY)
    days = _days(engine, influx, clock)
    await days.refresh_today()
    clock.advance(timedelta(days=1))
    assert days.period(DAY, TODAY) == []


async def test_future_day_has_no_elapsed_slot(engine: Engine, influx: Influx) -> None:
    heat_day = await _days(engine, influx, noon(DAY)).compute(TODAY, seed=True)
    assert heat_day.known_h == 0.0 and heat_day.burner_h is None and heat_day.day_h == 24.0
    assert influx.queries == []


def test_rows_of_empty_range(engine: Engine) -> None:
    _store(engine, DAY, burner_end=False)
    days = _days(engine, Influx(), noon(TODAY))
    assert days.rows(TODAY, DAY) == []
