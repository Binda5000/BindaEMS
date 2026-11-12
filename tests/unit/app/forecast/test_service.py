import asyncio
import errno
import json
import os
import threading
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP, StopScheduler, login_as, record_then_stop
from tests.helpers import FakeSink
from tests.unit.app.forecast.conftest import FIXTURE, snowy

from bindaems.app.forecast.archive import RecordValue
from bindaems.app.forecast.pv_model import plane_power_w
from bindaems.app.forecast.service import (
    OVERRIDE_NOT_SAVED,
    OverrideNotSaved,
    PvForecast,
    snow_info,
)
from bindaems.app.forecast.snow import NO_CUT, SnowCut
from bindaems.app.ledger.service import LedgerSlot
from bindaems.app.runtime import LoopSink
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


async def test_recalibrate_ignores_snow_days(forecast_env, archive) -> None:
    days = [datetime(2026, 9, 12, 10, 0, tzinfo=UTC) + timedelta(days=i) for i in range(20)]
    clear, snow = days[:8], days[8:]  # 8 klare Tage, 12 Schneetage
    pv_w: dict[datetime, float] = {}
    for day in days:
        ratio = 0.9 if day in clear else 0.02
        for i in range(6):  # 12:00 bis 13:15 Ortszeit
            slot = day + timedelta(minutes=15 * i)
            archive.record(
                "pv", day - timedelta(hours=3), {slot: RecordValue(4000.0, 5000.0, "clear")}
            )
            pv_w[slot] = ratio * 5000.0
    service, _ = forecast_env()
    with capture_logs() as logs:
        await asyncio.to_thread(service.recalibrate, FakeLedger(pv_w))
    models = service._models
    assert models[0].factor(12, "clear") == pytest.approx(0.9)  # sonst läge er bei 0,5
    assert models[0].samples == 8 * 6  # alle sechs Viertelstunden der klaren Tage
    low, high = models[1].band("h0_6", "clear")  # nur klare Tage: 0,9 · 5000 / 4000
    assert low == pytest.approx(1.125) and high == pytest.approx(1.125)
    assert next(e for e in logs if e["event"] == "PV-Prognose kalibriert")["excluded_days"] == len(
        snow
    )


async def test_bands_skip_cut_outputs_and_outlier_days(forecast_env, archive) -> None:
    # Ausgaben mit Vorlauf 30 h (h24) und 15 h (h6_24) ab 12:00 Ortszeit
    days = [datetime(2026, 9, 12, 10, 0, tzinfo=UTC) + timedelta(days=i) for i in range(23)]
    cut, outliers = days[:6], days[20:]  # dazwischen 14 ungekürzte Tage
    pv_w: dict[datetime, float] = {}
    for day in days:
        for i in range(8 if day in outliers else 2):
            slot = day + timedelta(minutes=15 * i)
            if day in cut:  # wegen Schneefall vorhergesagt auf 10 % gekürzt, die Module waren frei
                p50_w, pv_w[slot] = 500.0, 4500.0
            elif day in outliers:  # bedeckt: die Ausgabe bis 6 h zeigt ihn als Ausreißertag
                p50_w, pv_w[slot] = 4000.0, 100.0
                archive.record(
                    "pv", slot - timedelta(hours=3), {slot: RecordValue(4000.0, 5000.0, "clear")}
                )
                archive.record(
                    "pv", slot - timedelta(hours=15), {slot: RecordValue(4000.0, 5000.0, "clear")}
                )
            else:  # ungekürzt, auch mit dem kleinsten Faktor (P50 = 0,5 · roh)
                p50_w = 4000.0 if i == 0 else 2500.0
                pv_w[slot] = 1.125 * p50_w
            archive.record(
                "pv", slot - timedelta(hours=30), {slot: RecordValue(p50_w, 5000.0, "clear")}
            )
    service, _ = forecast_env()
    await asyncio.to_thread(service.recalibrate, FakeLedger(pv_w))
    bands = service._models[1]
    # nur die 28 Paare der ungekürzten Tage: Ist / P50 = 1,125 (gekürzt wären es 12 Paare mit 9,
    # an den Ausreißertagen 24 mit 0,025)
    assert bands.band("h24", "clear") == pytest.approx((1.125, 1.125))
    assert bands.band("h6_24", "clear") == (0.7, 1.2)  # die 24 Paare der Ausreißertage fehlen
    assert "h6_24" not in bands.by_horizon


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
    # die Laufzeit prüft gleich nach der ersten Prognose, nicht erst zur Minute 4 (Test
    # test_snow_check_waits_for_the_first_forecast_then_runs_at_once in test_app_runtime.py)
    assert await asyncio.to_thread(service.check_snow, ledger) is True
    await service.refresh()  # der nächste Abruf rechnet mit den gemerkten Beobachtungen
    before = service.latest()
    assert before is not None and before.snow.state == "gemessen"
    assert before.snow.factor == pytest.approx(0.3)
    restarted, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=body)
    await restarted.refresh()
    restarted.check_snow(ledger)  # wie die Laufzeit gleich nach der ersten Prognose
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


