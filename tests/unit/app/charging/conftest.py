import pytest

from bindaems.app.charging.tracker import ChargingTracker
from bindaems.app.core_link import LiveState


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {"signals": {}}
    return state


@pytest.fixture
def tracker(engine, clock, cfg, live) -> ChargingTracker:
    return ChargingTracker(engine, clock, cfg, live)


@pytest.fixture
def ledger(engine, clock, settings_service, audit):
    from tests.helpers import FakeSink

    from bindaems.app.ledger.service import LedgerService
    from bindaems.app.prices.service import PriceService
    from bindaems.app.prices.store import PriceStore

    prices = PriceService(PriceStore(engine, clock), settings_service)
    return LedgerService(engine, clock, prices, settings_service, audit, FakeSink())


@pytest.fixture
def costing(engine, ledger, settings_service):
    from bindaems.app.ledger.costing import LedgerCosting

    costing = LedgerCosting(engine, ledger, settings_service, usable_kwh=10.0)
    ledger.on_slots_changed(costing.mark_dirty)  # wie in runtime.py
    return costing


@pytest.fixture
def service(tracker, ledger, costing, clock):
    from bindaems.app.charging.sessions import SessionService

    return SessionService(tracker, ledger, costing, clock)


@pytest.fixture
def client(make_client, service, cfg, settings_service, guard):
    from bindaems.app.charging.routes import charging_router

    return make_client(charging_router(service, cfg, settings_service, guard))
