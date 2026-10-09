from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from tests.app_helpers import FAST_HASHER, T_APP

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthService
from bindaems.app.db.engine import migrate, open_database
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
