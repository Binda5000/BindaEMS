import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP, StopScheduler, login_as, record_then_stop
from tests.unit.app.forecast.conftest import FIXTURE

from bindaems.app.forecast.archive import RecordValue
from bindaems.app.forecast.pv_model import plane_power_w
from bindaems.app.forecast.service import PvForecast
from bindaems.app.forecast.snow import NO_CUT
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.settings import PvModelSettings
from bindaems.shared.timeutil import SLOT, local_day_slots


async def test_refresh_builds_forecast_and_writes_points(forecast_env) -> None:
    service, sink = forecast_env()
    forecast = await service.refresh()
    assert forecast.p50_w[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] == pytest.approx(
        plane_power_w(15.5, 12.2, 10.0, PvModelSettings())
    )
    assert {(p.measurement, p.rp, p.tags["kind"], p.tags["quantile"]) for p in sink.points} == {
        ("forecast", "long", "pv", quantile) for quantile in ("p25", "p50", "p75")
    }
    assert len(sink.points) == 3 * 384 and service.latest() is forecast
    assert service.component_status().ok


async def test_failed_plane_keeps_previous_forecast(forecast_env, respx_mock) -> None:
    service, _ = forecast_env()
    first = await service.refresh()
    respx_mock.routes["open_meteo"].respond(503)
    assert await service.refresh() is first and not service.component_status().ok
    assert "HTTP 503" in service.component_status().message


def test_day_energy_on_dst_day_uses_local_slots() -> None:
    day = date(2026, 10, 25)
    power = dict.fromkeys(local_day_slots(day), 1000.0)
    forecast = PvForecast(T_APP, "open_meteo", power, power, power, power, {}, power, T_APP)
    assert forecast.day_energy_kwh(day) == pytest.approx(25.0)  # 100 Slots × 0,25 h × 1 kW
    assert forecast.day_energy_kwh(date(2026, 10, 26)) is None
    assert forecast.power_at(local_day_slots(day)[3].replace(minute=7)) == 1000.0


async def test_run_refreshes_at_minute_7_and_retries_sooner_after_error(
    forecast_env, respx_mock, clock
) -> None:
    clock.advance(8 * 60)  # 10:08 Ortszeit; nächster Termin 11:07
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=record_then_stop(sleeps, after=4))
    body = respx_mock.routes["open_meteo"].return_value.text
    respx_mock.routes["open_meteo"].side_effect = [
        *[httpx.Response(503)] * 3,  # drei Versuche je Fläche, dazwischen je 30 s
        httpx.Response(200, text=body),
    ]
    with pytest.raises(StopScheduler):
        await service.run()
    assert sleeps == [30.0, 30.0, 600.0, 59 * 60.0] and service.latest() is not None


def test_forecast_endpoint(client, auth, filled_forecast) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/pv").json()
    assert body["source"] == "open_meteo"
    assert [d["date"] for d in body["days"]] == ["2026-10-09", "2026-10-10", "2026-10-11"]


async def test_refresh_records_forecast(forecast_env, archive, clock) -> None:
    service, _ = forecast_env()
    forecast = await service.refresh()
    rows = archive.records("pv", clock.now(), clock.now() + timedelta(days=1))
    assert rows and all(r.raw_w == r.p50_w for r in rows)
    assert {r.clearness for r in rows if r.raw_w > 0} - {None} <= {"clear", "mixed", "overcast"}
    assert {r.clearness for r in rows if r.raw_w > 0} - {None}  # die Aufnahme hat Tagesstunden
    assert {r.issued_at for r in rows} == {forecast.issued_at}


async def test_forecast_carries_raw_band_and_clearness(forecast_env) -> None:
    service, _ = forecast_env()
    forecast = await service.refresh()
    noon = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    assert forecast.raw_w == forecast.p50_w  # ohne Kalibrierung
    assert all(forecast.p25_w[s] <= forecast.p50_w[s] <= forecast.p75_w[s] for s in forecast.p50_w)
    assert set(forecast.clearness) == set(forecast.raw_w)
    assert forecast.clearness[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] is None  # Dämmerung
    assert forecast.clearness[noon] in {"clear", "mixed", "overcast"}


