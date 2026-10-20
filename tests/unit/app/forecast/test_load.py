import asyncio
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from structlog.testing import capture_logs
from tests.app_helpers import StopScheduler, influx_q, ingest, record_then_stop
from tests.plan_helpers import until
from tests.unit.app.forecast.conftest import NOW

from bindaems.app.daytype import day_type
from bindaems.app.forecast.archive import actuals
from bindaems.app.forecast.load import (
    HALF_LIFE_DAYS,
    MIN_SAMPLES,
    WINDOW_DAYS,
    LoadForecast,
    LoadForecastService,
    LoadSample,
    build_profile,
    weighted_median,
)
from bindaems.shared.config import LoadHistoryConfig
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, local_day_slots

URL = "http://influx.lan:8086/query"
EMPTY = {"results": [{"statement_id": 0}]}
MIN = 60_000


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def at_local(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), LOCAL_TZ).astimezone(UTC)


def local(text: str) -> datetime:
    """``"2026-10-19 08:00"`` als Ortszeit."""
    return at_local(date.fromisoformat(text[:10]), text[11:])


def days(n: int) -> timedelta:
    return timedelta(days=n)


def sample(when: date | datetime, hhmm: str, watts: float) -> LoadSample:
    day = when.astimezone(LOCAL_TZ).date() if isinstance(when, datetime) else when
    return LoadSample(at_local(day, hhmm), watts, "ledger")


def _recent(kind: str, count: int) -> list[date]:
    """Die ``count`` jüngsten Tage des Tagestyps vor dem lokalen Datum von NOW, neueste zuerst."""
    found: list[date] = []
    day = NOW.astimezone(LOCAL_TZ).date()
    while len(found) < count:
        day -= days(1)
        if day_type(day) == kind:
            found.append(day)
    return found


def workdays(n: int) -> list[date]:
    return _recent("workday", n)


def saturdays(n: int) -> list[date]:
    return _recent("saturday", n)


def sundays(n: int) -> list[date]:
    return _recent("sunday", n)


def all_quarters() -> list[str]:
    return [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]


def test_constants() -> None:
    assert (WINDOW_DAYS, HALF_LIFE_DAYS, MIN_SAMPLES) == (56, 14, 3)


def test_weighted_median() -> None:
    assert weighted_median([(100, 1), (200, 1), (300, 1)]) == 200
    assert weighted_median([(100, 3), (200, 1), (300, 1)]) == 100


def test_weighted_median_takes_the_smallest_value_reaching_half() -> None:
    assert weighted_median([(300, 1), (100, 1)]) == 100  # genau die Hälfte erreicht
    assert weighted_median([(500, 2)]) == 500
    with pytest.raises(ValueError):
        weighted_median([])


def test_profile_separates_day_types() -> None:
    samples = [sample(day, "08:00", 400) for day in workdays(4)] + [
        sample(day, "08:00", 900) for day in saturdays(4)
    ]
    profile = build_profile(samples, NOW)
    assert profile.at(local("2026-10-19 08:00")) == 400  # Montag
    assert profile.at(local("2026-10-17 08:00")) == 900  # Samstag
    # Feiertag (Nationalfeiertag): keine Sonntagswerte, also alle Tagestypen. Die Werktage
    # (Alter 1 bis 4 Tage, Gewicht 3,54) wiegen mehr als die Samstage (6 bis 27 Tage, 1,90)
    assert profile.at(local("2026-10-26 08:00")) == 400


def test_recent_samples_weigh_more() -> None:
    samples = [
        sample(NOW - days(1), "08:00", 300),
        sample(NOW - days(15), "08:00", 900),
        sample(NOW - days(22), "08:00", 900),
    ]
    assert build_profile(samples, NOW).at(local("2026-10-19 08:00")) == 300  # ungewichtet: 900


def test_samples_older_than_56_days_are_ignored() -> None:
    profile = build_profile([sample(NOW - days(57), "08:00", 900)] * 3, NOW)
    assert profile.samples == 0 and profile.at(local("2026-10-19 08:00")) is None


def test_samples_from_the_future_are_ignored() -> None:
    profile = build_profile([sample(NOW + days(1), "08:00", 900)] * 3, NOW)
    assert profile.samples == 0 and profile.overall is None


