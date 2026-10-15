from datetime import datetime

import pytest
from sqlalchemy import Engine
from tests.helpers import FakeSink

from bindaems.app.control.decisions import DecisionCollector, DecisionStore
from bindaems.app.core_link import CoreUnavailableError
from bindaems.shared.decisions import Decision
from bindaems.shared.timeutil import ManualClock


class FakeCoreClient:
    """Stand-in für ``CoreClient.decisions``: vorbereitete Seiten, die Aufrufe werden gemerkt."""

    def __init__(self) -> None:
        self.pages: list[tuple[list[Decision], bool]] = []
        self.calls: list[tuple[datetime, int]] = []
        self.error: CoreUnavailableError | None = None

    async def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        self.calls.append((since, limit))
        if self.error is not None:
            raise self.error
        return self.pages.pop(0)


@pytest.fixture
def store(engine: Engine, clock: ManualClock) -> DecisionStore:
    return DecisionStore(engine, clock)


@pytest.fixture
def fake_client() -> FakeCoreClient:
    return FakeCoreClient()


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def collector(
    store: DecisionStore, fake_client: FakeCoreClient, sink: FakeSink, clock: ManualClock
) -> DecisionCollector:
    return DecisionCollector(store, fake_client, sink, clock)  # type: ignore[arg-type]
