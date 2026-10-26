"""Fixtures der PV-Prognose.

``FIXTURE`` ist eine Open-Meteo-Antwort für eine Südfläche. Ihre Reihen ``shortwave_radiation`` und
``diffuse_radiation`` sind von Hand erzeugt (das Netz war gesperrt), keine Aufnahme: global =
0,78 · GTI, Diffusanteil = 1,05 − GTI/800, begrenzt auf 0,12 bis 0,95.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from tests.helpers import FakeSink

from bindaems.app.forecast.archive import ForecastArchive
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.openmeteo import OPEN_METEO_URL
from bindaems.app.forecast.quality import ForecastQuality
from bindaems.app.forecast.routes import forecast_router
from bindaems.app.forecast.service import ForecastService
from bindaems.app.learning.weather import WeatherArchive
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.config import LoadHistoryConfig
from bindaems.shared.timeutil import ManualClock

NOW = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)  # 08:00 Ortszeit, ein Freitag

FIXTURE = Path("tests/fixtures/openmeteo_sued_2026-10-09.json")


@pytest.fixture
def archive(engine) -> ForecastArchive:
    return ForecastArchive(engine)


@pytest.fixture
def weather(engine) -> WeatherArchive:
    return WeatherArchive(engine)


async def no_sleep(seconds: float) -> None:
    """Wartet nicht; so kosten die Wiederholungen nach 30 s im Test keine Zeit."""


@pytest.fixture
def forecast_env(cfg, settings_service, clock, respx_mock, archive, weather):
    """Dienst mit dem Beispiel-cfg (eine Fläche Süd, 10 kWp); Route ``open_meteo`` = Aufnahme."""

    def build(sleep=no_sleep) -> tuple[ForecastService, FakeSink]:
        respx_mock.get(OPEN_METEO_URL, name="open_meteo").respond(200, text=FIXTURE.read_text())
        sink = FakeSink()
        service = ForecastService(
            httpx.AsyncClient(), cfg, settings_service, clock, sink, archive, weather, sleep=sleep
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
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def now_clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def ledger(engine, now_clock, settings_service, audit, sink) -> LedgerService:
    prices = PriceService(PriceStore(engine, now_clock), settings_service)
    return LedgerService(engine, now_clock, prices, settings_service, audit, sink)


@pytest.fixture
def load_cfg(cfg):
    """Beispielkonfiguration mit Lastverlauf aus Home Assistant (``sensor.hausverbrauch``)."""
    home = cfg.homeassistant.model_copy(
        update={"load_history": LoadHistoryConfig(entity="sensor.hausverbrauch")}
    )
    return cfg.model_copy(update={"homeassistant": home})


@pytest.fixture
def load_service(cfg, ledger, archive, now_clock, sink) -> LoadForecastService:
    """Ohne Home Assistant: nur die Abrechnung liefert Verlauf."""
    return LoadForecastService(ledger, cfg, None, archive, now_clock, sink)


@pytest.fixture
def load_service_with_ha(load_cfg, ledger, reader, archive, now_clock, sink) -> LoadForecastService:
    return LoadForecastService(ledger, load_cfg, reader, archive, now_clock, sink)


@pytest.fixture
def quality(engine, archive, ledger) -> ForecastQuality:
    return ForecastQuality(engine, archive, ledger, 100.0)


@pytest.fixture
def client(make_client, forecast_service, load_service, quality, clock, guard):
    return make_client(forecast_router(forecast_service, load_service, quality, clock, guard))