def test_fallback_stages() -> None:
    # zwei Werktage um 08:00 (zu wenig), je zwei um 09:00 und 10:00 an anderen Tagen
    samples = [
        sample(workdays(2)[0], "08:00", 100),
        sample(workdays(2)[1], "08:00", 100),
        sample(saturdays(1)[0], "08:00", 700),  # 08:00 über alle Tagestypen: 3 Werte
        sample(workdays(1)[0], "12:00", 300),
    ]
    profile = build_profile(samples, NOW)
    assert profile.samples == 4
    assert profile.at(local("2026-10-19 08:00")) == 100  # Stufe 2: alle Tagestypen
    assert profile.at(local("2026-10-19 12:00")) is not None  # Stufe 3: Median aller Stichproben
    assert profile.at(local("2026-10-19 12:00")) == profile.overall


def test_profile_covers_100_slot_day() -> None:
    profile = build_profile(
        [sample(day, t, 500) for day in sundays(3) for t in all_quarters()], NOW
    )
    slots = local_day_slots(date(2026, 10, 25))
    assert len(slots) == 100 and all(profile.at(s) == 500 for s in slots)


def test_both_autumn_passes_share_one_profile_value() -> None:
    profile = build_profile([sample(day, "02:15", 321) for day in sundays(3)], NOW)
    second_pass = [s for s in local_day_slots(date(2026, 10, 25)) if s.minute == 15][2:4]
    assert [profile.at(s) for s in second_pass] == [321, 321]


def test_day_energy_on_dst_day_uses_local_slots() -> None:
    day = date(2026, 10, 25)
    power = dict.fromkeys(local_day_slots(day), 1000.0)
    forecast = LoadForecast(NOW, power, {"ledger": 0, "ha": 0})
    assert forecast.day_energy_kwh(day) == pytest.approx(25.0)  # 100 Slots × 0,25 h × 1 kW
    assert forecast.day_energy_kwh(date(2026, 10, 26)) is None


async def test_no_history_gives_no_forecast_and_status(load_service) -> None:
    assert await load_service.refresh() is None
    assert load_service.latest() is None
    status = load_service.component_status()
    assert (status.name, status.ok) == ("load-forecast", False)
    assert status.message == "Noch keine Daten für die Lastprognose"


async def test_incomplete_ledger_slot_falls_back_to_ha(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100}, covered_s=600)
    _mock_influx(respx_mock, _ha("sensor.hausverbrauch", "2026-10-12T06:00:00+00:00", 800))
    samples = await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:15:00+00:00")
    )
    assert samples == [LoadSample(at("2026-10-12T06:00:00+00:00"), 800.0, "ha")]


async def test_complete_ledger_slots_are_not_read_from_ha(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100, "pv>house": 25})  # 500 W
    route = _mock_influx(respx_mock, _ha("sensor.hausverbrauch", "2026-10-12T06:15:00Z", 9))
    samples = await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:30:00+00:00")
    )
    assert samples == [
        LoadSample(at("2026-10-12T06:00:00+00:00"), 500.0, "ledger"),
        LoadSample(at("2026-10-12T06:15:00+00:00"), 9.0, "ha"),
    ]
    assert len(_queries(route, seed=False)) == 1  # eine Abfrage für den ganzen Bereich


async def test_ha_query_asks_both_schemas_over_the_missing_range(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:15:00+00:00", {"grid>house": 100})
    route = _mock_influx(respx_mock)
    await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:45:00+00:00")
    )
    assert {c.request.url.params["db"] for c in route.calls} == {"homeassistant"}
    (query,) = _queries(route, seed=False)
    assert 'FROM "W","kW","sensor.hausverbrauch"' in query
    assert f"time >= {epoch_ms(at('2026-10-12T06:00:00+00:00'))}ms" in query
    assert f"time < {epoch_ms(at('2026-10-12T06:45:00+00:00'))}ms" in query
    assert 'GROUP BY time(60s),"domain","entity_id" fill(previous)' in query


async def test_ha_query_seeds_each_entity_with_its_last_value_before_the_range(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:15:00+00:00", {"grid>house": 100})
    route = _mock_influx(respx_mock)
    await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:45:00+00:00")
    )
    (query,) = _queries(route, seed=True)
    first = epoch_ms(at("2026-10-12T06:00:00+00:00"))
    assert 'FROM "W","kW","sensor.hausverbrauch"' in query
    assert f"time >= {first - 7 * 86_400_000}ms AND time < {first}ms" in query
    assert 'SELECT last("value") AS "w"' in query and 'GROUP BY "domain","entity_id"' in query