async def test_clearness_comes_from_first_plane(forecast_env, respx_mock, cfg) -> None:
    service, _ = forecast_env()
    data = json.loads(FIXTURE.read_text())
    del data["minutely_15"]["shortwave_radiation"]
    del data["minutely_15_units"]["shortwave_radiation"]
    respx_mock.routes["open_meteo"].respond(200, text=json.dumps(data))
    forecast = await service.refresh()
    assert forecast is not None and set(forecast.clearness.values()) == {None}
    assert forecast.p50_w  # die Prognose gilt trotzdem


async def test_archive_failure_keeps_forecast(forecast_env, archive, monkeypatch) -> None:
    service, sink = forecast_env()

    def broken(*args, **kwargs):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(archive, "record", broken)
    forecast = await service.refresh()
    assert forecast is not None and service.latest() is forecast
    assert service.component_status().ok and len(sink.points) == 3 * 384


async def test_cold_start_uses_neutral_calibration(forecast_env, cfg) -> None:
    service, _ = forecast_env()
    forecast = await service.refresh()
    slot, p50 = next((s, w) for s, w in forecast.p50_w.items() if w > 1000)
    assert p50 == forecast.raw_w[slot]
    assert forecast.p25_w[slot] == pytest.approx(0.7 * p50)
    assert forecast.p75_w[slot] == pytest.approx(min(1.2 * p50, cfg.pv.inverter_ac_max_w))
    assert service.component_status().message == "PV-Prognose von 10:00 Uhr"


async def test_refresh_publishes_all_three_quantiles(forecast_env) -> None:
    service, sink = forecast_env()
    forecast = await service.refresh()
    noon = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    kw = {p.tags["quantile"]: p.fields["kw"] for p in sink.points if p.ts == noon}
    assert kw == pytest.approx(
        {
            "p25": forecast.p25_w[noon] / 1000.0,
            "p50": forecast.p50_w[noon] / 1000.0,
            "p75": forecast.p75_w[noon] / 1000.0,
        }
    )
    assert kw["p25"] < kw["p50"] < kw["p75"]


# --- Kalibrierung aus Archiv und Abrechnung ---------------------------------------------------

NOON = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)  # 12:00 Ortszeit
SEED_DAYS = [datetime(2026, 9, 12, 10, 0, tzinfo=UTC) + timedelta(days=i) for i in range(24)]


class FakeLedger:
    """Abrechnungs-Slots mit gegebener PV-Leistung, nach Bereich gefiltert wie das Original."""

    def __init__(self, pv_w: dict[datetime, float]) -> None:
        self._pv_w = pv_w

    def slots(self, start: datetime, end: datetime) -> list[LedgerSlot]:
        return [
            LedgerSlot(slot, 900.0, {"pv>house": w / 4.0}, {}, 0.0, 0.0, None, "stream")
            for slot, w in sorted(self._pv_w.items())
            if start <= slot < end
        ]


def seed(archive, days, *, raw_w=5000.0, p50_w=4000.0, clearness="clear") -> None:
    for day in days:
        archive.record("pv", day - timedelta(hours=3), {day: RecordValue(p50_w, raw_w, clearness)})


def seeded_ledger(days) -> FakeLedger:  # Verhältnis zu roh 0,7 und 0,9: Median 0,8
    return FakeLedger({day: 3500.0 if i % 2 == 0 else 4500.0 for i, day in enumerate(days)})


