from datetime import UTC, datetime

import pytest
from tests.app_helpers import price_row, set_feed_in
from tests.helpers import FakeSink

from bindaems.app.ledger.routes import ledger_router
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore

S = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def ledger(engine, clock, store, settings_service, audit, sink) -> LedgerService:
    return LedgerService(
        engine, clock, PriceService(store, settings_service), settings_service, audit, sink
    )


@pytest.fixture
def prices_with_10ct(store) -> None:
    store.save([price_row(S, 10.0, "primary")])


@pytest.fixture
def feed_in_7ct(settings_service) -> None:
    set_feed_in(settings_service, {"2026-10": 7.0})


@pytest.fixture
def client(make_client, ledger, guard):
    return make_client(ledger_router(ledger, guard))
