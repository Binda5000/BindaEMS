import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from tests.app_helpers import T_APP, StopScheduler, login_as, record_then_stop
from tests.unit.app.forecast.conftest import FIXTURE

from bindaems.app.forecast.pv_model import plane_power_w
from bindaems.app.forecast.service import PvForecast
from bindaems.shared.settings import PvModelSettings
from bindaems.shared.timeutil import local_day_slots


async def test_refresh_builds_forecast_and_writes_points(forecast_env) -> None:
    service, sink = forecast_env()
    forecast = await service.refresh()
    assert forecast.p50_w[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] == pytest.approx(
        plane_power_w(15.5, 12.2, 10.0, PvModelSettings())
    )
    assert {(p.measurement, p.rp, p.tags["kind"], p.tags["quantile"]) for p in sink.points} == {
        ("forecast", "long", "pv", "p50")
    }
    assert len(sink.points) == 384 and service.latest() is forecast
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
    assert forecast.raw_w == forecast.p50_w == forecast.p25_w == forecast.p75_w
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
    assert service.component_status().ok and len(sink.points) == 384