async def test_recalibrate_scales_p50_and_sets_band(forecast_env, archive) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, seeded_ledger(SEED_DAYS))
    forecast = await service.refresh()
    assert forecast.clearness[NOON] == "clear"
    assert forecast.p50_w[NOON] == pytest.approx(0.8 * forecast.raw_w[NOON])
    # Vorlauf 2 h → h0_6: Ist/P50 = 0,875 und 1,125 (je zwölf Paare) → Viertel- und Dreiviertelwert
    assert forecast.p25_w[NOON] == pytest.approx(0.875 * forecast.p50_w[NOON])
    assert forecast.p75_w[NOON] == pytest.approx(1.125 * forecast.p50_w[NOON])
    # Vorlauf 26 h → h24 hat keine Paare: Standardband
    tomorrow = NOON + timedelta(days=1)
    assert forecast.p25_w[tomorrow] == pytest.approx(0.7 * forecast.p50_w[tomorrow])
    assert forecast.p75_w[tomorrow] == pytest.approx(1.2 * forecast.p50_w[tomorrow])
    assert forecast.p50_w[tomorrow] == pytest.approx(0.8 * forecast.raw_w[tomorrow])


async def test_refresh_archives_calibrated_p50_with_raw(forecast_env, archive) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, seeded_ledger(SEED_DAYS))
    await service.refresh()
    row = next(r for r in archive.records("pv", NOON, NOON + timedelta(minutes=15)))
    assert row.p50_w == pytest.approx(0.8 * row.raw_w) and row.clearness == "clear"


async def test_slots_before_issue_use_the_short_band(forecast_env, archive, clock) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, seeded_ledger(SEED_DAYS))
    clock.advance(4 * 3600)  # 14:00 Ortszeit; das Mittagsfenster der Prognose liegt davor
    forecast = await service.refresh()
    assert NOON < forecast.issued_at
    assert forecast.p25_w[NOON] == pytest.approx(0.875 * forecast.p50_w[NOON])


async def test_p50_is_clamped_to_the_inverter(forecast_env, archive, cfg) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    limit = 5000.0  # kleiner als die Roh-Spitze mal Faktor 1,5
    service._cfg = cfg.model_copy(
        update={"pv": cfg.pv.model_copy(update={"inverter_ac_max_w": limit})}
    )
    ledger = FakeLedger(dict.fromkeys(SEED_DAYS, 7500.0))  # Ist / roh = 1,5
    await asyncio.to_thread(service.recalibrate, ledger)
    forecast = await service.refresh()
    assert forecast.p50_w[NOON] == limit and forecast.p75_w[NOON] == limit
    assert max(forecast.p50_w.values()) <= limit and max(forecast.p75_w.values()) <= limit


async def test_recalibrate_uses_only_the_last_30_days(forecast_env, archive) -> None:
    old = [datetime(2026, 8, 1, 10, 0, tzinfo=UTC) + timedelta(days=i) for i in range(24)]
    seed(archive, old)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, FakeLedger(dict.fromkeys(old, 1000.0)))
    forecast = await service.refresh()
    assert forecast.p50_w[NOON] == forecast.raw_w[NOON]  # alles zu alt: neutral


async def test_recalibrate_ignores_slots_without_actuals(forecast_env, archive) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, FakeLedger({}))
    forecast = await service.refresh()
    assert forecast.p50_w[NOON] == forecast.raw_w[NOON]
    assert "kalibriert" not in service.component_status().message


async def test_failed_recalibration_keeps_the_previous_models(forecast_env, archive) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, seeded_ledger(SEED_DAYS))

    class Broken:
        def slots(self, start, end):
            raise RuntimeError("Datenbank gesperrt")

    with pytest.raises(RuntimeError):
        service.recalibrate(Broken())
    forecast = await service.refresh()
    assert forecast.p50_w[NOON] == pytest.approx(0.8 * forecast.raw_w[NOON])


async def test_status_names_the_calibration_samples(forecast_env, archive) -> None:
    seed(archive, SEED_DAYS)
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, seeded_ledger(SEED_DAYS))
    await service.refresh()
    assert service.component_status().message == (
        "PV-Prognose von 10:00 Uhr, kalibriert aus 24 Viertelstunden"
    )


