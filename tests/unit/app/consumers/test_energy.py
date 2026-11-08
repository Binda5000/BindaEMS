from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from tests.app_helpers import T_APP, influx_q, k

from bindaems.app.consumers.energy import (
    EnergyCache,
    core_tags,
    day_start,
    ha_points,
    integrate_wh,
)
from bindaems.app.consumers.service import Consumer
from bindaems.app.consumers.values import HaRef
from bindaems.app.history.influx import RP_LONG, Series
from bindaems.shared.influx.lineprotocol import epoch_ms

URL = "http://influx.lan:8086/query"
MIDNIGHT = datetime(2026, 10, 8, 22, 0, tzinfo=UTC)  # 09.10.2026, 00:00 Ortszeit
M0 = epoch_ms(MIDNIGHT)
MIN = 60_000
HOUR = 3_600_000


def _results(*series: dict[str, object]) -> dict[str, object]:
    return {"results": [{"statement_id": 0, "series": list(series)}]}


def _series(name: str, tags: dict[str, str], points: list[tuple[int, float]]) -> dict[str, object]:
    return {
        "name": name,
        "tags": tags,
        "columns": ["time", "w"],
        "values": [list(p) for p in points],
    }


def _ha(ref: str, unit: str = "W") -> Consumer:
    return Consumer(9, "Küche", None, None, "#000000", "ha", ref, unit, 0)


def test_day_start_is_midnight_in_vienna() -> None:
    assert day_start(T_APP) == MIDNIGHT
    assert day_start(datetime(2026, 10, 8, 22, 0, tzinfo=UTC)) == MIDNIGHT


def test_integration_holds_each_value_until_the_next_but_not_across_gaps() -> None:
    points = [(0, 1000.0), (MIN, 2000.0), (10 * MIN, 500.0)]
    # Minutenmittel: jedes gilt eine Minute; die Lücke 2–10 min zählt nicht
    assert integrate_wh(points, 11 * MIN, MIN) == (1000 + 2000 + 500) / 60
    # HA: ein Wert gilt bis zum nächsten
    assert integrate_wh(points, 11 * MIN, None) == (1000 + 2000 * 9 + 500) / 60
    assert integrate_wh([], HOUR, None) == 0.0


async def test_core_energy_with_house_in_one_query(respx_mock, reader, clock) -> None:
    og, _ = k(1, 0.0)  # load.v1.power_w
    route = respx_mock.post(URL).respond(
        json=_results(
            _series("power", {"source": "load", "id": "v1"}, [(M0, 600.0), (M0 + MIN, 600.0)]),
            _series("power", {"source": "derived", "id": "house_load"}, [(M0, 1200.0)]),
        )
    )
    cache = EnergyCache(reader, clock, None)
    await cache.refresh([og])
    assert cache.energy_kwh(og) == 0.02 and cache.note(og) is None  # 2 min × 600 W
    assert cache.house_kwh() == 0.02  # 1 min × 1200 W
    assert cache.since == MIDNIGHT
    query = influx_q(route.calls.last.request)
    assert query == (
        'SELECT mean("p_w") AS "w" FROM "raw"."power" WHERE "phase"=\'total\' AND '  # noqa: S608
        "((\"source\"='load' AND \"id\"='v1') OR (\"source\"='derived' AND \"id\"='house_load') "
        "OR (\"source\"='derived' AND \"id\"='pv_total')) "
        f"AND time >= {M0}ms AND time < {epoch_ms(T_APP)}ms "
        'GROUP BY time(60s),"source","id" fill(none)'
    )


async def test_core_energy_says_why_it_is_missing(respx_mock, reader, clock) -> None:
    quiet, _ = k(1, 0.0)
    other = Consumer(2, "Sonder", None, None, "#000000", "core", "vebus.ac_out.power_w", None, 0)
    respx_mock.post(URL).respond(json=_results())
    cache = EnergyCache(reader, clock, None)
    await cache.refresh([quiet, other])
    assert (cache.energy_kwh(quiet), cache.note(quiet)) == (None, "kein Verlauf seit Mitternacht")
    assert (cache.energy_kwh(other), cache.note(other)) == (None, "Signal wird nicht aufgezeichnet")
    assert cache.house_kwh() is None

    respx_mock.post(URL).respond(500, text="kaputt")
    await cache.refresh([quiet])
    assert (cache.energy_kwh(quiet), cache.note(quiet)) == (None, "Verlauf nicht lesbar")


