from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

import httpx
import pytest
from tests.helpers import T0

from bindaems.core.adapters.evcs import EvcsAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import MqttMessage, VictronMqttAdapter
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, EvcsConfig, TwcConfig
from bindaems.shared.timeutil import ManualClock

P = "c0619ab1234"


class RecordingSleep:
    """Ersatz für ``asyncio.sleep``: zeichnet Wartezeiten auf und kehrt sofort zurück."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)
        await asyncio.sleep(0)


class FakeTransport:
    def __init__(
        self,
        messages: Iterable[MqttMessage] = (),
        *,
        fail_on_enter: bool = False,
        end_with_error: bool = False,
    ) -> None:
        self._messages = list(messages)
        self._fail_on_enter = fail_on_enter
        self._end_with_error = end_with_error
        self.subscribed: list[str] = []
        self.published: list[tuple[str, bytes]] = []

    async def __aenter__(self) -> FakeTransport:
        if self._fail_on_enter:
            raise ConnectionError("Verbindung abgelehnt")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def publish(self, topic: str, payload: bytes) -> None:
        self.published.append((topic, payload))

    async def messages(self) -> AsyncIterator[MqttMessage]:
        for message in self._messages:
            yield message
        if self._end_with_error:
            raise ConnectionError("Verbindung verloren")
        await asyncio.Event().wait()  # blockiert bis zum Abbruch


@pytest.fixture
def fake_env(cfg: Config) -> Callable[..., tuple[Any, list[FakeTransport], StateStore]]:
    def build(
        keepalive_s: float = 30.0,
        messages: Iterable[MqttMessage] = (),
        portal_id: str | None = P,
        first_fails: bool = False,
        end_with_error: bool = False,
    ) -> tuple[Any, list[FakeTransport], StateStore]:
        specs: list[FakeTransport] = []
        if first_fails:
            specs.append(FakeTransport(fail_on_enter=True))
        specs.append(FakeTransport(messages, end_with_error=end_with_error))
        transports: list[FakeTransport] = []

        def factory() -> FakeTransport:
            if not specs:
                raise ConnectionError("keine weitere Verbindung")
            transport = specs.pop(0)
            transports.append(transport)
            return transport

        mqtt = cfg.victron.mqtt.model_copy(
            update={"keepalive_s": keepalive_s, "portal_id": portal_id}
        )
        victron = cfg.victron.model_copy(update={"mqtt": mqtt})
        store = StateStore(ManualClock(T0))
        sleep = RecordingSleep()
        adapter = VictronMqttAdapter(victron, None, store, factory, sleep=sleep)
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        return adapter, transports, store

    return build


class FakeReader:
    """Liefert ``blocks`` der Reihe nach: eine Liste als Registerinhalt, eine Exception wird
    geworfen; danach blockiert ``read_holding`` bis zum Abbruch.

    Jeder Aufruf landet in ``calls`` – auch jeder unbekannte, etwa ein Schreibversuch.
    """

    def __init__(self, blocks: Iterable[list[int] | Exception]) -> None:
        self._blocks = list(blocks)
        self.calls: list[tuple[object, ...]] = []

    async def connect(self) -> None:
        self.calls.append(("connect",))

    async def read_holding(self, address: int, count: int) -> list[int]:
        self.calls.append(("read_holding", address, count))
        if not self._blocks:
            await asyncio.Event().wait()  # blockiert bis zum Abbruch
        block = self._blocks.pop(0)
        if isinstance(block, Exception):
            raise block
        return block

    async def close(self) -> None:
        self.calls.append(("close",))

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        if name.startswith("_"):
            raise AttributeError(name)

        async def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, *args))

        return record


@pytest.fixture
def evcs_env(cfg: Config) -> Callable[..., tuple[Any, FakeReader, StateStore]]:
    def build(
        blocks: Iterable[list[int] | Exception], poll_s: float = 1.0
    ) -> tuple[Any, FakeReader, StateStore]:
        evcs = cfg.wallboxes["evcs"]
        assert isinstance(evcs, EvcsConfig)
        reader = FakeReader(blocks)
        clock = ManualClock(T0)
        store = StateStore(clock)
        sleep = RecordingSleep()
        adapter = EvcsAdapter("evcs", evcs, store, lambda: reader, poll_s=poll_s, sleep=sleep)
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        adapter.clock = clock  # type: ignore[attr-defined]
        return adapter, reader, store

    return build


@pytest.fixture
async def twc_env(cfg: Config) -> AsyncIterator[Callable[..., tuple[Any, StateStore]]]:
    clients: list[httpx.AsyncClient] = []

    def build(**kwargs: Any) -> tuple[Any, StateStore]:
        twc = cfg.wallboxes["twc"]
        assert isinstance(twc, TwcConfig)
        client = httpx.AsyncClient()
        clients.append(client)
        store = StateStore(ManualClock(T0))
        sleep = RecordingSleep()
        adapter = TwcAdapter("twc", twc, store, client, sleep=sleep, **kwargs)
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        return adapter, store

    yield build
    for client in clients:
        await client.aclose()