async def test_forecast_refresh_fills_weather_archive(forecast_env, weather, clock) -> None:
    service, _ = forecast_env()
    assert service.temperatures() == {}  # vor dem ersten Abruf
    forecast = await service.refresh()
    issued = forecast.issued_at
    found = weather.temperatures(issued - timedelta(days=3), issued + timedelta(days=3))
    assert found and max(found) < issued + timedelta(hours=6)
    assert found[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] == 12.2  # Vortag und Tag der Ausgabe
    assert dict(service.temperatures())[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] == 12.2
    assert len(service.temperatures()) == 384  # die ganze letzte Antwort, nicht nur 6 h


async def test_weather_archive_error_keeps_the_forecast(forecast_env, weather, monkeypatch) -> None:
    def broken(slots, issued_at):
        raise OSError("Datenbank gesperrt")

    monkeypatch.setattr(weather, "record", broken)
    service, _ = forecast_env()
    with capture_logs() as logs:
        forecast = await service.refresh()
    assert service.latest() is forecast and service.component_status().ok
    assert "Temperatur konnte nicht archiviert werden" in [e["event"] for e in logs]


async def test_failed_refresh_keeps_previous_temperatures(forecast_env, respx_mock) -> None:
    service, _ = forecast_env()
    await service.refresh()
    before = dict(service.temperatures())
    respx_mock.routes["open_meteo"].respond(503)
    await service.refresh()
    assert dict(service.temperatures()) == before


# Kurze Ausfälle von Open-Meteo (5 × HTTP 503 an der Anlage am 18. und 19.10.2026)


def recording_sleep(sleeps: list[float]):
    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleep


def two_planes(cfg):
    west = cfg.pv.planes[0].model_copy(update={"name": "West", "azimuth_deg": 50.0})
    return cfg.model_copy(
        update={"pv": cfg.pv.model_copy(update={"planes": [cfg.pv.planes[0], west]})}
    )


def azimuths(route) -> list[str]:
    return [call.request.url.params["azimuth"] for call in route.calls]


async def test_plane_is_fetched_again_after_30_s_on_503(forecast_env, respx_mock) -> None:
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=recording_sleep(sleeps))
    route = respx_mock.routes["open_meteo"]
    route.side_effect = [httpx.Response(503), httpx.Response(200, text=FIXTURE.read_text())]
    forecast = await service.refresh()
    assert forecast is not None and service.component_status().ok
    assert sleeps == [30.0] and route.call_count == 2


async def test_network_error_is_fetched_again(forecast_env, respx_mock) -> None:
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=recording_sleep(sleeps))
    route = respx_mock.routes["open_meteo"]
    route.side_effect = [
        httpx.ConnectError("kein Netz"),
        httpx.Response(200, text=FIXTURE.read_text()),
    ]
    assert await service.refresh() is not None
    assert sleeps == [30.0] and route.call_count == 2


async def test_plane_gives_up_after_three_attempts(forecast_env, respx_mock) -> None:
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=recording_sleep(sleeps))
    route = respx_mock.routes["open_meteo"]
    route.respond(503)
    with capture_logs() as logs:
        assert await service.refresh() is None
    assert sleeps == [30.0, 30.0] and route.call_count == 3
    assert "HTTP 503" in service.component_status().message
    assert [e["event"] for e in logs].count("Open-Meteo nicht verfügbar, neuer Versuch") == 2


async def test_rate_limit_is_not_fetched_again_and_names_the_reason(
    forecast_env, respx_mock
) -> None:
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=recording_sleep(sleeps))
    route = respx_mock.routes["open_meteo"]
    reason = "Daily API request limit exceeded. Please try again tomorrow."
    route.respond(429, json={"error": True, "reason": reason})
    assert await service.refresh() is None
    assert sleeps == [] and route.call_count == 1
    message = service.component_status().message
    assert "HTTP 429" in message and reason in message


async def test_status_ignores_a_body_without_reason(forecast_env, respx_mock) -> None:
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(503, text="<html>Service Unavailable</html>")
    await service.refresh()
    assert service.component_status().message.endswith("Open-Meteo HTTP 503")


