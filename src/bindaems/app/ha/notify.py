"""Benachrichtigungen in Home Assistant (Plan 2d, Präzisierung 14; Plan 4b, Entscheidung 14).

Die app meldet Alarme der Schwere ``error`` und die Warnungen der ``binary_sensor`` in Home
Assistant, die ``NOTIFY_AFTER_S`` ohne Unterbrechung bestehen, und abgelehnte Befehle aus Home
Assistant. Alle teilen ein Budget je Minute. Sie ruft dafür nur ``persistent_notification.create``,
``persistent_notification.dismiss`` und den eingestellten ``notify.*``-Dienst auf (je Aufruf eine
WebSocket-Verbindung). Der core ruft weiter nie Dienste in Home Assistant auf. Kein Fehler eines
Dienstaufrufs verlässt den Notifier oder die Alarmprüfung: er wird protokolliert, der Lauf geht
weiter.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from pydantic import SecretStr
from websockets.asyncio.client import connect as ws_connect

from bindaems.app.core_link import LiveState
from bindaems.app.ha.entities import UNKNOWN, HaInputs, build_entities, object_id, planned_view
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

NOTIFY_AFTER_S: Final = 30.0  # so lange besteht ein Fehler-Alarm, bevor er gemeldet wird
MAX_PER_MINUTE: Final = 10  # höchstens so viele Meldungen je Minute; der Rest geht ins Log
MAX_REJECTIONS_PER_MINUTE: Final = 4  # davon höchstens so viele für abgelehnte Befehle
RETRY_AFTER_S: Final = 60.0  # Abstand der Versuche je Alarm, bis Melden oder Entfernen gelingt
CALL_TIMEOUT_S: Final = 10.0
CHECK_INTERVAL_S: Final = 1.0
TITLE: Final = "BindaEMS"
REJECTION_ID: Final = "bindaems_ha_command"
WARNING_PREFIX: Final = "bindaems_warning_"  # ``notification_id`` einer Warnung: Präfix + Objekt-ID
# Warnungen am Gerät „BindaEMS“ (Objekt-ID → Text); „Watchdog ausgelöst“ meldet schon der Alarm
WARNING_TEXTS: Final = {
    "problem_competitor": "Mitregelndes System erkannt.",
    "problem_prices": "Preise fehlen.",
    "problem_device_offline": "Gerät offline: {names}.",
    "problem_assignment_unclear": "Zuordnung Auto und Wallbox unklar.",
}
COMPETITOR_WARNING: Final = "problem_competitor"
COMPETITOR_ALARMS: Final = "competitor."  # solange ein solcher Fehler besteht, schweigt sie
TARGET_SUFFIX: Final = "_target"  # je Fahrzeug: ``bindaems_warning_<Objekt-ID>_target``
TARGET_TEXT: Final = (
    "Ziel-SOC unerreichbar: {name} erreicht bis {hhmm} voraussichtlich nur {soc} %."
)
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
    """Sendet Meldungen an Home Assistant; erfüllt ``Rejections`` für die HA-Befehle.

    Höchstens ``MAX_PER_MINUTE`` Meldungen je Minute über Alarme, Warnungen und Ablehnungen,
    davon höchstens ``MAX_REJECTIONS_PER_MINUTE`` für abgelehnte Befehle: Den Alarmen und
    Warnungen bleiben so immer mindestens sechs, auch wenn eine Automation eine Flut ungültiger
    Befehle sendet.
    """

    def __init__(self, call: HaServiceCall, notify_service: str | None, clock: Clock) -> None:
        self._call = call
        self._notify = notify_service.removeprefix("notify.") if notify_service else None
        self._clock = clock
        self._sent: deque[datetime] = deque()  # Zeitpunkte der Meldungen der letzten Minute
        self._rejected: deque[datetime] = deque()  # davon die der abgelehnten Befehle
        self._active: set[str] = set()  # ``notification_id``, die in Home Assistant stehen
        self._alarms: set[str] = set()  # die Alarme, deren Meldung in Home Assistant steht

    async def alarm_raised(self, alarm_id: str, text: str) -> bool:
        """Meldet den Alarm; sagt, ob die persistente Meldung angelegt ist."""
        if not await self._raise(_notification_id(alarm_id), text):
            return False
        self._alarms.add(alarm_id)
        return True

    async def alarm_cleared(self, alarm_id: str) -> bool:
        """Entfernt die Meldung des Alarms; sagt, ob nichts mehr zu entfernen bleibt."""
        if not await self._clear(_notification_id(alarm_id)):
            return False
        self._alarms.discard(alarm_id)
        return True

    def alarm_notified(self, prefix: str) -> bool:
        """Steht die Meldung eines Alarms, dessen ID mit ``prefix`` beginnt?"""
        return any(alarm_id.startswith(prefix) for alarm_id in self._alarms)

    async def warning_raised(self, warning_id: str, text: str) -> bool:
        """Meldet die Warnung (``notification_id`` ``bindaems_warning_<warning_id>``); sagt, ob die
        persistente Meldung angelegt ist."""
        return await self._raise(WARNING_PREFIX + warning_id, text)

    async def warning_cleared(self, warning_id: str) -> bool:
        """Entfernt die Meldung der Warnung; sagt, ob nichts mehr zu entfernen bleibt."""
        return await self._clear(WARNING_PREFIX + warning_id)

    async def _raise(self, notification_id: str, text: str) -> bool:
        """Legt die persistente Meldung an; sagt, ob sie angelegt ist.

        Erst danach geht der ``notify``-Dienst hinaus, einmal: Ein neuer Versuch nach einem
        Fehlschlag oder über dem Budget schickt ihn so nicht mehrfach aufs Telefon.
        """
        if not self._allow(text):
            return False
        data = {"notification_id": notification_id, "title": TITLE, "message": text}
        if not await self._safe("persistent_notification", "create", data):
            return False
        self._active.add(notification_id)
        if self._notify is not None:
            await self._safe("notify", self._notify, {"title": TITLE, "message": text})
        return True

    async def _clear(self, notification_id: str) -> bool:
        if notification_id not in self._active:
            return True  # nie gemeldet (zu kurz, begrenzt oder fehlgeschlagen): nichts zu entfernen
        data = {"notification_id": notification_id}
        if not await self._safe("persistent_notification", "dismiss", data):
            return False
        self._active.discard(notification_id)
        return True

    async def reject(self, text: str) -> None:
        """Abgelehnter Befehl aus Home Assistant: eine Meldung, bei jeder neuen ersetzt."""
        if not self._allow(text, rejection=True):
            return
        data = {"notification_id": REJECTION_ID, "title": TITLE, "message": text}
        await self._safe("persistent_notification", "create", data)

    def _allow(self, text: str, *, rejection: bool = False) -> bool:
        now = self._clock.now()
        for sent in (self._sent, self._rejected):
            while sent and now - sent[0] >= _WINDOW:
                sent.popleft()
        full = len(self._sent) >= MAX_PER_MINUTE
        if full or (rejection and len(self._rejected) >= MAX_REJECTIONS_PER_MINUTE):
            log.warning("Benachrichtigung nicht gesendet: zu viele je Minute", text=text)
            return False
        self._sent.append(now)
        if rejection:
            self._rejected.append(now)
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


class _Pending:
    """Meldet Einträge, die ``NOTIFY_AFTER_S`` ohne Unterbrechung bestehen, einmal, und entfernt
    die Meldung, wenn sie verschwinden.

    Ein Eintrag gilt erst als gemeldet, wenn ``raised`` gelungen ist. Bis dahin versucht es
    ``update`` erneut, solange er besteht, je Eintrag höchstens alle ``RETRY_AFTER_S`` (über dem
    Budget, oder Home Assistant startet gerade neu). Ebenso wird ein gescheitertes Entfernen
    wiederholt, bis es gelingt oder der Eintrag wiederkommt.
    """

    def __init__(
        self,
        clock: Clock,
        raised: Callable[[str, str], Awaitable[bool]],
        cleared: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._clock = clock
        self._raised = raised
        self._cleared = cleared
        self._since: dict[str, datetime] = {}  # bestehende Einträge, seit wann ununterbrochen
        self._notified: set[str] = set()
        self._retry_at: dict[str, datetime] = {}  # nicht gemeldete Einträge: nächster Versuch
        self._dismiss_at: dict[str, datetime] = {}  # nicht entfernte Meldungen: nächster Versuch

    async def update(self, present: Mapping[str, str], held: Collection[str] = ()) -> None:
        """``present``: die bestehenden Einträge mit ihrem Text. ``held``: Einträge, deren Stand
        unbekannt ist; sie zählen nicht weiter, eine stehende Meldung bleibt aber stehen."""
        now = self._clock.now()
        retry_at = now + timedelta(seconds=RETRY_AFTER_S)
        known = [*self._since, *sorted(self._notified - self._since.keys())]
        for item in [item for item in known if item not in present]:
            self._since.pop(item, None)
            self._retry_at.pop(item, None)
            if item in self._notified and item not in held:
                self._notified.discard(item)
                self._dismiss_at[item] = now
        for item, due in list(self._dismiss_at.items()):
            if item in present:  # wieder da: die neue Meldung ersetzt die alte
                del self._dismiss_at[item]
            elif now >= due:
                if await self._cleared(item):
                    del self._dismiss_at[item]
                else:
                    self._dismiss_at[item] = retry_at
        for item, text in present.items():
            since = self._since.setdefault(item, now)
            if item in self._notified or (now - since).total_seconds() < NOTIFY_AFTER_S:
                continue
            if item in self._retry_at and now < self._retry_at[item]:
                continue
            if await self._raised(item, text):
                self._notified.add(item)
                self._retry_at.pop(item, None)
            else:
                self._retry_at[item] = retry_at


class AlarmNotifications:
    """Prüft jede Sekunde die Alarme des core und meldet Fehler, die ``NOTIFY_AFTER_S`` bestehen
    (``_Pending``)."""

    def __init__(
        self,
        live: LiveState,
        notifier: HaNotifier,
        clock: Clock,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._live = live
        self._sleep = sleep
        self._pending = _Pending(clock, notifier.alarm_raised, notifier.alarm_cleared)

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("Alarme für Benachrichtigungen nicht prüfbar")
            await self._sleep(CHECK_INTERVAL_S)

    async def check(self) -> None:
        await self._pending.update(error_alarms(self._live))


def error_alarms(live: LiveState) -> dict[str, str]:
    """Die Alarme des core mit der Schwere ``error``: ID → Text (ohne Text die ID)."""
    errors: dict[str, str] = {}
    for alarm in list(live.alarms):
        alarm_id = alarm.get("id") if isinstance(alarm, dict) else None
        if isinstance(alarm_id, str) and alarm.get("severity") == "error":
            message = alarm.get("message")
            errors[alarm_id] = message if isinstance(message, str) and message else alarm_id
    return errors


def _whole(value: float) -> str:
    """Ganze Zahl, Hälften runden auf (63,5 → 64)."""
    return str(math.floor(value + 0.5))


class WarningNotifications:
    """Prüft jede Sekunde die Warnungen in Home Assistant und meldet die, die ``NOTIFY_AFTER_S``
    ohne Unterbrechung bestehen, einmal (Plan 4b, Entscheidung 14).

    - Am Gerät „BindaEMS“: die ``binary_sensor`` aus ``WARNING_TEXTS`` (Zustand ``ON``) mit der
      ``notification_id`` ``bindaems_warning_<Objekt-ID>``. „Watchdog ausgelöst“ meldet schon der
      Alarm, „Ziel-SOC unerreichbar“ meldet sich je Fahrzeug.
    - Je Fahrzeug in „Geplant“, dessen Ziel nicht erreichbar ist (``planned_view``, wie
      ``problem_target_unreachable``): ``bindaems_warning_<Objekt-ID>_target``. Ein neuer Plan, der
      das Ziel weiter verfehlt, meldet nichts Neues.
    - „Mitregelndes System“ schweigt, solange ein Fehler ``competitor.*`` besteht (in den Alarmen
      des core, ``live``) oder gemeldet ist, auch bevor der Alarm selbst gemeldet ist: Sonst kämen
      beide Meldungen, wenn sie im selben Takt fällig werden. Steht ihre Meldung schon, entfernt
      die app sie, sobald der Fehler besteht.
    - Ein unbekannter Zustand (``unknown``, etwa ohne core) unterbricht das Zählen, entfernt aber
      keine stehende Meldung; ebenso ein unerreichbares Ziel ohne erwarteten Ladestand.

    Das Budget je Minute teilen die Warnungen mit den Alarmen (``HaNotifier``).
    """

    def __init__(
        self,
        cfg: Config,
        inputs: Callable[[], HaInputs],
        notifier: HaNotifier,
        clock: Clock,
        *,
        live: LiveState,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cfg = cfg
        self._inputs = inputs
        self._notifier = notifier
        self._live = live
        self._sleep = sleep
        specs = {spec.object_id: spec for spec in build_entities(cfg)}
        self._specs = {oid: specs[oid] for oid in WARNING_TEXTS}
        self._target = specs["problem_target_unreachable"]
        self._pending = _Pending(clock, notifier.warning_raised, notifier.warning_cleared)

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("Warnungen für Benachrichtigungen nicht prüfbar")
            await self._sleep(CHECK_INTERVAL_S)

    async def check(self) -> None:
        inputs = self._inputs()
        present: dict[str, str] = {}
        held: list[str] = []
        for oid, spec in self._specs.items():
            state = spec.value(inputs)
            if state == UNKNOWN:
                held.append(oid)
            elif state == "ON":
                names = ", ".join(inputs.adapters_offline or [])
                present[oid] = WARNING_TEXTS[oid].format(names=names)
        if COMPETITOR_WARNING in present and self._competitor_error():
            del present[COMPETITOR_WARNING]
        unknown = self._target.value(inputs) == UNKNOWN
        for key, vehicle in self._cfg.vehicles.items():
            warning_id = object_id(key) + TARGET_SUFFIX
            view = None if unknown else planned_view(self._cfg, key, inputs)
            expected = None if view is None or view.reachable else view.expected_soc_pct
            if unknown or (view is not None and not view.reachable and expected is None):
                held.append(warning_id)
            elif view is not None and expected is not None:
                present[warning_id] = TARGET_TEXT.format(
                    name=vehicle.name,
                    hhmm=view.departure_at.astimezone(LOCAL_TZ).strftime("%H:%M"),
                    soc=_whole(expected),
                )
        await self._pending.update(present, held)

    def _competitor_error(self) -> bool:
        """Besteht ein Fehler ``competitor.*`` oder steht seine Meldung noch?"""
        present = any(alarm.startswith(COMPETITOR_ALARMS) for alarm in error_alarms(self._live))
        return present or self._notifier.alarm_notified(COMPETITOR_ALARMS)
