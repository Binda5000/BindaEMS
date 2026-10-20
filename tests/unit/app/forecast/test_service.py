import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from structlog.testing import capture_logs
from tests.app_helpers import T_APP, StopScheduler, login_as, record_then_stop
from tests.unit.app.forecast.conftest import FIXTURE

from bindaems.app.forecast.archive import RecordValue
from bindaems.app.forecast.pv_model import plane_power_w
from bindaems.app.forecast.service import PvForecast
from bindaems.app.ledger.service import LedgerSlot
from bindaems.shared.settings import PvModelSettings
from bindaems.shared.timeutil import local_day_slots


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
    forecast = PvForecast(T_APP, "open_meteo", power, power, power, power, {})
    assert forecast.day_energy_kwh(day) == pytest.approx(25.0)  # 100 Slots × 0,25 h × 1 kW
    assert forecast.day_energy_kwh(date(2026, 10, 26)) is None
    assert forecast.power_at(local_day_slots(day)[3].replace(minute=7)) == 1000.0


async def test_run_refreshes_at_minute_7_and_retries_sooner_after_error(
    forecast_env, respx_mock, clock
) -> None:
    clock.advance(8 * 60)  # 10:08 Ortszeit; nächster Termin 11:07
    sleeps: list[float] = []
    service, _ = forecast_env(sleep=record_then_stop(sleeps, after=2))
    body = respx_mock.routes["open_meteo"].return_value.text
    respx_mock.routes["open_meteo"].side_effect = [
        httpx.Response(503),
        httpx.Response(200, text=body),
    ]
    with pytest.raises(StopScheduler):
        await service.run()
    assert sleeps == [600.0, 59 * 60.0] and service.latest() is not None


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
