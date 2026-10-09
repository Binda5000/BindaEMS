import asyncio
from pathlib import Path

import httpx
import pytest
from tests.helpers import FakeSink

from bindaems.app.forecast.openmeteo import OPEN_METEO_URL
from bindaems.app.forecast.routes import forecast_router
from bindaems.app.forecast.service import ForecastService

FIXTURE = Path("tests/fixtures/openmeteo_sued_2026-10-09.json")


@pytest.fixture
def forecast_env(cfg, settings_service, clock, respx_mock):
    """Dienst mit dem Beispiel-cfg (eine Fläche Süd, 10 kWp); Route ``open_meteo`` = Aufnahme."""

    def build(sleep=asyncio.sleep) -> tuple[ForecastService, FakeSink]:
        respx_mock.get(OPEN_METEO_URL, name="open_meteo").respond(200, text=FIXTURE.read_text())
        sink = FakeSink()
        service = ForecastService(
            httpx.AsyncClient(), cfg, settings_service, clock, sink, sleep=sleep
        )
        return service, sink

    return build


@pytest.fixture
def forecast_service(forecast_env) -> ForecastService:
    service, _ = forecast_env()
    return service


@pytest.fixture
def filled_forecast(forecast_service) -> ForecastService:
    asyncio.run(forecast_service.refresh())
    return forecast_service


@pytest.fixture
def client(make_client, forecast_service, clock, guard):
    return make_client(forecast_router(forecast_service, clock, guard))
