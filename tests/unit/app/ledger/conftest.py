import re
from datetime import UTC, datetime

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


_STATEMENT = re.compile(
    r'SELECT (?P<select>last|first)\("kwh"\) AS "(?P<column>\w+)" '
    r'FROM "(?P<rp>raw|long)"\."energy" '
    r"WHERE time >= (?P<low>\d+)ms AND time < (?P<high>\d+)ms"
)
MINUTE_MS = 60_000


def _stamped(values: dict[datetime, float], rp: str) -> list[tuple[int, float]]:
    """Die Punkte, wie sie in der Aufbewahrung ``rp`` stehen: in ``raw`` mit dem Lesezeitpunkt, in
    ``long`` wie von der CQ ``cq_energy_1m`` (``last(kwh) … GROUP BY time(1m)``) je Minute der
    letzte Wert, gestempelt mit dem Anfang der Minute."""
    readings = sorted((epoch_ms(t), v) for t, v in values.items())
    if rp == "raw":
        return readings
    buckets: dict[int, float] = {}
    for stamp, value in readings:
        buckets[stamp // MINUTE_MS * MINUTE_MS] = value  # sortiert: der letzte bleibt
    return sorted(buckets.items())


class FakeInflux:
    """Ersatz für den ``InfluxReader``: merkt sich die Abfragen und liefert vorbereitete Reihen.

    Mit ``set_counters`` liegen Rohwerte der Zähler bereit; Anweisungen der Form
    ``SELECT last|first("kwh") AS "<Spalte>" FROM "raw"|"long"."energy" WHERE time >= …ms AND
    time < …ms`` (mehrere durch ``;`` getrennt) beantwortet die Attrappe dann wie InfluxDB: je
    Zähler der letzte bzw. erste Punkt im Fenster mit seiner Zeit. In ``raw`` ist das der
    Lesezeitpunkt, in ``long`` der Anfang der Minute wie bei der CQ (``_stamped``)."""

    def __init__(self) -> None:
        self.series: list[Series] = []
        self.queries: list[str] = []
        self.error: Exception | None = None
        self.points: dict[str, dict[datetime, float]] | None = None

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append(q)
        if self.error is not None:
            raise self.error
        if self.points is None:
            return self.series
        result: list[Series] = []
        for statement in q.split(";"):
            match = _STATEMENT.search(statement)
            if match is None:
                continue
            low, high = int(match["low"]), int(match["high"])
            for signal, values in self.points.items():
                inside = [(t, v) for t, v in _stamped(values, match["rp"]) if low <= t < high]
                if not inside:
                    continue
                stamp, value = inside[-1] if match["select"] == "last" else inside[0]
                result.append(
                    Series("energy", _tags(signal), ["time", match["column"]], [[stamp, value]])
                )
        return result

    def set_counters(self, readings: dict[str, dict[datetime, float]]) -> None:
        """Rohwerte der Zähler je Signal und Lesezeitpunkt (``long`` entsteht daraus wie per CQ)."""
        self.points = readings


def _tags(signal: str) -> dict[str, str]:
    source, ident, direction = counter_tags(signal) or ("", "", "")
    return {"source": source, "id": ident, "direction": direction}


@pytest.fixture
def influx() -> FakeInflux:
    return FakeInflux()


@pytest.fixture
def checks(engine, ledger, influx, clock, cfg) -> LedgerChecks:
    return LedgerChecks(engine, ledger, influx, clock, cfg)  # type: ignore[arg-type]