async def test_ha_energy_starts_with_the_value_before_midnight(respx_mock, reader, clock) -> None:
    kitchen, stove = _ha("sensor.kueche"), _ha("sensor.herd", "kW")
    tags = {"domain": "sensor", "entity_id": "kueche"}
    stove_tags = {"domain": "sensor", "entity_id": "herd"}

    def influx(request: httpx.Request) -> httpx.Response:
        query = influx_q(request)
        if query.startswith('SELECT mean("p_w")'):
            return httpx.Response(200, json=_results())
        if query.startswith('SELECT last("value")'):
            # vor Mitternacht: Küche 100 W (Messung je Einheit)
            return httpx.Response(200, json=_results(_series("W", tags, [(M0 - HOUR, 100.0)])))
        # heute: Küche ab 05:00 Ortszeit 300 W (Messung je Entität), Herd 1 h lang 2 kW
        return httpx.Response(
            200,
            json=_results(
                _series("sensor.kueche", tags, [(M0 + 5 * HOUR, 300.0)]),
                _series("kW", stove_tags, [(M0 + 6 * HOUR, 2.0), (M0 + 7 * HOUR, 0.0)]),
            ),
        )

    route = respx_mock.post(URL).mock(side_effect=influx)
    cache = EnergyCache(reader, clock, "homeassistant")
    await cache.refresh([kitchen, stove])
    # 0–5 Uhr 100 W, 5–10 Uhr 300 W
    assert cache.energy_kwh(kitchen) == 2.0 and cache.note(kitchen) is None
    assert cache.energy_kwh(stove) == 2.0
    ha_queries = [
        influx_q(call.request)
        for call in route.calls
        if call.request.url.params["db"] == "homeassistant"
    ]
    assert ha_queries[0].startswith(
        'SELECT mean("value") AS "w" FROM "W","kW","sensor.kueche","sensor.herd" '  # noqa: S608
        f"WHERE time >= {M0}ms AND "
    )
    assert f"time >= {M0 - 7 * 24 * HOUR}ms AND time < {M0}ms" in ha_queries[1]


def held_ha(seed_at: int, seed_w: float):
    """HA schreibt nur bei Änderung und bei „unavailable“ keinen Wert: vor Mitternacht
    ``seed_w`` (um ``seed_at``), heute 1 kW ab 07:00 und 20 W ab 08:00, danach nichts mehr."""
    tags = {"domain": "sensor", "entity_id": "pv1"}

    def influx(request: httpx.Request) -> httpx.Response:
        query = influx_q(request)
        if query.startswith('SELECT mean("p_w")'):
            return httpx.Response(200, json=_results())
        if query.startswith('SELECT last("value")'):
            return httpx.Response(200, json=_results(_series("W", tags, [(seed_at, seed_w)])))
        points = [(M0 + 7 * HOUR, 1000.0), (M0 + 8 * HOUR, 20.0)]
        return httpx.Response(200, json=_results(_series("W", tags, points)))

    return influx


async def test_ha_hold_caps_a_held_value(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).mock(side_effect=held_ha(M0 - 15 * MIN, 40.0))
    pv = _ha("sensor.pv1")
    cache = EnergyCache(reader, clock, "homeassistant", ha_hold=timedelta(minutes=30))
    await cache.refresh([pv])
    # 40 W bis 00:15 (30 min nach 23:45), 1 kW 07:00–07:30, 20 W 08:00–08:30; dazwischen Lücke
    assert (cache.energy_kwh(pv), cache.note(pv)) == (0.52, None)


async def test_ha_hold_old_value_before_midnight_counts_nothing(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).mock(side_effect=held_ha(M0 - 6 * HOUR, 40.0))
    pv = _ha("sensor.pv1")
    cache = EnergyCache(reader, clock, "homeassistant", ha_hold=timedelta(minutes=30))
    await cache.refresh([pv])
    assert (cache.energy_kwh(pv), cache.note(pv)) == (0.51, None)  # kein unbekannter Tag


async def test_consumers_hold_ha_values_without_cap(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).mock(side_effect=held_ha(M0 - 15 * MIN, 40.0))
    kitchen = _ha("sensor.pv1")
    cache = EnergyCache(reader, clock, "homeassistant")
    await cache.refresh([kitchen])
    # 40 W 00:00–07:00, 1 kW 07:00–08:00, 20 W 08:00–10:00
    assert cache.energy_kwh(kitchen) == 1.32


async def test_ha_energy_without_database(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).respond(json=_results())
    kitchen = _ha("sensor.kueche")
    cache = EnergyCache(reader, clock, None)
    await cache.refresh([kitchen])
    assert (cache.energy_kwh(kitchen), cache.note(kitchen)) == (
        None,
        "Signal wird nicht aufgezeichnet",
    )


def _entity_series(rows: list[list[object]], columns: list[str]) -> Series:
    return Series("sensor.x", {"domain": "sensor", "entity_id": "x"}, columns, rows)