async def test_free_stays_free_while_the_snowfall_is_in_the_weather(
    forecast_env, respx_mock, clock
) -> None:
    # Schneefall heute 02:00, 10:30 und 10:45 frei gemessen, danach trüb (nichts messbar); die
    # Schneestunde steht bis zum nächsten Abruf im Wetter, das bis zu 48 h zurückreicht
    clock.advance(EARLY)
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    await service.refresh()
    clock.advance(6 * 3600)  # 11:00 Ortszeit
    ledger = ratio_ledger(service.latest(), morning(0.8, 2, first=MORNING + SLOT))
    assert service.check_snow(ledger) is True and service.latest().snow == NO_CUT
    clock.advance(timedelta(hours=36, minutes=15))  # morgen 23:15 Ortszeit, über 36 h nach 10:45
    assert service.check_snow(ledger) is False
    assert service.latest().snow == NO_CUT


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
    assert service.set_snow_override("covered", actor="admin") is False
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
    assert await asyncio.to_thread(service.set_snow_override, "covered", actor="admin") is True
    assert json.loads(path.read_text()) == {"mode": "covered", "at": clock.now().isoformat()}
    forecast = service.latest()
    assert forecast.snow.state == "vermutet" and forecast.revision == clock.now()
    assert forecast.p50_w[NOON] == pytest.approx(0.1 * forecast.uncut_p50_w[NOON])
    reloaded, _ = forecast_env(override_path=path)  # der Knopf übersteht einen Neustart
    assert (await reloaded.refresh()).snow == forecast.snow
    clock.advance(60)
    assert service.set_snow_override(None, actor="admin") is True
    assert not path.exists() and service.latest().snow == NO_CUT
    assert service.set_snow_override(None, actor="admin") is True  # ohne Datei: nichts zu löschen
    entries = audit.recent()
    assert [(e.actor, e.source, e.action, e.target, e.details) for e in entries] == [
        ("admin", "ui", "forecast.snow_override", "pv", {"mode": "auto"}),
        ("admin", "ui", "forecast.snow_override", "pv", {"mode": "auto"}),
        ("admin", "ui", "forecast.snow_override", "pv", {"mode": "covered"}),
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
    assert (entry.actor, entry.source, entry.action, entry.details) == (
        "anna",
        "ha",
        "forecast.snow_override",
        {"mode": "free"},
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


async def test_snow_check_keeps_influx_in_order_with_the_loop(
    forecast_env, respx_mock, clock, tmp_path
) -> None:
    # wie in der Laufzeit: Punkte aus Worker-Threads gehen über die Event-Loop (LoopSink)
    recorder = FakeSink()
    sink = LoopSink(recorder)
    sink.bind(asyncio.get_running_loop())
    clock.advance(3600)  # 11:00 Ortszeit
    service, _ = forecast_env(override_path=tmp_path / "pv_snow_override.json", sink=sink)
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    first = await service.refresh()
    worker = threading.Thread(
        target=service.check_snow, args=(ratio_ledger(first, morning(0.3, 3)),)
    )
    worker.start()
    worker.join()  # die Loop läuft währenddessen nicht: was der Worker schreibt, wartet
    assert service.latest().snow.state == "gemessen"
    clock.advance(60)
    service.set_snow_override("free", actor="admin")  # neuere Prognose, aus der Loop
    await asyncio.sleep(0)
    kw = [p.fields["kw"] for p in recorder.points if p.ts == NOON and p.tags["quantile"] == "p50"]
    assert kw[-1] == pytest.approx(first.uncut_p50_w[NOON] / 1000.0)


async def test_override_file_is_written_outside_the_snow_lock(
    forecast_env, tmp_path, monkeypatch
) -> None:
    service, _ = forecast_env(override_path=tmp_path / "pv_snow_override.json")
    await service.refresh()
    held: list[bool] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        held.append(service._snow_lock.locked())  # refresh in der Loop wartete auf die Platte
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    assert service.set_snow_override("covered", actor="admin") is True
    assert held == [False]


async def test_unwritable_override_file_changes_nothing(
    forecast_env, respx_mock, clock, tmp_path, audit, monkeypatch
) -> None:
    clock.advance(EARLY)
    path = tmp_path / "data" / "pv_snow_override.json"
    path.parent.mkdir()
    service, sink = forecast_env(override_path=path)
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    before = await service.refresh()
    sink.points.clear()

    def full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "Kein Platz auf dem Gerät")

    clock.advance(60)
    with monkeypatch.context() as patch:  # Platte voll beim Ersetzen der Datei
        patch.setattr(os, "replace", full)
        with capture_logs() as logs, pytest.raises(OverrideNotSaved) as refused:
            service.set_snow_override("free", actor="admin")
    assert str(refused.value) == OVERRIDE_NOT_SAVED
    assert service.latest() is before and service.snow_override() is None
    assert list(path.parent.iterdir()) == []  # auch die temporäre Datei ist weg
    assert audit.recent() == [] and sink.points == []
    assert [
        e["log_level"] for e in logs if e["event"] == "Handschalter Schnee nicht gespeichert"
    ] == ["warning"]
    assert service.set_snow_override("covered", actor="admin") is True
    covered = service.latest()
    with monkeypatch.context() as patch:  # Automatik: die Datei lässt sich nicht löschen
        patch.setattr(type(path), "unlink", full)
        with pytest.raises(OverrideNotSaved):
            service.set_snow_override(None, actor="admin")
    assert service.latest() is covered and service.snow_override() == "covered"
    assert path.exists() and len(audit.recent()) == 1


class SteppingClock:
    """Jede Abfrage eine Minute später; merkt sich, ob dabei die Sperre gehalten wird."""

    def __init__(self, start: datetime, lock) -> None:
        self.at = start
        self.lock = lock
        self.calls: list[tuple[datetime, bool]] = []

    def now(self) -> datetime:
        self.at += timedelta(minutes=1)
        self.calls.append((self.at, self.lock.locked()))
        return self.at


async def test_snow_check_takes_the_time_inside_the_lock(forecast_env, respx_mock, clock) -> None:
    clock.advance(EARLY)
    service, _ = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0}))
    first = await service.refresh()
    stepping = SteppingClock(clock.now() + timedelta(hours=6), service._snow_lock)  # 11:00
    service._clock = stepping
    assert service.check_snow(ratio_ledger(first, morning(0.8, 2, first=MORNING + SLOT))) is True
    inside = [at for at, locked in stepping.calls if locked]
    assert inside and service.latest().revision == inside[-1]


