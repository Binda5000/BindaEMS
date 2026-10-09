from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from tests.app_helpers import FAST_HASHER, T_APP

from bindaems.app.api.server import create_app
from bindaems.app.audit import AuditLog
from bindaems.app.auth.routes import audit_router, auth_router, users_router
from bindaems.app.auth.service import AuthService
from bindaems.app.auth.web import Guard
from bindaems.app.db.engine import migrate, open_database
from bindaems.app.history.influx import InfluxReader
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
from bindaems.shared.timeutil import ManualClock


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(T_APP)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = open_database(tmp_path / "app.sqlite3")
    migrate(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def audit(engine: Engine, clock: ManualClock) -> AuditLog:
    return AuditLog(engine, clock)


@pytest.fixture
def auth(engine: Engine, clock: ManualClock, audit: AuditLog) -> AuthService:
    return AuthService(engine, clock, audit, hasher=FAST_HASHER)


@pytest.fixture
def guard(auth: AuthService) -> Guard:
    return Guard(auth, cookie_secure=True)


@pytest.fixture
def make_client(auth: AuthService, guard: Guard, audit: AuditLog) -> Callable[..., TestClient]:
    """Test-Client über HTTPS – sonst schickt er die Secure-Cookies nicht zurück."""

    def build(*routers: APIRouter) -> TestClient:
        base = [auth_router(auth, guard), users_router(auth, guard), audit_router(audit, guard)]
        return TestClient(create_app([*base, *routers]), base_url="https://testserver")

    return build


@pytest.fixture
def settings_service(engine: Engine, clock: ManualClock, audit: AuditLog) -> SettingsService:
    return SettingsService(engine, clock, audit)


@pytest.fixture
def reader(cfg: Config) -> InfluxReader:
    # respx ersetzt den Transport; es entstehen keine echten Verbindungen
    return InfluxReader(cfg.influxdb, SecretStr("geheim"), httpx.AsyncClient())