async def test_constant_subtract_entity_with_only_an_old_point_still_yields_samples(
    load_cfg, ledger, reader, archive, now_clock, respx_mock
) -> None:
    service = _service_with_subtract(
        load_cfg, ledger, reader, archive, now_clock, ["sensor.wallbox"]
    )
    first = at("2026-10-12T06:00:00+00:00")
    long_before = epoch_ms(first - timedelta(days=3))
    _mock_influx(
        respx_mock,
        _ha_series(_ha_rows("sensor.hausverbrauch", first, 800, "W", count=30)),
        seed=_ha_series(
            _ha_seed("sensor.wallbox", long_before, 0, "W"),
            _ha_seed("sensor.hausverbrauch", long_before, 5, "W"),
        ),
    )
    samples = await service.history(first, first + timedelta(minutes=30))
    assert [(s.slot_start, s.house_w) for s in samples] == [
        (first, 800.0),
        (first + timedelta(minutes=15), 800.0),
    ]


async def test_seed_fills_only_the_minutes_before_the_first_point_in_the_range(
    load_cfg, ledger, reader, archive, now_clock, respx_mock
) -> None:
    service = _service_with_subtract(
        load_cfg, ledger, reader, archive, now_clock, ["sensor.wallbox"]
    )
    first = at("2026-10-12T06:00:00+00:00")
    second = first + timedelta(minutes=15)
    _mock_influx(
        respx_mock,
        _ha_series(
            _ha_rows("sensor.hausverbrauch", first, 800, "W", count=30),
            _ha_rows("sensor.wallbox", second + timedelta(minutes=5), 300, "W", count=10),
        ),
        seed=_ha_series(_ha_seed("sensor.wallbox", epoch_ms(first - timedelta(days=1)), 100, "W")),
    )
    samples = await service.history(first, second + timedelta(minutes=15))
    # erster Slot: 15 Minuten mit dem Startwert 100; zweiter: 5 Minuten 100, 10 Minuten 300
    assert [s.house_w for s in samples] == [
        pytest.approx(700.0),
        pytest.approx(800 - (5 * 100 + 10 * 300) / 15),
    ]


async def test_units_other_than_w_and_kw_are_dropped_with_one_warning_per_entity(
    load_service_with_ha, ledger, respx_mock
) -> None:
    first = at("2026-10-12T06:00:00+00:00")
    _mock_influx(
        respx_mock,
        _ha_series(_ha_rows("sensor.hausverbrauch", first, 12345, "kWh", count=30)),
    )
    with capture_logs() as logs:
        samples = await load_service_with_ha.history(first, first + timedelta(minutes=30))
        await load_service_with_ha.history(first, first + timedelta(minutes=30))
    assert samples == []
    warnings = [e for e in logs if e["log_level"] == "warning" and "Einheit" in e["event"]]
    assert len(warnings) == 1 and warnings[0]["entity"] == "sensor.hausverbrauch"
    assert warnings[0]["units"] == ["kWh"]


async def test_rows_without_a_unit_keep_the_w_fallback(load_service_with_ha, respx_mock) -> None:
    first = at("2026-10-12T06:00:00+00:00")
    rows = _ha_rows("sensor.hausverbrauch", first, 640, "W", count=15)
    rows["values"] = [[t, w, None] for t, w, _ in rows["values"]]
    _mock_influx(respx_mock, _ha_series(rows))
    samples = await load_service_with_ha.history(first, first + timedelta(minutes=15))
    assert samples == [LoadSample(first, 640.0, "ha")]


async def test_ha_kw_unit_is_scaled_and_subtract_entities_are_removed(
    load_cfg, ledger, reader, archive, now_clock, respx_mock
) -> None:
    home = load_cfg.homeassistant.model_copy(
        update={
            "load_history": LoadHistoryConfig(
                entity="sensor.hausverbrauch", subtract=["sensor.wallbox", "sensor.boiler"]
            )
        }
    )
    service = LoadForecastService(
        ledger, load_cfg.model_copy(update={"homeassistant": home}), reader, archive, now_clock
    )
    slot = at("2026-10-12T06:00:00+00:00")
    _mock_influx(
        respx_mock,
        _ha_series(
            _ha_rows("sensor.hausverbrauch", slot, 2.5, "kW"),  # 2500 W
            _ha_rows("sensor.wallbox", slot, 1000, "W"),
            _ha_rows("sensor.boiler", slot, 0.5, "kW", name="kW"),  # Messung je Einheit
        ),
    )
    assert await service.history(slot, slot + timedelta(minutes=15)) == [
        LoadSample(slot, 1000.0, "ha")
    ]


