import pytest

from bindaems.app.consumers.routes import consumers_router
from bindaems.app.consumers.service import ConsumerService
from bindaems.app.core_link import LiveState


@pytest.fixture
def service(engine, clock, audit) -> ConsumerService:
    return ConsumerService(engine, clock, audit, ha_enabled=True)


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {
        "signals": {"load.obergeschoss.power_w": {"v": 640.0, "ts": "…", "q": "ok"}},
        "derived": {"house_load_w": 2100.0},
    }
    return state


@pytest.fixture
def client(make_client, service, live, reader, cfg, guard):
    return make_client(consumers_router(service, live, None, reader, cfg, guard))
