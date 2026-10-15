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
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from pydantic import SecretStr, ValidationError
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from bindaems.app.status import ComponentStatus
from bindaems.shared.broadcast import Broadcast
from bindaems.shared.control import Intent
from bindaems.shared.decisions import Decision, decision_from_json
from bindaems.shared.domain import Value
from bindaems.shared.retry import Backoff, StatusLog
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

HEALTH_TIMEOUT_S = 5.0
OPEN_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 5.0  # Absicht, Entscheidungen und Quittierung

SlotFlowsHandler = Callable[[dict[str, Any]], Awaitable[None]]
DecisionHandler = Callable[[Decision], Awaitable[None]]
ModeHandler = Callable[[dict[str, Any]], Awaitable[None]]
ConnectHandler = Callable[[], Awaitable[None]]


class CoreUnavailableError(Exception):
    """Der core ist nicht erreichbar oder lehnt die Verbindung ab."""


@dataclass(frozen=True)
class CoreReply:
    """Antwort des core mit HTTP-Status und JSON-Objekt, auch bei den erwarteten Fehlerstatus."""

    status: int
    body: dict[str, Any]


class CoreClient:
    def __init__(self, base_url: str, token: SecretStr, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._http = http

    def _headers(self) -> dict[str, str]:
        # nur im Header: ein Token in der URL landete in Zugriffslogs
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def health(self) -> dict[str, Any]:
        reply = await self._call("GET", "/v1/health", frozenset({200}), timeout_s=HEALTH_TIMEOUT_S)
        return reply.body

    async def _call(
        self,
        method: str,
        path: str,
        expected: frozenset[int],
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> CoreReply:
        """Ruft den core auf; nur die ``expected`` Status kehren als Antwort zurück.

        Netzfehler, Zeitüberschreitung, ein anderer Status (etwa 401 oder 500) und eine Antwort,
        die kein JSON-Objekt ist, sind ``CoreUnavailableError``.
        """
        try:
            response = await self._http.request(
                method,
                f"{self._base}{path}",
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=timeout_s,
            )
        except httpx.HTTPError as exc:
            raise CoreUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code not in expected:
            raise CoreUnavailableError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise CoreUnavailableError("Antwort ist kein JSON") from exc
        if not isinstance(data, dict):
            raise CoreUnavailableError("Antwort ist kein JSON-Objekt")
        return CoreReply(response.status_code, data)

    async def intent(self) -> Intent | None:
        """Die gültige Absicht des core; ``None``, wenn er keine hat."""
        reply = await self._call("GET", "/v1/intent", frozenset({200}))
        raw = reply.body.get("intent")
        if raw is None:
            return None
        try:
            return Intent.model_validate(raw)
        except ValidationError as exc:
            raise CoreUnavailableError("Absicht des core ist ungültig") from exc

    async def submit_intent(self, intent: Intent) -> CoreReply:
        """Sendet die Absicht: 200 (angenommen, auch geklemmt), 409 (veraltet) oder 422."""
        return await self._call(
            "PUT",
            "/v1/intent",
            frozenset({200, 409, 422}),
            json_body=intent.model_dump(mode="json"),
        )

    async def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        """Die Entscheidungen mit ``ts`` nach ``since`` (aufsteigend) und ob weitere folgen."""
        # ``params`` kodiert das ``+`` der Zeitzone als ``%2B``
        reply = await self._call(
            "GET",
            "/v1/decisions",
            frozenset({200}),
            params={"since": since.isoformat(), "limit": limit},
        )
        raw, more = reply.body.get("decisions"), reply.body.get("more")
        if not isinstance(raw, list) or not isinstance(more, bool):
            raise CoreUnavailableError("Antwort enthält keine Entscheidungen")
        try:
            return [decision_from_json(item) for item in raw], more
        except ValueError as exc:
            raise CoreUnavailableError(str(exc)) from exc

    async def acknowledge(self, actuator: str) -> CoreReply:
        """Quittiert die Sperre eines Aktors: 202, 409 (keine Sperre, Ursache) oder 404."""
        return await self._call(
            "POST", f"/v1/locks/{quote(actuator, safe='')}/ack", frozenset({202, 404, 409})
        )

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


async def _run_handlers(
    handlers: Sequence[Callable[..., Awaitable[None]]], what: str, *args: Any
) -> None:
    """Ruft jeden Handler mit ``args``; ein Fehler kommt ins Log und bricht den Stream nicht ab."""
    for handler in handlers:
        try:
            await handler(*args)
        except Exception:
            log.exception("Verarbeitung fehlgeschlagen", message=what)


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
        decision_handlers: Sequence[DecisionHandler] = (),
        mode_handlers: Sequence[ModeHandler] = (),
        connect_handlers: Sequence[ConnectHandler] = (),
        health_interval_s: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Backoff | None = None,
    ) -> None:
        self._client = client
        self._live = live
        self._clock = clock
        self._handlers = list(slot_flows_handlers)
        self._decision_handlers = list(decision_handlers)
        self._mode_handlers = list(mode_handlers)
        self._connect_handlers = list(connect_handlers)
        self._health_now = asyncio.Event()
        self._health_interval_s = health_interval_s
        self._sleep = sleep
        self._backoff = backoff or Backoff()
        self._status = StatusLog("core")
        self._since: datetime | None = None

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._stream_loop(), name="core-stream")
            tasks.create_task(self._health_loop(), name="core-health")

    def refresh_health(self) -> None:
        """Die nächste Health-Abfrage folgt sofort, ohne den Takt abzuwarten."""
        self._health_now.set()

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
                        if not self._live.connected:
                            self._connected()
                            await self._run_connect_handlers()
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
            # vor der Abfrage zurücksetzen: ein Wunsch während der Abfrage löst die nächste aus
            self._health_now.clear()
            try:
                self._live.health = await self._client.health()
            except Exception:  # nicht erreichbar: ohne Health weiter
                self._live.health = None
            await self._wait_for_next_health()

    async def _wait_for_next_health(self) -> None:
        """Wartet den Takt ab oder bis ``refresh_health`` gerufen wird, was zuerst eintritt."""
        timer = asyncio.ensure_future(self._sleep(self._health_interval_s))
        wished = asyncio.ensure_future(self._health_now.wait())
        try:
            await asyncio.wait({timer, wished}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (timer, wished):
                task.cancel()
            await asyncio.gather(timer, wished, return_exceptions=True)

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
            await _run_handlers(self._handlers, "slot_flows", data)
        elif kind == "decision":
            await self._dispatch_decision(data)
        elif kind == "mode_change" and isinstance(data, dict):
            self.refresh_health()
            await _run_handlers(self._mode_handlers, "mode_change", data)

    async def _dispatch_decision(self, data: Any) -> None:
        try:
            decision = decision_from_json(data)
        except ValueError as exc:  # ein ungültiges Objekt wird übergangen, der Stream läuft weiter
            log.warning("Entscheidung vom core ungültig", error=str(exc))
            return
        await _run_handlers(self._decision_handlers, "decision", decision)

    async def _run_connect_handlers(self) -> None:
        """Ruft nach dem Verbindungsaufbau jeden Handler (etwa den Rückstand der Entscheidungen).

        Ein Fehler kommt ins Log. Der Stream wird erst danach weiter gelesen; die Nachrichten
        dazwischen puffert die Verbindung.
        """
        await _run_handlers(self._connect_handlers, "connect")

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