async def test_missing_subtract_value_drops_the_slot_and_negative_loads_become_zero(
    load_cfg, ledger, reader, archive, now_clock, respx_mock
) -> None:
    home = load_cfg.homeassistant.model_copy(
        update={
            "load_history": LoadHistoryConfig(
                entity="sensor.hausverbrauch", subtract=["sensor.wallbox"]
            )
        }
    )
    service = LoadForecastService(
        ledger, load_cfg.model_copy(update={"homeassistant": home}), reader, archive, now_clock
    )
    first = at("2026-10-12T06:00:00+00:00")
    second, third = first + timedelta(minutes=15), first + timedelta(minutes=30)
    _mock_influx(
        respx_mock,
        _ha_series(
            _ha_rows("sensor.hausverbrauch", first, 800, "W", count=45),
            _ha_rows("sensor.wallbox", first, 100, "W", count=15),  # nur im ersten Slot
            _ha_rows("sensor.wallbox", third, 900, "W", count=15),
        ),
    )
    samples = await service.history(first, third + timedelta(minutes=15))
    assert [(s.slot_start, s.house_w) for s in samples] == [(first, 700.0), (third, 0.0)]
    assert second not in {s.slot_start for s in samples}


async def test_ha_failure_leaves_the_ledger_samples(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100})
    respx_mock.post(URL).respond(500)
    samples = await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:30:00+00:00")
    )
    assert samples == [LoadSample(at("2026-10-12T06:00:00+00:00"), 400.0, "ledger")]


async def test_without_load_history_only_the_ledger_counts(load_service, ledger) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100}, covered_s=100)
    ingest(ledger, "2026-10-12T06:15:00+00:00", {"grid>house": 100})
    samples = await load_service.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:30:00+00:00")
    )
    assert samples == [LoadSample(at("2026-10-12T06:15:00+00:00"), 400.0, "ledger")]


async def test_refresh_records_and_publishes(load_service, archive, sink, ledger) -> None:
    for day in workdays(2):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})  # 500 W
    assert load_service.profile() is None
    forecast = await load_service.refresh()
    profile = load_service.profile()  # das gemerkte Profil, z. B. für das Lastmodell
    assert profile is not None and profile.overall == 500.0
    assert forecast is not None and forecast.basis == {"ledger": 2, "ha": 0}
    assert archive.records("load", NOW, NOW + timedelta(days=1))
    assert {p.tags["kind"] for p in sink.points if p.measurement == "forecast"} == {"load"}
    points = [p for p in sink.points if p.measurement == "forecast"]
    assert {(p.tags["quantile"], p.rp) for p in points} == {("p50", "long")}
    assert all(p.fields == {"kw": 0.5} for p in points)
    # vom laufenden Slot bis zum Ende des übernächsten Tages (Ortszeit)
    assert min(forecast.p50_w) == NOW and max(forecast.p50_w) == at("2026-10-18T21:45:00+00:00")
    assert len(forecast.p50_w) == len(points) == 256
    assert load_service.latest() is forecast
    assert load_service.component_status().message == "Lastprognose von 08:00 Uhr"
    assert load_service.component_status().ok


async def test_profile_is_cached_until_the_next_local_day(
    load_service, ledger, now_clock, monkeypatch
) -> None:
    builds: list[datetime] = []
    original = LoadForecastService.history

    async def counting(self, start, end):
        builds.append(end)
        return await original(self, start, end)

    monkeypatch.setattr(LoadForecastService, "history", counting)
    for day in workdays(3):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})
    await load_service.refresh()
    now_clock.advance(timedelta(hours=1))
    await load_service.refresh()
    assert len(builds) == 1
    now_clock.advance(timedelta(hours=17))  # 02:00 Ortszeit des nächsten Tages
    await load_service.refresh()
    assert len(builds) == 2
    # kein Rückgriff auf den laufenden Slot: er ist noch nicht vollständig
    assert builds[0] == NOW


