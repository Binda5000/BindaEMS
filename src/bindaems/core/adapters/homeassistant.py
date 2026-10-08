"""Home Assistant über die WebSocket-API, ausschließlich lesend.

Gesendet werden nur ``auth``, ``subscribe_entities`` und ``ping`` – niemals Dienstaufrufe.
HA meldet nur Änderungen, daher sind alle Werte Zustände (gültig, solange verbunden).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import SecretStr
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect

from bindaems.core.adapters.base import AdapterHealth, Backoff
from bindaems.core.state.store import StateStore
from bindaems.shared.config import HomeAssistantConfig
from bindaems.shared.domain import SignalKind, Value

SOURCE = "ha"
AUTH_RETRY_S = 300.0
SUBSCRIPTION_ID = 1
_MISSING = ("unavailable", "unknown", "")


class HaConnection(Protocol):
    async def send_json(self, msg: dict[str, Any]) -> None: ...

    async def recv_json(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class AuthInvalidError(Exception):
    """Home Assistant hat das Token abgelehnt."""


def websocket_url(url: str) -> str:
    """``http(s)://host[:port]`` → ``ws(s)://host[:port]/api/websocket``."""
    return "ws" + url.rstrip("/").removeprefix("http") + "/api/websocket"


class _WebsocketConnection:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    async def send_json(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg))

    async def recv_json(self) -> dict[str, Any]:
        data = json.loads(await self._ws.recv())
        if not isinstance(data, dict):
            raise ValueError("Home Assistant: JSON-Objekt erwartet")
        return data

    async def close(self) -> None:
        await self._ws.close()


async def connect_ws(url: str) -> HaConnection:
    return _WebsocketConnection(await ws_connect(websocket_url(url)))


def convert_state(state: str) -> Value:
    if state in _MISSING:
        return None
    if state in ("on", "off"):
        return state == "on"
    try:
        number = float(state)
    except ValueError:
        return state
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class HaAdapter:
    name = "ha"

    def __init__(
        self,
        cfg: HomeAssistantConfig,
        token: SecretStr,
        store: StateStore,
        connect: Callable[[str], Awaitable[HaConnection]] = connect_ws,
        *,
        ping_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cfg = cfg
        self._token = token
        self._store = store
        self._connect = connect
        self._ping_s = ping_s
        self._sleep = sleep
        self._health = AdapterHealth(name=self.name)
        self._backoff = Backoff()
        self._signals: dict[str, list[str]] = {}
        for signal, entity_id in cfg.entities.items():
            self._signals.setdefault(entity_id, []).append(signal)
        self._authenticated = False
        store.register_source(SOURCE, None)

    def health(self) -> AdapterHealth:
        return replace(self._health)

    async def run(self) -> None:
        while True:
            self._authenticated = False
            delay: float | None = None
            try:
                await self._session()
                error = "Verbindung beendet"
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except AuthInvalidError:
                error, delay = "HA-Token ungültig", AUTH_RETRY_S
            except Exception as exc:  # jeder Verbindungsfehler führt zur Wiederverbindung
                error = f"{type(exc).__name__}: {exc}"
            self._set_connected(False)
            self._health.last_error = error
            self._health.error_count += 1
            if delay is None:
                if self._authenticated:
                    self._backoff.reset()
                delay = self._backoff.next()
            await self._sleep(delay)

    async def _session(self) -> None:
        conn = await self._connect(self._cfg.url)
        try:
            await self._authenticate(conn)
            await conn.send_json(
                {
                    "id": SUBSCRIPTION_ID,
                    "type": "subscribe_entities",
                    "entity_ids": sorted(self._signals),
                }
            )
            self._set_connected(True)
            ping = asyncio.create_task(self._ping(conn))
            try:
                while True:
                    self._handle(await conn.recv_json())
            finally:
                ping.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await ping
        finally:
            with contextlib.suppress(Exception):
                await conn.close()

    async def _authenticate(self, conn: HaConnection) -> None:
        greeting = await conn.recv_json()
        if greeting.get("type") != "auth_required":
            raise ConnectionError(f"unerwartete Begrüßung: {greeting.get('type')}")
        await conn.send_json({"type": "auth", "access_token": self._token.get_secret_value()})
        answer = await conn.recv_json()
        if answer.get("type") == "auth_invalid":
            raise AuthInvalidError
        if answer.get("type") != "auth_ok":
            raise ConnectionError(f"unerwartete Antwort auf auth: {answer.get('type')}")
        self._authenticated = True

    async def _ping(self, conn: HaConnection) -> None:
        message_id = SUBSCRIPTION_ID
        while True:
            await asyncio.sleep(self._ping_s)
            message_id += 1
            await conn.send_json({"id": message_id, "type": "ping"})

    def _handle(self, msg: dict[str, Any]) -> None:
        if msg.get("id") != SUBSCRIPTION_ID:
            return  # z. B. pong
        if msg.get("type") == "result" and not msg.get("success", False):
            raise ConnectionError(f"subscribe_entities fehlgeschlagen: {msg.get('error')}")
        if msg.get("type") != "event":
            return
        event = _mapping(msg.get("event"))
        for entity_id, data in _mapping(event.get("a")).items():
            self._set_entity(entity_id, _mapping(data).get("s"))
        for entity_id, diff in _mapping(event.get("c")).items():
            added = _mapping(_mapping(diff).get("+"))
            if "s" in added:
                self._set_entity(entity_id, added["s"])
        removed = event.get("r")
        for entity_id in removed if isinstance(removed, list) else []:
            self._set_entity(entity_id, None)
        self._health.last_ok = datetime.now(UTC)

    def _set_entity(self, entity_id: Any, state: Any) -> None:
        value = convert_state(state) if isinstance(state, str) else None
        for signal in self._signals.get(entity_id, []) if isinstance(entity_id, str) else []:
            self._store.update(signal, value, source=SOURCE, kind=SignalKind.STATE)

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(SOURCE, connected)
        self._health.connected = connected
