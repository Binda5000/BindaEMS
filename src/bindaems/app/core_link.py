"""Verbindung zum core: Health-Abfrage, Live-Stream und der letzte bekannte Zustand.

Fällt der core aus, läuft die app weiter: Der letzte Zustand bleibt zur Anzeige erhalten, gilt
aber als veraltet (``value``/``derived`` liefern nichts mehr), bis der Stream wieder steht.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

import httpx
import structlog
from pydantic import SecretStr
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from bindaems.app.status import ComponentStatus
from bindaems.shared.broadcast import Broadcast
from bindaems.shared.domain import Value
from bindaems.shared.retry import Backoff, StatusLog
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

HEALTH_TIMEOUT_S = 5.0
OPEN_TIMEOUT_S = 5.0

SlotFlowsHandler = Callable[[dict[str, Any]], Awaitable[None]]


class CoreUnavailableError(Exception):
    """Der core ist nicht erreichbar oder lehnt die Verbindung ab."""


class CoreClient:
    def __init__(self, base_url: str, token: SecretStr, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http

    def _headers(self) -> dict[str, str]:
        # nur im Header: ein Token in der URL landete in Zugriffslogs
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def health(self) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{self._base}/v1/health", headers=self._headers(), timeout=HEALTH_TIMEOUT_S
            )
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise CoreUnavailableError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise CoreUnavailableError("Antwort ist kein JSON") from exc
        if not isinstance(data, dict):
            raise CoreUnavailableError("Antwort ist kein JSON-Objekt")
        return data

    def stream(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        return self._stream()

    @contextlib.asynccontextmanager
    async def _stream(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        url = "ws" + self._base.removeprefix("http") + "/v1/stream"
        try:
            ws = await ws_connect(
                url, additional_headers=self._headers(), proxy=None, open_timeout=OPEN_TIMEOUT_S
            )
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise CoreUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        try:
            yield _messages(ws)
        finally:
            await ws.close()


async def _messages(ws: ClientConnection) -> AsyncIterator[dict[str, Any]]:
    try:
        async for raw in ws:
            data = json.loads(raw)
            if isinstance(data, dict):
                yield data
    except ConnectionClosed as exc:  # u. a. 4401: Token abgelehnt
        code = exc.rcvd.code if exc.rcvd is not None else None
        raise CoreUnavailableError(f"Stream geschlossen (Code {code})") from exc


class LiveState:
    """Letzter Stand vom core; ``publish``/``subscribe`` verteilen Nachrichten an das UI."""

    def __init__(self) -> None:
        self.connected = False
        self.state: dict[str, Any] | None = None
        self.alarms: list[dict[str, Any]] = []
        self.health: dict[str, Any] | None = None
        self.updated_at: datetime | None = None
        self._broadcast = Broadcast()

    def value(self, signal: str) -> Value:
        """Wert eines Signals – nur bei Verbindung und Qualität ``ok``."""
        if not self.connected or self.state is None:
            return None
        signals = self.state.get("signals")
        reading = signals.get(signal) if isinstance(signals, dict) else None
        if not isinstance(reading, dict) or reading.get("q") != "ok":
            return None
        value = reading.get("v")
        if value is None or isinstance(value, bool | int | float | str):
            return value
        return None

    def derived(self) -> Mapping[str, Any]:
        """Abgeleitete Größen des core – leer, solange er nicht verbunden ist."""
        if not self.connected or self.state is None:
            return {}
        derived = self.state.get("derived")
        return derived if isinstance(derived, dict) else {}

    def publish(self, msg: dict[str, Any]) -> None:
        self._broadcast.publish(msg)

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        return self._broadcast.subscribe()


class LiveFeed:
    """Hält den Stream zum core offen und fragt regelmäßig dessen Health ab."""

    def __init__(
        self,
        client: CoreClient,
        live: LiveState,
        clock: Clock,
        *,
        slot_flows_handlers: Sequence[SlotFlowsHandler] = (),
        health_interval_s: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Backoff | None = None,
    ) -> None:
        self._client = client
        self._live = live
        self._clock = clock
        self._handlers = list(slot_flows_handlers)
        self._health_interval_s = health_interval_s
        self._sleep = sleep
        self._backoff = backoff or Backoff()
        self._status = StatusLog("core")
        self._since: datetime | None = None

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._stream_loop(), name="core-stream")
            tasks.create_task(self._health_loop(), name="core-health")

    def component_status(self) -> ComponentStatus:
        if self._live.connected:
            return ComponentStatus("core", True, "core verbunden", self._since)
        if self._since is None:
            return ComponentStatus("core", False, "core nicht erreichbar")
        local = self._since.astimezone(LOCAL_TZ)
        return ComponentStatus(
            "core", False, f"core nicht erreichbar seit {local:%H:%M}", self._since
        )

    async def _stream_loop(self) -> None:
        while True:
            error = "Stream beendet"
            try:
                async with self._client.stream() as messages:
                    async for message in messages:
                        self._connected()
                        self._backoff.reset()
                        await self._dispatch(message)
            except CoreUnavailableError as exc:
                error = str(exc)
            except Exception as exc:  # jeder Fehler: neu verbinden, die app läuft weiter
                error = f"{type(exc).__name__}: {exc}"
            self._disconnected()
            delay = self._backoff.next()
            self._status.failed(error, delay)
            await self._sleep(delay)

    async def _health_loop(self) -> None:
        while True:
            try:
                self._live.health = await self._client.health()
            except Exception:  # nicht erreichbar: ohne Health weiter
                self._live.health = None
            await self._sleep(self._health_interval_s)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        kind, data = message.get("type"), message.get("data")
        if kind == "state" and isinstance(data, dict):
            self._live.state = data
            self._live.updated_at = self._clock.now()
            self._live.publish(message)
        elif kind == "alarm" and isinstance(data, list):
            self._live.alarms = data
            self._live.publish(message)
        elif kind == "slot_flows" and isinstance(data, dict):
            for handler in self._handlers:
                try:
                    await handler(data)
                except Exception:  # ein Empfänger darf den Stream nicht abbrechen
                    log.exception("Verarbeitung von slot_flows fehlgeschlagen")

    def _connected(self) -> None:
        if self._live.connected:
            return
        self._live.connected = True
        self._since = self._clock.now()
        self._status.connected()
        self._live.publish({"type": "core", "data": {"connected": True}})

    def _disconnected(self) -> None:
        if not self._live.connected:
            return
        self._live.connected = False
        self._since = self._clock.now()
        self._live.publish({"type": "core", "data": {"connected": False}})