async def test_new_since_alone_replaces_the_forecast_without_revision(
    forecast_env, respx_mock, clock
) -> None:
    clock.advance(timedelta(hours=2, minutes=30))  # 12:30 Ortszeit
    again = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)  # noch einmal Schnee um 12:00 Ortszeit
    service, sink = forecast_env()
    respx_mock.routes["open_meteo"].respond(200, text=snowy({SNOW_AT: 2.0, again: 0.5}))
    first = await service.refresh()
    assert (first.snow.state, first.snow.since) == ("vermutet", SNOW_AT)
    sink.points.clear()
    # 10:30 und 10:45 frei, danach der zweite Schneefall: wieder vermutet, gleiche Faktoren
    ledger = ratio_ledger(first, morning(0.8, 2, first=MORNING + SLOT))
    assert service.check_snow(ledger) is False
    forecast = service.latest()
    assert (forecast.snow.state, forecast.snow.since) == ("vermutet", again)
    assert forecast.snow.factors == first.snow.factors and forecast.p50_w == first.p50_w
    assert forecast.revision == first.revision and sink.points == []


def _with_snow(state, since: datetime | None) -> PvForecast:
    slot = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    power = {slot: 1000.0}
    return PvForecast(
        T_APP,
        "open_meteo",
        power,
        power,
        power,
        power,
        {},
        power,
        T_APP,
        SnowCut(
            state, 0.1 if state != "frei" else 1.0, since, {} if state == "frei" else {slot: 0.1}
        ),
    )


def test_snow_info_names_the_cut_since_local_time() -> None:
    now = T_APP  # 09.10., 10:00 Ortszeit
    text = "PV-Prognose gekürzt: vermutlich Schnee auf den Modulen"
    assert snow_info(_with_snow("vermutet", SNOW_AT), now) == [f"{text} (seit 02:00)"]
    assert snow_info(_with_snow("gemessen", SNOW_AT), now) == [f"{text} (seit 02:00)"]
    yesterday = datetime(2026, 10, 8, 21, 30, tzinfo=UTC)  # 08.10., 23:30 Ortszeit
    assert snow_info(_with_snow("vermutet", yesterday), now) == [f"{text} (seit 08.10. 23:30)"]
    assert snow_info(_with_snow("vermutet", None), now) == [text]


def test_snow_info_is_empty_without_cut_now() -> None:
    # künftiger Schneefall kürzt erst später: kein Hinweis (Ruling zum Status)
    assert snow_info(_with_snow("frei", None), T_APP) == []
    assert snow_info(None, T_APP) == []


async def test_snow_override_is_shown_while_it_applies(forecast_env, clock, tmp_path) -> None:
    service, _ = forecast_env(override_path=tmp_path / "pv_snow_override.json")
    assert service.snow_override() is None
    await service.refresh()
    assert service.set_snow_override("covered", actor="admin") is True
    assert service.snow_override() == "covered"
    clock.advance(timedelta(hours=48))
    assert service.snow_override() == "covered"  # höchstens 48 h, wie in ``snow_cut``
    clock.advance(timedelta(seconds=1))
    assert service.snow_override() is None
    assert service.set_snow_override(None, actor="admin") is True
    assert service.snow_override() is None
