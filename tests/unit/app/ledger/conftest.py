from datetime import UTC, datetime, timedelta

import pytest
from tests.app_helpers import price_row, set_feed_in
from tests.helpers import FakeSink

from bindaems.app.history.influx import Series
from bindaems.app.ledger.checks import LedgerChecks
from bindaems.app.ledger.counters import counter_tags
from bindaems.app.ledger.routes import ledger_router
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.influx.lineprotocol import epoch_ms

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
def client(make_client, ledger, checks, guard):
    return make_client(ledger_router(ledger, guard, checks))


class FakeInflux:
    """Ersatz für den ``InfluxReader``: merkt sich die Abfragen und liefert vorbereitete Reihen."""

    def __init__(self) -> None:
        self.series: list[Series] = []
        self.queries: list[str] = []
        self.error: Exception | None = None

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append(q)
        if self.error is not None:
            raise self.error
        return self.series

    def set_counters(self, readings: dict[str, dict[datetime, float]]) -> None:
        """Zählerstände je Signal und Zeitpunkt, so wie ``GROUP BY time(30m)`` sie liefert: der
        Wert steht im Eimer, der eine halbe Stunde vor dem Zeitpunkt beginnt."""
        self.series = []
        for signal, values in readings.items():
            source, ident, direction = counter_tags(signal) or ("", "", "")
            self.series.append(
                Series(
                    "energy",
                    {"source": source, "id": ident, "direction": direction},
                    ["time", "kwh"],
                    [
                        [epoch_ms(instant - timedelta(minutes=30)), value]
                        for instant, value in sorted(values.items())
                    ],
                )
            )


@pytest.fixture
def influx() -> FakeInflux:
    return FakeInflux()


@pytest.fixture
def checks(engine, ledger, influx, clock, cfg) -> LedgerChecks:
    return LedgerChecks(engine, ledger, influx, clock, cfg)  # type: ignore[arg-type]
