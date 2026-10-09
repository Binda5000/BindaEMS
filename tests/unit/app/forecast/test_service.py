from datetime import UTC, date, datetime

import httpx
import pytest
from tests.app_helpers import T_APP, StopScheduler, login_as, record_then_stop

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
    forecast = PvForecast(T_APP, "open_meteo", dict.fromkeys(local_day_slots(day), 1000.0))
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
