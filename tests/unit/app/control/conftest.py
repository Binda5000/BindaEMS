import asyncio
from collections.abc import Callable
from datetime import datetime

import pytest
from sqlalchemy import Engine
from tests.helpers import FakeSink

from bindaems.app.audit import AuditLog
from bindaems.app.control.decisions import DecisionCollector, DecisionStore
from bindaems.app.control.intent import IntentService
from bindaems.app.core_link import CoreReply, CoreUnavailableError, LiveState
from bindaems.shared.config import Config
from bindaems.shared.control import Intent
from bindaems.shared.decisions import Decision
from bindaems.shared.timeutil import ManualClock


class FakeCoreClient:
    """Stand-in für ``CoreClient``: ``decisions`` mit vorbereiteten Seiten, ``intent`` und
    ``submit_intent`` mit einer gültigen Absicht; die Aufrufe werden gemerkt.

    ``replies`` sind vorbereitete Antworten auf ``submit_intent`` (oder Funktionen, die sie beim
    Senden erzeugen); ohne sie nimmt der core eine Absicht mit höherer Version an und lehnt eine
    andere mit 409 ab.
    """

    def __init__(self) -> None:
        self.pages: list[tuple[list[Decision], bool]] = []
        self.calls: list[tuple[datetime, int]] = []
        self.error: CoreUnavailableError | None = None
        self.valid: Intent | None = None
        self.replies: list[CoreReply | Callable[[], CoreReply]] = []
        self.sent: list[Intent] = []
        self.ack_reply = CoreReply(
            202, {"detail": "Quittiert. Die Sperre von EVCS löst sich im nächsten Zyklus."}
        )
        self.acked: list[str] = []

    async def acknowledge(self, actuator: str) -> CoreReply:
        if self.error is not None:
            raise self.error
        self.acked.append(actuator)
        return self.ack_reply

    async def intent(self) -> Intent | None:
        if self.error is not None:
            raise self.error
        await asyncio.sleep(0)
        return self.valid

    async def submit_intent(self, intent: Intent) -> CoreReply:
        if self.error is not None:
            raise self.error
        self.sent.append(intent)
        await asyncio.sleep(0)
        if self.replies:
            reply = self.replies.pop(0)
            return reply() if callable(reply) else reply
        if self.valid is not None and intent.version <= self.valid.version:
            return CoreReply(
                409, {"result": "rejected", "version": self.valid.version, "details": []}
            )
        self.valid = intent
        return CoreReply(200, {"result": "accepted", "version": intent.version, "details": []})

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


class FakeFeed:
    """Stand-in für ``LiveFeed.refresh_health``."""

    def __init__(self) -> None:
        self.refreshed = 0

    def refresh_health(self) -> None:
        self.refreshed += 1


@pytest.fixture
def live() -> LiveState:
    return LiveState()


@pytest.fixture
def feed() -> FakeFeed:
    return FakeFeed()


@pytest.fixture
def service(
    fake_client: FakeCoreClient,
    live: LiveState,
    feed: FakeFeed,
    audit: AuditLog,
    clock: ManualClock,
    cfg: Config,
) -> IntentService:
    return IntentService(fake_client, live, feed, audit, clock, cfg)  # type: ignore[arg-type]