async def test_retry_fetches_only_the_missing_plane(forecast_env, respx_mock, cfg, clock) -> None:
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    body = FIXTURE.read_text()
    route.side_effect = [httpx.Response(200, text=body), *[httpx.Response(503)] * 3]
    assert await service.refresh() is None
    assert azimuths(route) == ["0.0", "50.0", "50.0", "50.0"]
    clock.advance(10 * 60)  # die Wiederholung der Schleife
    route.side_effect = None
    route.respond(200, text=body)
    forecast = await service.refresh()
    assert forecast is not None and service.component_status().ok
    assert azimuths(route)[4:] == ["50.0"]
    fresh, _ = forecast_env()  # beide Flächen auf einmal geholt
    fresh._cfg = service._cfg
    expected = await fresh.refresh()
    assert forecast.raw_w == expected.raw_w and forecast.clearness == expected.clearness
    assert service.temperatures() == fresh.temperatures()


async def test_kept_planes_expire_after_30_min(forecast_env, respx_mock, cfg, clock) -> None:
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    body = FIXTURE.read_text()
    route.side_effect = [httpx.Response(200, text=body), *[httpx.Response(503)] * 3]
    await service.refresh()
    clock.advance(31 * 60)
    route.side_effect = None
    route.respond(200, text=body)
    assert await service.refresh() is not None
    assert azimuths(route)[4:] == ["0.0", "50.0"]


async def test_kept_plane_expires_30_min_after_it_was_fetched(
    forecast_env, respx_mock, cfg, clock
) -> None:
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    route.side_effect = [httpx.Response(200, text=FIXTURE.read_text()), *[httpx.Response(503)] * 6]
    await service.refresh()
    clock.advance(20 * 60)
    await service.refresh()  # Süd kommt aus dem ersten Abruf, West scheitert wieder
    assert azimuths(route)[4:] == ["50.0"] * 3
    clock.advance(11 * 60)  # 31 min nach dem Abruf von Süd
    route.side_effect = None
    route.respond(200, text=FIXTURE.read_text())
    assert await service.refresh() is not None
    assert azimuths(route)[7:] == ["0.0", "50.0"]


async def test_successful_refresh_fetches_all_planes_next_time(
    forecast_env, respx_mock, cfg, clock
) -> None:
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    await service.refresh()
    clock.advance(5 * 60)
    await service.refresh()
    assert azimuths(route) == ["0.0", "50.0", "0.0", "50.0"]


# --- Schnee auf den Modulen -------------------------------------------------------------------

SNOW_AT = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)  # heute 02:00 Ortszeit
EARLY = timedelta(hours=-5)  # von 10:00 auf 05:00 Ortszeit
MORNING = datetime(2026, 10, 9, 8, 15, tzinfo=UTC)  # 10:15 Ortszeit, Prognose um 4 kW
NO_SNOW_DATA = "Keine Schneedaten von Open-Meteo, Schneeerkennung entfällt"


def snowy(snowfall_cm: Mapping[datetime, float] | None = None, *, depth_m: float = 0.0) -> str:
    """Die Aufnahme mit stündlichem Schneeblock; ``snowfall_cm`` je Beginn der Stunde (UTC)."""
    data = json.loads(FIXTURE.read_text())
    times = data["minutely_15"]["time"]
    stamps = list(range(times[0] + 3600, times[-1] + 1, 3600))
    falls = snowfall_cm or {}
    data["hourly_units"] = {"time": "unixtime", "snowfall": "cm", "snow_depth": "m"}
    data["hourly"] = {
        "time": stamps,
        "snowfall": [falls.get(datetime.fromtimestamp(t - 3600, UTC), 0.0) for t in stamps],
        "snow_depth": [depth_m] * len(stamps),
    }
    return json.dumps(data)