async def test_profile_is_rebuilt_while_it_has_no_data(load_service, ledger, now_clock) -> None:
    assert await load_service.refresh() is None
    for day in workdays(3):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})
    now_clock.advance(timedelta(hours=1))
    assert await load_service.refresh() is not None


async def test_failing_history_keeps_the_previous_forecast(
    load_service, ledger, monkeypatch
) -> None:
    for day in workdays(3):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})
    first = await load_service.refresh()

    def broken(*args, **kwargs):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(load_service, "_profile_day", None)  # erzwingt das Neubauen
    monkeypatch.setattr("bindaems.app.forecast.load.actuals", broken)
    assert await load_service.refresh() is first
    status = load_service.component_status()
    assert not status.ok and status.message == "Lastprognose fehlgeschlagen: Datenbank gesperrt"


async def test_run_outputs_at_minute_9_and_retries_sooner_after_error(
    cfg, ledger, archive, now_clock, monkeypatch
) -> None:
    now_clock.advance(timedelta(minutes=20))  # 08:20 Ortszeit; nächster Termin 09:09
    calls = 0
    real = actuals

    def flaky(source, start, end):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("kaputt")
        return real(source, start, end)

    monkeypatch.setattr("bindaems.app.forecast.load.actuals", flaky)
    sleeps: list[float] = []
    service = LoadForecastService(
        ledger, cfg, None, archive, now_clock, sleep=record_then_stop(sleeps, after=2)
    )
    with pytest.raises(StopScheduler):
        await service.run()
    assert sleeps == [600.0, 49 * 60.0] and calls == 2


def _ha_rows(
    entity: str,
    first: datetime,
    value: float,
    unit: str,
    *,
    count: int = 15,
    name: str | None = None,
) -> dict[str, object]:
    """Minutenmittel einer Entität. ``name`` ist die Messung je Einheit (``W``, ``kW``)."""
    domain, object_id = entity.split(".")
    start = epoch_ms(first)
    return {
        "name": name or entity,
        "tags": {"domain": domain, "entity_id": object_id},
        "columns": ["time", "w", "u"],
        "values": [[start + i * MIN, value, None if name else unit] for i in range(count)],
    }


def _ha_seed(entity: str, ms: int, value: float, unit: str) -> dict[str, object]:
    """Letzter Wert einer Entität vor dem Bereich (Abfrage ``last``)."""
    domain, object_id = entity.split(".")
    return {
        "name": entity,
        "tags": {"domain": domain, "entity_id": object_id},
        "columns": ["time", "w", "u"],
        "values": [[ms, value, unit]],
    }


def _mock_influx(respx_mock, means: dict | None = None, seed: dict | None = None):
    """Beantwortet die Minutenmittel (``GROUP BY time``) und die Abfrage des Startwerts getrennt."""

    def answer(request: httpx.Request) -> httpx.Response:
        is_means = "GROUP BY time" in influx_q(request)
        return httpx.Response(200, json=(means if is_means else seed) or EMPTY)

    return respx_mock.post(URL).mock(side_effect=answer)


def _queries(route, *, seed: bool) -> list[str]:
    queries = [influx_q(call.request) for call in route.calls]
    return [q for q in queries if ("GROUP BY time" not in q) == seed]


def _service_with_subtract(load_cfg, ledger, reader, archive, clock, subtract):
    home = load_cfg.homeassistant.model_copy(
        update={"load_history": LoadHistoryConfig(entity="sensor.hausverbrauch", subtract=subtract)}
    )
    return LoadForecastService(
        ledger, load_cfg.model_copy(update={"homeassistant": home}), reader, archive, clock
    )


def _ha_series(*series: dict[str, object]) -> dict[str, object]:
    return {"results": [{"statement_id": 0, "series": list(series)}]}


def _ha(entity: str, first: str, value: float) -> dict[str, object]:
    return _ha_series(_ha_rows(entity, at(first), value, "W"))


async def test_ha_samples_count_in_the_basis(
    load_service_with_ha, ledger, respx_mock, monkeypatch
) -> None:
    ingest(ledger, at_local(workdays(1)[0], "10:00"), {"grid>house": 125})
    day = workdays(2)[1]
    first = at_local(day, "10:00")
    _mock_influx(
        respx_mock,
        _ha_series(
            _ha_rows("sensor.hausverbrauch", first, 700, "W", count=15),
        ),
    )
    forecast = await load_service_with_ha.refresh()
    assert forecast is not None and forecast.basis == {"ledger": 1, "ha": 1}


