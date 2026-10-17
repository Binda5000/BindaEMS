from datetime import UTC, date, datetime

import pytest
from sqlalchemy import insert
from tests.app_helpers import price_row, set_feed_in
from tests.helpers import FakeSink
from tests.unit.app.ledger.conftest import FakeInflux

from bindaems.app.charging.sessions import SessionService
from bindaems.app.charging.tracker import ChargingTracker
from bindaems.app.consumers.days import ConsumerDays
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.service import ConsumerInput, ConsumerService
from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import charging_session
from bindaems.app.ledger.checks import LedgerChecks
from bindaems.app.ledger.costing import LedgerCosting
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.app.stats.routes import stats_router
from bindaems.app.stats.service import StatsService
from bindaems.shared.timeutil import slots_between

T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)  # 08:00 Ortszeit, vor "jetzt" (10:00 Ortszeit)
TODAY = date(2026, 10, 9)


@pytest.fixture
def price_store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def ledger(engine, clock, price_store, settings_service, audit) -> LedgerService:
    prices = PriceService(price_store, settings_service)
    return LedgerService(engine, clock, prices, settings_service, audit, FakeSink())


@pytest.fixture
def price_all(price_store):
    """Börsenpreis 10 ct netto (13,44 ct brutto) für alle Viertelstunden ``[start, end)``."""

    def fill(start: datetime, end: datetime, net: float = 10.0) -> None:
        price_store.save([price_row(slot, net, "primary") for slot in slots_between(start, end)])

    return fill


@pytest.fixture
def feed_in_7ct(settings_service) -> None:
    set_feed_in(settings_service, {"2026-10": 7.0, "2026-03": 7.0, "2026-11": 7.0})


@pytest.fixture
def costing(engine, ledger, settings_service) -> LedgerCosting:
    costing = LedgerCosting(engine, ledger, settings_service, usable_kwh=10.0)
    ledger.on_slots_changed(costing.mark_dirty)
    return costing


@pytest.fixture
def tracker(engine, clock, cfg) -> ChargingTracker:
    live = LiveState()
    live.connected = True
    live.state = {"signals": {}}
    return ChargingTracker(engine, clock, cfg, live)


@pytest.fixture
def influx() -> FakeInflux:
    return FakeInflux()


@pytest.fixture
def consumers(engine, clock, audit) -> ConsumerService:
    return ConsumerService(engine, clock, audit, ha_enabled=False)


@pytest.fixture
def energy(influx, clock) -> EnergyCache:
    return EnergyCache(influx, clock, None)  # type: ignore[arg-type]


@pytest.fixture
def stats(
    ledger, tracker, costing, consumers, energy, influx, engine, clock, settings_service, cfg
) -> StatsService:
    sessions = SessionService(tracker, ledger, costing, clock)
    days = ConsumerDays(engine, energy, consumers, clock)
    checks = LedgerChecks(engine, ledger, influx, clock, cfg)  # type: ignore[arg-type]
    return StatsService(
        ledger, sessions, consumers, days, energy, checks, settings_service, cfg, clock
    )


@pytest.fixture
def client(make_client, stats, guard):
    return make_client(stats_router(stats, guard))


def add_session(engine, start, end, vehicle="tesla", wallbox="twc") -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox=wallbox, vehicle=vehicle, start=start, end=end, last_seen=end or start
            )
        )


def add_consumer(consumers: ConsumerService, name: str) -> int:
    return consumers.create(
        ConsumerInput(
            name=name, color="#336699", source_kind="core", power_ref=f"load.{name}.power_w"
        ),
        actor="chris",
        source="ui",
    ).id
