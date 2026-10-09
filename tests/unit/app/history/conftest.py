import pytest

from bindaems.app.history.routes import history_router
from bindaems.app.history.series import build_catalog


@pytest.fixture
def client(make_client, reader, cfg, clock, guard):
    return make_client(history_router(reader, build_catalog(cfg), clock, guard))