# --- Schattenbetrieb von Profil und Modell (Plan 5a, Entscheidung 12) ----------------------------


class FakeModel:
    """Ersatz für den ``LoadModelService``: feste Modellwerte, ``missing`` Slots ohne Wert."""

    def __init__(
        self,
        *,
        active: bool = True,
        watts: float = 1000.0,
        missing: int = 0,
        bad: float | None = None,
        none: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._active, self.watts, self.missing, self.bad = active, watts, missing, bad
        self.none, self.error = none, error
        self.calls: list[tuple[datetime, int]] = []

    def active(self) -> bool:
        return self._active

    async def predict(self, issued_at, slots, profile):
        self.calls.append((issued_at, len(slots)))
        if self.error is not None:
            raise self.error
        if self.none:
            return None
        values = {slot: self.watts for slot in slots}
        for slot in slots[: self.missing]:
            if self.bad is None:
                del values[slot]
            else:
                values[slot] = self.bad  # NaN: wie fehlend
        return values


@pytest.fixture
def shadow_ledger(ledger):
    for day in workdays(2):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})  # Profil 500 W überall
    return ledger


def _archived(archive, kind: str) -> dict[datetime, float]:
    return {r.slot_start: r.p50_w for r in archive.records(kind, NOW, NOW + timedelta(days=4))}


async def test_without_model_output_is_profile(load_service, archive, shadow_ledger) -> None:
    forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "profile"
    assert set(forecast.p50_w.values()) == {500.0}
    assert _archived(archive, "load") == _archived(archive, "load_profile") == forecast.p50_w
    assert _archived(archive, "load_model") == {}


async def test_shadow_records_both_methods(load_service, archive, shadow_ledger, sink) -> None:
    model = FakeModel(active=False)
    load_service.attach_model(model)
    forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "profile"
    assert model.calls == [(NOW, 256)]
    assert set(_archived(archive, "load").values()) == {500.0}
    assert set(_archived(archive, "load_profile").values()) == {500.0}
    assert set(_archived(archive, "load_model").values()) == {1000.0}
    assert len(_archived(archive, "load_model")) == 256
    # InfluxDB bekommt weiter nur die ausgegebene Prognose
    points = [p for p in sink.points if p.measurement == "forecast"]
    assert {p.tags["kind"] for p in points} == {"load"} and len(points) == 256


async def test_active_model_is_output(load_service, archive, shadow_ledger, sink) -> None:
    load_service.attach_model(FakeModel(active=True))
    forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "model"
    assert set(forecast.p50_w.values()) == {1000.0}
    assert load_service.latest() is forecast
    assert set(_archived(archive, "load").values()) == {1000.0}
    assert set(_archived(archive, "load_profile").values()) == {500.0}
    assert set(_archived(archive, "load_model").values()) == {1000.0}
    points = [p for p in sink.points if p.measurement == "forecast"]
    assert all(p.fields == {"kw": 1.0} and p.tags["kind"] == "load" for p in points)


async def test_inactive_model_only_in_shadow(load_service, archive, shadow_ledger) -> None:
    model = FakeModel(active=False)
    load_service.attach_model(model)
    forecast = await load_service.refresh()
    assert forecast is not None and set(forecast.p50_w.values()) == {500.0}
    assert forecast.method == "profile"
    assert set(_archived(archive, "load_model").values()) == {1000.0}


async def test_inactive_model_without_archive_is_not_asked(
    cfg, ledger, now_clock, shadow_ledger
) -> None:
    service = LoadForecastService(ledger, cfg, None, None, now_clock)
    model = FakeModel(active=False)
    service.attach_model(model)
    forecast = await service.refresh()
    assert forecast is not None and forecast.method == "profile" and model.calls == []


@pytest.mark.parametrize("missing", [12, 25])  # 4,7 % und 9,8 %; bis 10 % wird aufgefüllt
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
async def test_gaps_are_filled_with_profile(
    load_service, archive, shadow_ledger, missing, bad
) -> None:
    load_service.attach_model(FakeModel(missing=missing, bad=bad))
    forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "model"
    values = list(forecast.p50_w.values())
    assert values.count(500.0) == missing and values.count(1000.0) == 256 - missing
    assert len(forecast.p50_w) == 256
    # das Modell wird nur dort archiviert, wo es wirklich einen Wert hat
    assert len(_archived(archive, "load_model")) == 256 - missing
    assert set(_archived(archive, "load_model").values()) == {1000.0}
    assert len(_archived(archive, "load")) == 256


