from datetime import date, timedelta

from tests.app_helpers import influx_q

from bindaems.app.consumers.values import NOT_CONFIGURED
from bindaems.app.history.influx import InfluxReader
from bindaems.app.pv.curve import CURVE_STEP_S, day_curve
from bindaems.app.pv.service import PvSource
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import local_day_slots

URL = "http://influx.lan:8086/query"
DAY = date(2026, 10, 8)


def _core() -> PvSource:
    return PvSource(1, "Huawei", None, "#f59e0b", "core", "pv.huawei.power_w", None, 10.0, 0)


def _ha(unit: str = "kW") -> PvSource:
    return PvSource(2, "String 1", 1, "#f59e0b", "ha", "sensor.pv1_leistung", unit, None, 0)


def _results(*series: dict[str, object]) -> dict[str, object]:
    return {"results": [{"statement_id": 0, "series": list(series)}]}


def _series(name: str, tags: dict[str, str], rows: list[list[object]]) -> dict[str, object]:
    return {"name": name, "tags": tags, "columns": ["time", "w"], "values": rows}


async def test_core_curve_from_long_in_five_minutes(respx_mock, reader: InfluxReader) -> None:
    route = respx_mock.post(URL).respond(
        json=_results(
            _series("power", {"source": "pv", "id": "huawei"}, [[1000, 100.0], [301000, 250.5]])
        )
    )
    curves = await day_curve(reader, None, [_core()], DAY)
    query = influx_q(route.calls.last.request)
    assert '"long"."power"' in query and f"time({CURVE_STEP_S}s)" in query
    assert "time(300s)" in query and "\"source\"='pv'" in query and "\"id\"='huawei'" in query
    assert curves[1] == [(1000, 100.0), (301000, 250.5)]
    assert curves.notes == {}


async def test_ha_curve_scales_kw(respx_mock, reader: InfluxReader) -> None:
    respx_mock.post(URL).respond(
        json=_results(
            _series("kW", {"domain": "sensor", "entity_id": "pv1_leistung"}, [[1000, 1.8]])
        )
    )
    curves = await day_curve(reader, "homeassistant", [_ha()], DAY)
    assert curves[2] == [(1000, 1800.0)]


async def test_dst_day_has_25_hours(respx_mock, reader: InfluxReader) -> None:
    for day, hours in ((date(2026, 10, 25), 25), (date(2026, 3, 29), 23)):
        route = respx_mock.post(URL).respond(json=_results())
        await day_curve(reader, None, [_core()], day)
        query = influx_q(route.calls.last.request)
        start, end = (
            epoch_ms(local_day_slots(day)[0]),
            epoch_ms(local_day_slots(day + timedelta(1))[0]),
        )
        assert f"time >= {start}ms AND time < {end}ms" in query
        assert (end - start) == hours * 3_600_000


async def test_unavailable_is_skipped(respx_mock, reader: InfluxReader) -> None:
    respx_mock.post(URL).respond(
        json=_results(
            _series(
                "W",
                {"domain": "sensor", "entity_id": "pv1_leistung"},
                [[1000, 0.0], [301000, None], [601000, 40.0]],
            )
        )
    )
    curves = await day_curve(reader, "homeassistant", [_ha("W")], DAY)
    assert curves[2] == [(1000, 0.0), (601000, 40.0)]


async def test_notes_for_missing_database_and_unreadable(respx_mock, reader: InfluxReader) -> None:
    respx_mock.post(URL).respond(500, text="kaputt")
    curves = await day_curve(reader, None, [_core(), _ha()], DAY)
    assert curves[1] == [] and curves.notes[1] == "Verlauf nicht lesbar"
    assert curves[2] == [] and curves.notes[2] == NOT_CONFIGURED
