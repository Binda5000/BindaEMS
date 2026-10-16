"""Benachrichtigungen in Home Assistant (Plan 2d, Präzisierung 14).

Die app meldet Alarme der Schwere ``error``, die ``NOTIFY_AFTER_S`` ohne Unterbrechung bestehen,
und abgelehnte Befehle aus Home Assistant. Sie ruft dafür nur ``persistent_notification.create``,
``persistent_notification.dismiss`` und den eingestellten ``notify.*``-Dienst auf (je Aufruf eine
WebSocket-Verbindung). Der core ruft weiter nie Dienste in Home Assistant auf. Kein Fehler eines
Dienstaufrufs verlässt den Notifier oder die Alarmprüfung: er wird protokolliert, der Lauf geht
weiter.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from pydantic import SecretStr
from websockets.asyncio.client import connect as ws_connect

from bindaems.app.core_link import LiveState
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

NOTIFY_AFTER_S: Final = 30.0  # so lange besteht ein Fehler-Alarm, bevor er gemeldet wird
MAX_PER_MINUTE: Final = 10  # höchstens so viele Meldungen je Minute; der Rest geht ins Log
CALL_TIMEOUT_S: Final = 10.0
CHECK_INTERVAL_S: Final = 1.0
TITLE: Final = "BindaEMS"
REJECTION_ID: Final = "bindaems_ha_command"
_WINDOW: Final = timedelta(minutes=1)

# Domain, Dienst, Daten
HaServiceCall = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def _websocket_url(url: str) -> str:
    """``http(s)://host[:port]`` → ``ws(s)://host[:port]/api/websocket``."""
    return "ws" + url.rstrip("/").removeprefix("http") + "/api/websocket"


def websocket_service_call(
    url: str, token: SecretStr, *, timeout_s: float = CALL_TIMEOUT_S
) -> HaServiceCall:
    """Dienstaufruf über die WebSocket-API: je Aufruf verbinden, ``auth``, ``call_service``,
    Antwort prüfen, schließen. Fehler und Zeitüberschreitung (``timeout_s``) lösen eine
    Ausnahme aus."""
    target = _websocket_url(url)

    async def recv(ws: Any) -> dict[str, Any]:
        data = json.loads(await ws.recv())
        if not isinstance(data, dict):
            raise ConnectionError("Home Assistant: JSON-Objekt erwartet")
        return data

    async def call(domain: str, service: str, data: dict[str, Any]) -> None:
        async with asyncio.timeout(timeout_s), ws_connect(target) as ws:
            greeting = await recv(ws)
            if greeting.get("type") != "auth_required":
                raise ConnectionError(f"unerwartete Begrüßung: {greeting.get('type')}")
            await ws.send(json.dumps({"type": "auth", "access_token": token.get_secret_value()}))
            answer = await recv(ws)
            if answer.get("type") == "auth_invalid":
                raise ConnectionError("HA-Token ungültig")
            if answer.get("type") != "auth_ok":
                raise ConnectionError(f"unerwartete Antwort auf auth: {answer.get('type')}")
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "call_service",
                        "domain": domain,
                        "service": service,
                        "service_data": data,
                    }
                )
            )
            while True:
                reply = await recv(ws)
                if reply.get("id") == 1 and reply.get("type") == "result":
                    break
            if not reply.get("success", False):
                error = reply.get("error")
                code = error.get("code") if isinstance(error, dict) else error
                raise ConnectionError(f"{domain}.{service} fehlgeschlagen: {code}")

    return call


def _notification_id(alarm_id: str) -> str:
    return "bindaems_" + alarm_id.replace(".", "_")


class HaNotifier:
    """Sendet Meldungen an Home Assistant; erfüllt ``Rejections`` für die HA-Befehle."""

    def __init__(self, call: HaServiceCall, notify_service: str | None, clock: Clock) -> None:
        self._call = call
        self._notify = notify_service.removeprefix("notify.") if notify_service else None
        self._clock = clock
        self._sent: deque[datetime] = deque()  # Zeitpunkte der Meldungen der letzten Minute
        self._active: set[str] = (
            set()
        )  # Meldungen, die ein Alarm in Home Assistant hinterlassen hat

    async def alarm_raised(self, alarm_id: str, text: str) -> None:
        if not self._allow(text):
            return
        data = {"notification_id": _notification_id(alarm_id), "title": TITLE, "message": text}
        if await self._safe("persistent_notification", "create", data):
            self._active.add(alarm_id)
        if self._notify is not None:
            await self._safe("notify", self._notify, {"title": TITLE, "message": text})

    async def alarm_cleared(self, alarm_id: str) -> None:
        if alarm_id not in self._active:
            return  # nie gemeldet (zu kurz, begrenzt oder fehlgeschlagen): nichts zu entfernen
        self._active.discard(alarm_id)
        await self._safe(
            "persistent_notification", "dismiss", {"notification_id": _notification_id(alarm_id)}
        )

    async def reject(self, text: str) -> None:
        """Abgelehnter Befehl aus Home Assistant: eine Meldung, bei jeder neuen ersetzt."""
        if not self._allow(text):
            return
        data = {"notification_id": REJECTION_ID, "title": TITLE, "message": text}
        await self._safe("persistent_notification", "create", data)

    def _allow(self, text: str) -> bool:
        now = self._clock.now()
        while self._sent and now - self._sent[0] >= _WINDOW:
            self._sent.popleft()
        if len(self._sent) >= MAX_PER_MINUTE:
            log.warning("Benachrichtigung nicht gesendet: zu viele je Minute", text=text)
            return False
        self._sent.append(now)
        return True

    async def _safe(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        try:
            await self._call(domain, service, data)
        except Exception as exc:
            log.warning(
                "Dienstaufruf an Home Assistant fehlgeschlagen",
                service=f"{domain}.{service}",
                error=f"{type(exc).__name__}: {exc}",
            )
            return False
        return True


class AlarmNotifications:
    """Prüft jede Sekunde die Alarme des core und meldet Fehler, die ``NOTIFY_AFTER_S`` bestehen."""

    def __init__(
        self,
        live: LiveState,
        notifier: HaNotifier,
        clock: Clock,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._live = live
        self._notifier = notifier
        self._clock = clock
        self._sleep = sleep
        self._since: dict[str, datetime] = {}  # Fehler-Alarme, seit wann sie ununterbrochen stehen
        self._notified: set[str] = set()

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("Alarme für Benachrichtigungen nicht prüfbar")
            await self._sleep(CHECK_INTERVAL_S)

    async def check(self) -> None:
        now = self._clock.now()
        errors: dict[str, str] = {}
        for alarm in list(self._live.alarms):
            alarm_id = alarm.get("id") if isinstance(alarm, dict) else None
            if isinstance(alarm_id, str) and alarm.get("severity") == "error":
                message = alarm.get("message")
                errors[alarm_id] = message if isinstance(message, str) and message else alarm_id
        for alarm_id in [a for a in self._since if a not in errors]:
            del self._since[alarm_id]
            if alarm_id in self._notified:
                self._notified.discard(alarm_id)
                await self._notifier.alarm_cleared(alarm_id)
        for alarm_id, text in errors.items():
            since = self._since.setdefault(alarm_id, now)
            if alarm_id not in self._notified and (now - since).total_seconds() >= NOTIFY_AFTER_S:
                self._notified.add(alarm_id)  # einmal je Auftreten, auch wenn der Aufruf scheitert
                await self._notifier.alarm_raised(alarm_id, text)
