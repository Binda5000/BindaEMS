from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from typing import Any

import aiomqtt
import httpx
import pytest
from pydantic import SecretStr
from tests.helpers import T0

from bindaems.core.adapters.coe import CoeAdapter
from bindaems.core.adapters.evcs import EvcsAdapter
from bindaems.core.adapters.homeassistant import HaAdapter
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import MqttMessage, VictronMqttAdapter
from bindaems.core.state.store import StateStore
from bindaems.shared.config import CoeOffsetConfig, Config, EvcsConfig, TwcConfig
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
        messages: Iterable[MqttMessage | Callable[[], object]] = (),
        *,
        fail_on_enter: bool = False,
        end_with_error: bool = False,
        fail_publish_prefix: str | None = None,
    ) -> None:
        self._messages = list(messages)
        self._fail_on_enter = fail_on_enter
        self._end_with_error = end_with_error
        self._fail_publish_prefix = fail_publish_prefix
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
        if self._fail_publish_prefix is not None and topic.startswith(self._fail_publish_prefix):
            raise aiomqtt.MqttError("Senden fehlgeschlagen")
        self.published.append((topic, payload))

    async def messages(self) -> AsyncIterator[MqttMessage]:
        for message in self._messages:
            if callable(message):  # Skript-Schritt, z. B. die Uhr vorstellen
                message()
                continue
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
        sessions: Sequence[Iterable[MqttMessage | Callable[[], object]]] | None = None,
        fail_publish_prefix: str | None = None,
    ) -> tuple[Any, list[FakeTransport], StateStore]:
        def make_transport(
            scripted: Iterable[MqttMessage | Callable[[], object]] = (), **options: bool
        ) -> FakeTransport:
            # ``fail_publish_prefix`` gilt für jeden Transport, auch für spätere Sitzungen
            return FakeTransport(scripted, fail_publish_prefix=fail_publish_prefix, **options)

        specs: list[FakeTransport] = []
        if first_fails:
            specs.append(make_transport(fail_on_enter=True))
        if sessions is not None:  # mehrere Sitzungen; alle außer der letzten enden mit Fehler
            specs += [make_transport(m, end_with_error=True) for m in sessions[:-1]]
            specs.append(make_transport(sessions[-1]))
        else:
            specs.append(make_transport(messages, end_with_error=end_with_error))
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
        clock = ManualClock(T0)
        store = StateStore(clock)
        sleep = RecordingSleep()
        adapter = VictronMqttAdapter(victron, None, store, factory, sleep=sleep)
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        adapter.clock = clock  # type: ignore[attr-defined]
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


@pytest.fixture
async def tessie_env(cfg: Config) -> AsyncIterator[Callable[..., tuple[Any, ...]]]:
    clients: list[httpx.AsyncClient] = []

    def build(with_clock: bool = False) -> tuple[Any, ...]:
        client = httpx.AsyncClient()
        clients.append(client)
        clock = ManualClock(T0)
        store = StateStore(clock)
        sleep = RecordingSleep()
        adapter = TessieAdapter(
            "tesla",
            cfg.vehicles["tesla"],
            cfg.site.location,
            SecretStr("tok"),
            store,
            client,
            sleep=sleep,
        )
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        return (adapter, store, clock) if with_clock else (adapter, store)

    yield build
    for client in clients:
        await client.aclose()


class FakeConnection:
    """Eingangs-Skript für ``recv_json``: ein Dict ist eine Nachricht, eine Exception wird
    geworfen; danach blockiert ``recv_json`` bis zum Abbruch. Gesendetes landet in ``outbound``.
    """

    def __init__(self, inbound: Iterable[dict[str, Any] | Exception]) -> None:
        self._inbound = list(inbound)
        self.outbound: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.outbound.append(msg)

    async def recv_json(self) -> dict[str, Any]:
        while True:
            if not self._inbound:
                await asyncio.Event().wait()  # blockiert bis zum Abbruch
            item = self._inbound.pop(0)
            if callable(item):  # Skript-Schritt, z. B. die Uhr vorstellen
                item()
                continue
            if isinstance(item, Exception):
                raise item
            return item

    async def close(self) -> None:
        return None


