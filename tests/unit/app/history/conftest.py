import httpx
import pytest
from pydantic import SecretStr

from bindaems.app.history.influx import InfluxReader
from bindaems.app.history.routes import history_router
from bindaems.app.history.series import build_catalog


@pytest.fixture
def reader(cfg) -> InfluxReader:
    # respx ersetzt den Transport; es entstehen keine echten Verbindungen
    return InfluxReader(cfg.influxdb, SecretStr("geheim"), httpx.AsyncClient())


@pytest.fixture
def client(make_client, reader, cfg, clock, guard):
    return make_client(history_router(reader, build_catalog(cfg), clock, guard))