def ratio_ledger(forecast: PvForecast, ratios: Mapping[datetime, float]) -> FakeLedger:
    """Abrechnung mit Ist = Verhältnis · ungekürzte P50 je Slot."""
    return FakeLedger({slot: r * forecast.uncut_p50_w[slot] for slot, r in ratios.items()})


def morning(ratio: float, count: int, *, first: datetime = MORNING) -> dict[datetime, float]:
    return {first + i * SLOT: ratio for i in range(count)}


async def test_refresh_applies_snow_cut(forecast_env, respx_mock, clock, cfg, archive) -> None:
    clock.advance(EARLY)
    service, sink = forecast_env()
    route = respx_mock.routes["open_meteo"]
    route.respond(200, text=snowy({SNOW_AT: 2.0}))
    await service.refresh()
    forecast = service.latest()
    assert forecast is not None
    assert route.calls.last.request.url.params["hourly"] == "snowfall,snow_depth"
    uncut = forecast.uncut_p50_w[NOON]
    assert uncut > 1000 and forecast.raw_w[NOON] == uncut  # ohne Kalibrierung
    assert forecast.p50_w[NOON] == pytest.approx(0.1 * uncut)
    assert forecast.p25_w[NOON] == pytest.approx(0.1 * 0.7 * uncut)
    assert forecast.p75_w[NOON] == pytest.approx(0.1 * min(1.2 * uncut, cfg.pv.inverter_ac_max_w))
    assert (forecast.snow.state, forecast.snow.factor, forecast.snow.since) == (
        "vermutet",
        0.1,
        SNOW_AT,
    )
    assert forecast.revision == forecast.issued_at
    assert (
        forecast.p50_w[NOON - timedelta(days=1)] == forecast.uncut_p50_w[NOON - timedelta(days=1)]
    )
    kw = [p.fields["kw"] for p in sink.points if p.ts == NOON and p.tags["quantile"] == "p50"]
    assert kw == [pytest.approx(0.1 * uncut / 1000.0)]  # InfluxDB bekommt die gekürzte Prognose
    row = next(r for r in archive.records("pv", NOON, NOON + SLOT))
    assert row.p50_w == pytest.approx(0.1 * row.raw_w)  # das Archiv auch (Entscheidung 6)


async def test_only_the_first_plane_asks_for_snow(forecast_env, respx_mock, cfg, clock) -> None:
    clock.advance(EARLY)
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    route.respond(200, text=snowy({SNOW_AT: 2.0}))
    forecast = await service.refresh()
    assert [call.request.url.params.get("hourly") for call in route.calls] == [
        "snowfall,snow_depth",
        None,
    ]
    assert forecast is not None and forecast.snow.state == "vermutet"


async def test_kept_first_plane_keeps_its_snow_hours(forecast_env, respx_mock, cfg, clock) -> None:
    clock.advance(EARLY)
    service, _ = forecast_env()
    service._cfg = two_planes(cfg)
    route = respx_mock.routes["open_meteo"]
    route.side_effect = [
        httpx.Response(200, text=snowy({SNOW_AT: 2.0})),
        *[httpx.Response(503)] * 3,
    ]
    assert await service.refresh() is None
    clock.advance(10 * 60)
    route.side_effect = None
    route.respond(200, text=FIXTURE.read_text())  # West ohne Schneeblock
    forecast = await service.refresh()
    assert forecast is not None and forecast.snow.state == "vermutet"


async def test_refresh_without_snow_data_is_uncut(forecast_env, clock) -> None:
    clock.advance(EARLY)
    service, _ = forecast_env()
    with capture_logs() as logs:
        forecast = await service.refresh()
    assert forecast is not None and forecast.p50_w == forecast.uncut_p50_w
    assert forecast.snow == NO_CUT
    status = service.component_status()
    assert status.ok and "gekürzt" not in status.message and status.details == {}
    assert [e["log_level"] for e in logs if e["event"] == NO_SNOW_DATA] == ["info"]