@pytest.fixture
def ha_env(cfg: Config) -> Callable[..., tuple[Any, FakeConnection, StateStore]]:
    def build(
        inbound: Iterable[dict[str, Any] | Exception],
        ping_s: float = 30.0,
        entities: dict[str, str] | None = None,
    ) -> tuple[Any, FakeConnection, StateStore]:
        assert cfg.homeassistant is not None
        ha = cfg.homeassistant.model_copy(
            update={"entities": entities or {"vehicle.egolf.soc_pct": "sensor.egolf_soc"}}
        )
        conn = FakeConnection(inbound)

        async def connect(url: str) -> FakeConnection:
            return conn

        clock = ManualClock(T0)
        store = StateStore(clock)
        sleep = RecordingSleep()
        adapter = HaAdapter(ha, SecretStr("tok"), store, connect, ping_s=ping_s, sleep=sleep)
        adapter.sleeps = sleep.calls  # type: ignore[attr-defined]
        adapter.clock = clock  # type: ignore[attr-defined]
        return adapter, conn, store

    return build


class FakeDatagram:
    """Ersatz für den UDP-Port: sammelt Gesendetes und reicht Empfangenes an den Adapter."""

    def __init__(self, on_datagram: Callable[[bytes, tuple[str, int]], None]) -> None:
        self.on_datagram = on_datagram
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


class GatedSleep:
    """Ersatz für ``asyncio.sleep``: jeder Aufruf wartet, bis er freigegeben wird.

    ``release`` beendet alle wartenden Pausen; ``release_due`` nur die, deren Dauer nach der
    Uhr ``now`` abgelaufen ist.
    """

    def __init__(self, now: Callable[[], float] = lambda: 0.0) -> None:
        self.calls: list[float] = []
        self._now = now
        self._waiting: list[tuple[float, asyncio.Future[None]]] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiting.append((self._now() + delay, future))
        await future

    def release(self) -> None:
        waiting, self._waiting = self._waiting, []
        for _, future in waiting:
            if not future.done():
                future.set_result(None)

    def release_due(self) -> None:
        now = self._now()
        due = [item for item in self._waiting if item[0] <= now]
        self._waiting = [item for item in self._waiting if item[0] > now]
        for _, future in due:
            if not future.done():
                future.set_result(None)


class CoeEnv:
    """Der CoE-Adapter mit Ersatzport, von Hand gestellten Uhren und Takt (``tick``)."""

    def __init__(self, cfg: CoeOffsetConfig, ports: list[Any] | None = None) -> None:
        self.cfg = cfg
        self.clock = ManualClock(T0)
        self.now = 1000.0  # Monotonic-Uhr des Adapters
        self.store = StateStore(self.clock)
        self.sleep = GatedSleep(self.mono)
        self.ports: list[FakeDatagram] = []
        self.open_errors: list[Exception] = []
        self.adapter = CoeAdapter(
            "klima", cfg, self.store, self._open, clock=self.mono, sleep=self.sleep
        )
        self.task: asyncio.Task[None] | None = None

    async def _open(
        self, on_datagram: Callable[[bytes, tuple[str, int]], None], listen_port: int
    ) -> FakeDatagram:
        if self.open_errors:
            raise self.open_errors.pop(0)
        port = FakeDatagram(on_datagram)
        self.ports.append(port)
        return port

    @property
    def port(self) -> FakeDatagram:
        return self.ports[-1]

    def mono(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.clock.advance(seconds)

    async def settle(self) -> None:
        for _ in range(10):
            await asyncio.sleep(0)

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.adapter.run())
        await self.settle()

    async def tick(self) -> None:
        """Lässt jede wartende Pause des Adapters einmal ablaufen."""
        self.sleep.release()
        await self.settle()

    async def run_for(self, seconds: float, step: float = 0.5) -> None:
        """Lässt die Zeit in kleinen Schritten vergehen; jede Pause endet nach ihrer Dauer."""
        for _ in range(round(seconds / step)):
            self.advance(step)
            self.sleep.release_due()
            await self.settle()

    async def receive(self, data: bytes, addr: tuple[str, int]) -> None:
        await self.start()
        self.port.on_datagram(data, addr)
        await self.settle()

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task


@pytest.fixture
async def coe_env(cfg: Config) -> AsyncIterator[Callable[[], CoeEnv]]:
    envs: list[CoeEnv] = []

    def build() -> CoeEnv:
        klima = cfg.thermal.devices["klima"]
        assert isinstance(klima, CoeOffsetConfig)
        env = CoeEnv(klima)
        envs.append(env)
        return env

    yield build
    for env in envs:
        await env.stop()
