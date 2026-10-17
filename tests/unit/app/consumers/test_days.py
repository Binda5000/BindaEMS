from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import insert, select

from bindaems.app.consumers.days import NO_DAY, ConsumerDays
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.service import ConsumerInput
from bindaems.app.db.schema import consumer_day
from bindaems.shared.influx.lineprotocol import epoch_ms

URL = "http://influx.lan:8086/query"
MIN = 60_000
YESTERDAY = date(2026, 10, 8)  # T_APP ist der 09.10.2026, 10:00 Ortszeit
MIDNIGHT = datetime(2026, 10, 7, 22, 0, tzinfo=UTC)  # 08.10.2026, 00:00 Ortszeit (UTC+2)


@pytest.fixture
def days(engine, reader, clock, service) -> ConsumerDays:
    return ConsumerDays(engine, EnergyCache(reader, clock, None), service, clock)


def _consumers(service, *names: str) -> list[int]:
    return [
        service.create(
            ConsumerInput(
                name=name, color="#336699", source_kind="core", power_ref=f"load.{name}.power_w"
            ),
            actor="chris",
            source="ui",
        ).id
        for name in names
    ]


def _answer(request: httpx.Request, minutes: dict[str, int]) -> httpx.Response:
    """Antwort mit ``minutes`` Minuten à 600 W je Verbraucher (``load.<id>``), ab Fensterbeginn."""
    query = request.url.params["q"]
    start = int(query.split("time >= ")[1].split("ms")[0])
    series = [
        {
            "name": "power",
            "tags": {"source": "load", "id": name},
            "columns": ["time", "w"],
            "values": [[start + n * MIN, 600.0] for n in range(count)],
        }
        for name, count in minutes.items()
    ]
    return httpx.Response(200, json={"results": [{"statement_id": 0, "series": series}]})


def _rows(engine) -> dict[tuple[str, int], tuple[float | None, str | None]]:
    with engine.connect() as conn:
        return {(r.day, r.consumer_id): (r.kwh, r.note) for r in conn.execute(select(consumer_day))}


async def test_store_writes_one_row_per_consumer(days, engine, service, respx_mock) -> None:
    first, second = _consumers(service, "a", "b")
    route = respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 120}))
    assert await days.store(YESTERDAY) == 2
    # a: 120 min × 600 W; b: kein Verlauf – die Zeile trägt den Grund
    assert _rows(engine) == {
        ("2026-10-08", first): (1.2, None),
        ("2026-10-08", second): (None, "kein Verlauf seit Mitternacht"),
    }
    # der Tag ist [lokale Mitternacht, nächste Mitternacht)
    query = route.calls.last.request.url.params["q"]
    assert f"time >= {epoch_ms(MIDNIGHT)}ms AND time < {epoch_ms(MIDNIGHT) + 24 * 60 * MIN}ms" in (
        query
    )
    # erneutes Speichern ersetzt die Zeilen des Tages
    respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 60, "b": 30}))
    assert await days.store(YESTERDAY) == 2
    assert _rows(engine) == {
        ("2026-10-08", first): (0.6, None),
        ("2026-10-08", second): (0.3, None),
    }


async def test_store_uses_the_real_length_of_a_dst_day(days, clock, service, respx_mock) -> None:
    _consumers(service, "a")
    route = respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 1}))
    clock.advance(timedelta(days=30))  # 08.11.2026
    await days.store(date(2026, 10, 25))  # Ende der Sommerzeit: 25 Stunden
    query = route.calls.last.request.url.params["q"]
    start = epoch_ms(datetime(2026, 10, 24, 22, 0, tzinfo=UTC))
    assert f"time >= {start}ms AND time < {start + 25 * 60 * MIN}ms" in query


async def test_store_never_stores_today_or_the_future(days, engine, service, respx_mock) -> None:
    _consumers(service, "a")
    route = respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 5}))
    assert await days.store(date(2026, 10, 9)) == 0  # heute (Ortszeit)
    assert await days.store(date(2026, 10, 10)) == 0
    assert route.call_count == 0 and _rows(engine) == {}