async def test_restart_after_snowfall_keeps_cut(forecast_env, respx_mock, clock) -> None:
    clock.advance(3600)  # 11:00 Ortszeit; Schneefall um 02:00, drei Slots mit einem Drittel
    body = snowy({SNOW_AT: 2.0})
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=body)
    first = await service.refresh()
    assert first is not None and first.snow.state == "vermutet"
    ledger = ratio_ledger(first, morning(0.3, 3))
    assert await asyncio.to_thread(service.check_snow, ledger) is True
    await service.refresh()  # der nächste Abruf rechnet mit den gemerkten Beobachtungen
    before = service.latest()
    assert before is not None and before.snow.state == "gemessen"
    assert before.snow.factor == pytest.approx(0.3)
    restarted, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=body)
    await restarted.refresh()
    restarted.check_snow(ledger)
    after = restarted.latest()
    assert after is not None and after.snow == before.snow
    assert after.p50_w == pytest.approx(before.p50_w)


async def test_snow_check_bumps_revision_and_triggers_replan(
    forecast_env, respx_mock, clock, archive
) -> None:
    clock.advance(EARLY)
    service, sink = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    issued = (await service.refresh()).issued_at
    clock.advance(6 * 3600)  # 11:00 Ortszeit; 10:30 und 10:45 liefern 80 %
    ledger = ratio_ledger(service.latest(), morning(0.8, 2, first=MORNING + SLOT))
    sink.points.clear()
    assert await asyncio.to_thread(service.check_snow, ledger) is True
    forecast = service.latest()
    assert forecast.revision == clock.now() and forecast.revision > issued
    assert forecast.issued_at == issued
    assert forecast.snow == NO_CUT and forecast.p50_w == forecast.uncut_p50_w
    assert len(sink.points) == 3 * 384  # InfluxDB neu
    kw = [p.fields["kw"] for p in sink.points if p.ts == NOON and p.tags["quantile"] == "p50"]
    assert kw == [pytest.approx(forecast.uncut_p50_w[NOON] / 1000.0)]
    rows = archive.records("pv", issued, issued + timedelta(days=2))
    assert {r.issued_at for r in rows} == {issued}  # das Archiv bleibt
    row = next(r for r in archive.records("pv", NOON, NOON + SLOT))
    assert row.p50_w == pytest.approx(0.1 * row.raw_w)
    clock.advance(15 * 60)
    assert service.check_snow(ledger) is False
    assert service.latest().revision == forecast.revision


async def test_small_factor_change_keeps_the_revision(forecast_env, respx_mock, clock) -> None:
    clock.advance(3600)  # 11:00 Ortszeit
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    first = await service.refresh()
    assert service.check_snow(ratio_ledger(first, morning(0.3, 3))) is True
    measured = service.latest()
    clock.advance(15 * 60)
    ratios = {**morning(0.3, 2), MORNING + 2 * SLOT: 0.34, MORNING + 3 * SLOT: 0.34}
    assert service.check_snow(ratio_ledger(first, ratios)) is False  # 0,3 → 0,34
    assert service.latest() is measured
    clock.advance(15 * 60)
    ratios = {**morning(0.3, 2), MORNING + 2 * SLOT: 0.4, MORNING + 3 * SLOT: 0.4}
    assert service.check_snow(ratio_ledger(first, ratios)) is True  # 0,3 → 0,4 (gemerkt: 0,3)
    assert service.latest().snow.factor == pytest.approx(0.4)


async def test_snow_check_without_forecast_does_nothing(forecast_env, tmp_path, audit) -> None:
    path = tmp_path / "pv_snow_override.json"
    service, sink = forecast_env(override_path=path)
    assert service.check_snow(FakeLedger({})) is False
    service.set_snow_override("covered", actor="admin")
    assert service.latest() is None and not path.exists() and not sink.points
    assert audit.recent() == []