def test_ha_points_with_unit_column_drops_other_units_and_reports_them() -> None:
    series = [
        _entity_series(
            [[1000, 2.0, "kW"], [2000, 5.0, "kWh"], [3000, 7.0, None], [4000, 80.0, "%"]],
            ["time", "w", "u"],
        )
    ]
    ref = HaRef("sensor", "x", "W")
    rejected: set[str] = set()
    assert ha_points(series, ref, rejected) == [
        (1000, 2000.0),
        (3000, 7.0),
    ]  # None: Einheit der Ref
    assert rejected == {"kWh", "%"}


def test_ha_points_without_unit_column_is_unchanged() -> None:
    series = [_entity_series([[1000, 2.0], [2000, 5.0]], ["time", "w"])]
    rejected: set[str] = set()
    assert ha_points(series, HaRef("sensor", "x", "kW"), rejected) == [
        (1000, 2000.0),
        (2000, 5000.0),
    ]
    assert rejected == set()


async def test_window_energy_matches_refresh_for_today(respx_mock, reader, clock) -> None:
    og, _ = k(1, 0.0)
    other = Consumer(2, "Sonder", None, None, "#000000", "core", "vebus.ac_out.power_w", None, 0)
    respx_mock.post(URL).respond(
        json=_results(
            _series("power", {"source": "load", "id": "v1"}, [(M0, 600.0), (M0 + MIN, 600.0)]),
            _series("power", {"source": "derived", "id": "house_load"}, [(M0, 1200.0)]),
        )
    )
    cache = EnergyCache(reader, clock, None)
    energy, house, *_ = await cache.window_energy([og, other], MIDNIGHT, clock.now())
    # keine Zustandsänderung: der Cache ist noch leer
    assert cache.since is None and cache.house_kwh() is None and cache.energy_kwh(og) is None
    await cache.refresh([og, other])
    assert energy == {
        1: (cache.energy_kwh(og), cache.note(og)),
        2: (None, "Signal wird nicht aufgezeichnet"),
    }
    assert house == cache.house_kwh() == 0.02


async def test_window_energy_reads_the_given_window_and_leaves_the_cache_alone(
    respx_mock, reader, clock
) -> None:
    og, _ = k(1, 0.0)
    route = respx_mock.post(URL).respond(
        json=_results(_series("power", {"source": "load", "id": "v1"}, [(M0, 600.0)]))
    )
    cache = EnergyCache(reader, clock, None)
    await cache.refresh([og])
    before = (cache.since, cache.energy_kwh(og), cache.house_kwh())
    start, end = MIDNIGHT - timedelta(days=1), MIDNIGHT
    energy, house, *_ = await cache.window_energy([og], start, end)
    assert (cache.since, cache.energy_kwh(og), cache.house_kwh()) == before
    # der Punkt liegt bei end (Mock ignoriert das Fenster): er zählt nicht
    assert energy == {1: (0.0, None)}
    assert house is None
    query = influx_q(route.calls.last.request)
    assert f"time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms" in query


@dataclass(frozen=True)
class _Source:
    id: int
    source_kind: str
    power_ref: str
    power_unit: str | None


async def test_window_energy_accepts_any_power_source(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).respond(
        json=_results(
            _series("power", {"source": "pv", "id": "huawei"}, [(M0, 600.0), (M0 + MIN, 600.0)])
        )
    )
    cache = EnergyCache(reader, clock, None)
    source = _Source(7, "core", "pv.huawei.power_w", None)
    result = await cache.window_energy([source], MIDNIGHT, clock.now())
    assert result.per_source == {7: (0.02, None)}


def test_pv_dc_maps_to_dc_series() -> None:
    assert core_tags("pv_dc.power_w") == ("pv", "dc")
    assert core_tags("pv.huawei.power_w") == ("pv", "huawei")
    assert core_tags("battery.power_w") is None


async def test_pv_total_kwh_from_derived(respx_mock, reader, clock) -> None:
    respx_mock.post(URL).respond(
        json=_results(
            _series(
                "power",
                {"source": "derived", "id": "pv_total"},
                [(M0 + n * MIN, 1200.0) for n in range(60)],
            )
        )
    )
    cache = EnergyCache(reader, clock, None)
    await cache.refresh([])
    assert cache.pv_total_kwh() == 1.2


async def test_retention_long_reads_long(respx_mock, reader, clock) -> None:
    route = respx_mock.post(URL).respond(json=_results())
    source = _Source(1, "core", "pv.huawei.power_w", None)
    await EnergyCache(reader, clock, None, retention=RP_LONG).refresh([source])
    assert '"long"."power"' in influx_q(route.calls.last.request)
    await EnergyCache(reader, clock, None).refresh([source])
    assert '"raw"."power"' in influx_q(route.calls.last.request)