async def test_store_keeps_old_rows_when_influx_cannot_be_read(
    days, engine, service, respx_mock
) -> None:
    (first,) = _consumers(service, "a")
    respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 60}))
    await days.store(YESTERDAY)
    respx_mock.get(URL).respond(500, text="kaputt")
    assert (
        await days.store(YESTERDAY) == 0
    )  # nichts Neues, der Grund "nicht lesbar" wird nicht gespeichert
    assert _rows(engine) == {("2026-10-08", first): (0.6, None)}


async def test_fill_missing_skips_stored_days(days, engine, service, respx_mock) -> None:
    _consumers(service, "a", "b")
    route = respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 60, "b": 60}))
    await days.store(YESTERDAY)
    seen = route.call_count
    # die letzten 3 Tage bis gestern: 06., 07., 08.10.; der 08. ist gespeichert
    assert await days.fill_missing(date(2026, 10, 9), days=3) == 2
    assert route.call_count - seen == 2
    assert sorted({day for day, _ in _rows(engine)}) == ["2026-10-06", "2026-10-07", "2026-10-08"]
    seen = route.call_count
    assert await days.fill_missing(date(2026, 10, 9), days=3) == 0
    assert route.call_count == seen


async def test_fill_missing_fills_days_a_new_consumer_lacks(
    days, engine, service, respx_mock
) -> None:
    (first,) = _consumers(service, "a")
    respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 60, "c": 30}))
    await days.fill_missing(date(2026, 10, 9), days=2)
    (third,) = _consumers(service, "c")
    assert await days.fill_missing(date(2026, 10, 9), days=2) == 2
    assert _rows(engine)[("2026-10-07", third)] == (0.3, None)
    assert _rows(engine)[("2026-10-07", first)] == (0.6, None)


async def test_fill_missing_without_consumers_does_nothing(days, respx_mock) -> None:
    route = respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {}))
    assert await days.fill_missing(date(2026, 10, 9)) == 0
    assert route.call_count == 0


def _put(engine, day: str, consumer_id: int, kwh: float | None, note: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(consumer_day).values(day=day, consumer_id=consumer_id, kwh=kwh, note=note)
        )


def test_energy_sums_the_days(days, engine) -> None:
    _put(engine, "2026-10-07", 1, 1.5)
    _put(engine, "2026-10-08", 1, 2.25)
    _put(engine, "2026-10-09", 1, 9.0)  # außerhalb
    assert days.energy(date(2026, 10, 7), date(2026, 10, 8)) == {1: (3.75, None)}
    assert days.energy(date(2026, 10, 8), date(2026, 10, 7)) == {}


def test_sum_is_unknown_when_a_day_is_missing(days, engine) -> None:
    _put(engine, "2026-10-06", 1, None, "kein Verlauf seit Mitternacht")
    _put(engine, "2026-10-07", 1, 1.0)
    _put(engine, "2026-10-08", 1, None, "Verlauf nicht lesbar")
    _put(engine, "2026-10-06", 2, 1.0)
    _put(engine, "2026-10-08", 2, 1.0)  # der 07. fehlt ganz
    result = days.energy(date(2026, 10, 6), date(2026, 10, 8))
    # der Grund des ersten Tages ohne Wert
    assert result[1] == (None, "kein Verlauf seit Mitternacht")
    assert result[2] == (None, NO_DAY)


async def test_a_recreated_consumer_does_not_inherit_the_days_of_a_deleted_one(
    days, engine, service, respx_mock
) -> None:
    _consumers(service, "a")
    (old,) = _consumers(service, "b")
    respx_mock.get(URL).mock(side_effect=lambda r: _answer(r, {"a": 60, "b": 60, "c": 30}))
    await days.fill_missing(date(2026, 10, 9), days=2)
    service.delete(old, actor="chris", source="ui")
    assert not [key for key in _rows(engine) if key[1] == old]
    (new,) = _consumers(service, "c")
    assert new == old  # SQLite vergibt die Nummer des neuesten Verbrauchers wieder
    assert not [key for key in _rows(engine) if key[1] == new]
    # die Tage zählen für den neuen Verbraucher als fehlend und werden aus den Rohdaten gefüllt
    assert await days.fill_missing(date(2026, 10, 9), days=2) == 2
    assert _rows(engine)[("2026-10-08", new)] == (0.3, None)
