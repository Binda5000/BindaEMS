from datetime import UTC, date, datetime, time, timedelta

import pytest
from tests.app_helpers import StopScheduler, ingest, record_then_stop
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
    respx_mock.get(URL).respond(json=_ha("sensor.hausverbrauch", "2026-10-12T06:00:00+00:00", 800))
    samples = await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:15:00+00:00")
    )
    assert samples == [LoadSample(at("2026-10-12T06:00:00+00:00"), 800.0, "ha")]


async def test_complete_ledger_slots_are_not_read_from_ha(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100, "pv>house": 25})  # 500 W
    route = respx_mock.get(URL).respond(json=_ha("sensor.hausverbrauch", "2026-10-12T06:15:00Z", 9))
    samples = await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:30:00+00:00")
    )
    assert samples == [
        LoadSample(at("2026-10-12T06:00:00+00:00"), 500.0, "ledger"),
        LoadSample(at("2026-10-12T06:15:00+00:00"), 9.0, "ha"),
    ]
    assert route.call_count == 1  # eine Abfrage für den ganzen Bereich


async def test_ha_query_asks_both_schemas_over_the_missing_range(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:15:00+00:00", {"grid>house": 100})
    route = respx_mock.get(URL).respond(json={"results": [{"statement_id": 0}]})
    await load_service_with_ha.history(
        at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:45:00+00:00")
    )
    request = route.calls.last.request
    query = request.url.params["q"]
    assert request.url.params["db"] == "homeassistant"
    assert 'FROM "W","kW","sensor.hausverbrauch"' in query
    assert f"time >= {epoch_ms(at('2026-10-12T06:00:00+00:00'))}ms" in query
    assert f"time < {epoch_ms(at('2026-10-12T06:45:00+00:00'))}ms" in query
    assert 'GROUP BY time(60s),"domain","entity_id" fill(previous)' in query


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
    respx_mock.get(URL).respond(
        json=_ha_series(
            _ha_rows("sensor.hausverbrauch", slot, 2.5, "kW"),  # 2500 W
            _ha_rows("sensor.wallbox", slot, 1000, "W"),
            _ha_rows("sensor.boiler", slot, 0.5, "kW", name="kW"),  # Messung je Einheit
        )
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
    respx_mock.get(URL).respond(
        json=_ha_series(
            _ha_rows("sensor.hausverbrauch", first, 800, "W", count=45),
            _ha_rows("sensor.wallbox", first, 100, "W", count=15),  # nur im ersten Slot
            _ha_rows("sensor.wallbox", third, 900, "W", count=15),
        )
    )
    samples = await service.history(first, third + timedelta(minutes=15))
    assert [(s.slot_start, s.house_w) for s in samples] == [(first, 700.0), (third, 0.0)]
    assert second not in {s.slot_start for s in samples}


async def test_ha_failure_leaves_the_ledger_samples(
    load_service_with_ha, ledger, respx_mock
) -> None:
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100})
    respx_mock.get(URL).respond(500)
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
    forecast = await load_service.refresh()
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
    respx_mock.get(URL).respond(
        json=_ha_series(
            _ha_rows("sensor.hausverbrauch", first, 700, "W", count=15),
        )
    )
    forecast = await load_service_with_ha.refresh()
    assert forecast is not None and forecast.basis == {"ledger": 1, "ha": 1}