async def test_snow_detection_off(forecast_env, respx_mock, clock, settings_service) -> None:
    current = settings_service.current()
    model = current.settings.pv_model.model_copy(update={"snow_detection": False})
    settings_service.update(
        current.settings.model_copy(update={"pv_model": model}),
        base_version=current.version,
        actor="admin",
        source="ui",
    )
    clock.advance(EARLY)
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    forecast = await service.refresh()
    assert forecast is not None and forecast.snow == NO_CUT
    assert forecast.p50_w == forecast.uncut_p50_w
    service.set_snow_override("covered", actor="admin")  # die Knöpfe wirken nicht
    assert service.latest().snow == NO_CUT


async def test_override_file_roundtrip(forecast_env, clock, tmp_path, audit) -> None:
    path = tmp_path / "pv_snow_override.json"
    service, _ = forecast_env(override_path=path)
    await service.refresh()  # 10:00 Ortszeit, Open-Meteo ohne Schneeblock
    clock.advance(60)
    await asyncio.to_thread(service.set_snow_override, "covered", actor="admin")
    assert json.loads(path.read_text()) == {"mode": "covered", "at": clock.now().isoformat()}
    forecast = service.latest()
    assert forecast.snow.state == "vermutet" and forecast.revision == clock.now()
    assert forecast.p50_w[NOON] == pytest.approx(0.1 * forecast.uncut_p50_w[NOON])
    reloaded, _ = forecast_env(override_path=path)  # der Knopf übersteht einen Neustart
    assert (await reloaded.refresh()).snow == forecast.snow
    clock.advance(60)
    service.set_snow_override(None, actor="admin")
    assert not path.exists() and service.latest().snow == NO_CUT
    service.set_snow_override(None, actor="admin")  # ohne Datei: nichts zu löschen
    entries = audit.recent()
    assert [(e.actor, e.source, e.action) for e in entries] == [
        ("admin", "ui", "PV: Schneeerkennung wieder automatisch"),
        ("admin", "ui", "PV: Schneeerkennung wieder automatisch"),
        ("admin", "ui", "PV: Schnee auf den Modulen gemeldet"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "{kaputt",
        "[]",
        '{"mode": "covered"}',
        '{"mode": "weg", "at": "2026-10-09T08:00:00+00:00"}',
        '{"mode": "covered", "at": "gestern"}',
    ],
)
async def test_unreadable_override_file_means_automatic(forecast_env, tmp_path, text) -> None:
    path = tmp_path / "pv_snow_override.json"
    path.write_text(text, encoding="utf-8")
    with capture_logs() as logs:
        broken, _ = forecast_env(override_path=path)
    assert [e["log_level"] for e in logs if e["event"] == "Handschalter Schnee unlesbar"] == [
        "warning"
    ]
    assert (await broken.refresh()).snow == NO_CUT


async def test_override_free_lifts_the_cut(
    forecast_env, respx_mock, clock, tmp_path, audit
) -> None:
    clock.advance(EARLY)
    path = tmp_path / "pv_snow_override.json"
    service, _ = forecast_env(override_path=path)
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    assert (await service.refresh()).snow.state == "vermutet"
    clock.advance(60)
    service.set_snow_override("free", actor="anna", source="ha")
    assert json.loads(path.read_text())["mode"] == "free"
    forecast = service.latest()
    assert forecast.snow == NO_CUT and forecast.p50_w == forecast.uncut_p50_w
    entry = audit.recent()[0]
    assert (entry.actor, entry.source, entry.action) == (
        "anna",
        "ha",
        "PV: Module als frei gemeldet",
    )


async def test_status_names_the_cut(forecast_env, respx_mock, clock) -> None:
    clock.advance(EARLY)
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    await service.refresh()
    status = service.component_status()
    assert status.ok is True
    assert status.message == (
        "PV-Prognose von 05:00 Uhr, gekürzt auf 10 %: vermutlich Schnee auf den Modulen"
    )
    assert status.details == {
        "snow_state": "vermutet",
        "snow_factor_pct": 10,
        "snow_since": "2026-10-09T00:00:00+00:00",
    }
