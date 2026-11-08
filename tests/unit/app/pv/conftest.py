import pytest

from bindaems.app.pv.service import PvSourceService


@pytest.fixture
def service(engine, clock, audit) -> PvSourceService:
    return PvSourceService(engine, clock, audit, ha_enabled=True)