@pytest.mark.parametrize("bad", [None, float("nan")])
async def test_many_gaps_fall_back_to_profile(load_service, archive, shadow_ledger, bad) -> None:
    load_service.attach_model(FakeModel(missing=29, bad=bad))  # 11,3 %
    with capture_logs() as logs:
        forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "profile"
    assert set(forecast.p50_w.values()) == {500.0}
    assert len(_archived(archive, "load_model")) == 256 - 29
    assert set(_archived(archive, "load").values()) == {500.0}
    assert [e["log_level"] for e in logs if "Lücken" in e["event"]] == ["warning"]


@pytest.mark.parametrize("model", [FakeModel(error=RuntimeError("kaputt")), FakeModel(none=True)])
async def test_broken_model_falls_back_to_profile(
    load_service, archive, shadow_ledger, model
) -> None:
    load_service.attach_model(model)
    with capture_logs() as logs:
        forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "profile"
    assert set(forecast.p50_w.values()) == {500.0}
    assert _archived(archive, "load_model") == {}
    assert set(_archived(archive, "load").values()) == {500.0}
    assert set(_archived(archive, "load_profile").values()) == {500.0}
    failures = [e for e in logs if "Vorhersage des Lastmodells" in e["event"]]
    # genau eine Warnung je Ausgabe, kein Traceback je Slot; ohne Version keine
    assert len(failures) == (1 if model.error else 0)
    assert all(e["log_level"] == "warning" and "exc_info" not in e for e in failures)


async def test_archive_failure_keeps_the_forecast_and_the_other_kinds(
    load_service, archive, shadow_ledger, monkeypatch
) -> None:
    load_service.attach_model(FakeModel(active=True))
    record = archive.record

    def failing(kind, issued_at, values):
        if kind == "load_profile":
            raise OSError("voll")
        return record(kind, issued_at, values)

    monkeypatch.setattr(archive, "record", failing)
    with capture_logs() as logs:
        forecast = await load_service.refresh()
    assert forecast is not None and forecast.method == "model"
    assert _archived(archive, "load_profile") == {}
    assert len(_archived(archive, "load")) == len(_archived(archive, "load_model")) == 256
    assert [e["kind"] for e in logs if "archiviert" in e["event"]] == ["load_profile"]


async def test_requested_refresh_does_not_wait_for_minute_9(
    cfg, ledger, archive, now_clock, shadow_ledger
) -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.Event().wait()  # die Minute 9 kommt in diesem Test nie

    service = LoadForecastService(ledger, cfg, None, archive, now_clock, sleep=sleep)
    model = FakeModel(active=True)
    service.attach_model(model)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: len(sleeps) == 1)  # wartet auf die Minute 9
        first = service.latest()
        assert first is not None and first.method == "model"
        model._active = False  # etwa „Lastmodell verwenden“ ausgeschaltet
        now_clock.advance(timedelta(minutes=2))
        service.request_refresh()
        await until(lambda: service.latest() is not first)
        second = service.latest()
        assert second is not None and second.method == "profile"
        assert second.issued_at == first.issued_at + timedelta(minutes=2)
        await until(lambda: len(sleeps) == 2)  # und danach wieder
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_refresh_requested_during_an_issue_runs_once_more(
    cfg, ledger, archive, now_clock, shadow_ledger
) -> None:
    calls: list[bool] = []
    service = LoadForecastService(
        ledger, cfg, None, archive, now_clock, sleep=record_then_stop([], after=2)
    )

    class Requesting(FakeModel):
        async def predict(self, issued_at, slots, profile):
            calls.append(self._active)
            if len(calls) == 1:
                service.request_refresh()  # die Anfrage kommt, während die Ausgabe rechnet
            return await super().predict(issued_at, slots, profile)

    service.attach_model(Requesting(active=True))
    with pytest.raises(StopScheduler):
        await service.run()
    assert len(calls) == 3  # erste Ausgabe, angeforderte Ausgabe, Ausgabe zur Minute 9
