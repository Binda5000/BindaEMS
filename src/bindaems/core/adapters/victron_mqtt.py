"""Victron-Cerbo-Adapter über MQTT (dbus-flashmq): liest alles, schreibt nur freigegebene Pfade.

Publiziert werden der Keepalive und die Pfade aus ``writable_paths(cfg)``.

Der Keepalive geht auf ``R/<portal>/keepalive``: nach jedem Verbindungsaufbau einmal leer
(vollständige Neuveröffentlichung aller Werte), danach im Takt ``keepalive_s`` mit
``suppress-republish``.

Geschrieben wird nur über ``VictronMqttAdapter.write``, und zwar auf die Pfade aus
``writable_paths``: den Heartbeat des Totmann-Watchdogs (``W/<portal>/bindaems/0/Heartbeat``),
den Override des Netz-Sollwerts, die beiden persistenten ESS-Einstellungen und je
``evcharger``-Instanz der Konfiguration Sollstrom und Laden an/aus. Der Auftrag geht in die
Warteschlange der laufenden Sitzung, deren Sender-Task ihn veröffentlicht. Mit der Sitzung
verfällt die Warteschlange; ein veralteter Auftrag erreicht also nie eine neue Verbindung.

Eine Sitzung besteht aus drei Tasks: Nachrichtenschleife, Keepalive und Sender. Endet der
Keepalive oder der Sender, mit oder ohne Fehler, endet die Sitzung mit einem ``ConnectionError``,
und ``run`` verbindet nach dem Backoff neu. Sonst bliebe ein toter Sender unbemerkt, während
``write`` weiter ``True`` meldet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal, Protocol

import aiomqtt
from pydantic import SecretStr

from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.state.store import StateStore
from bindaems.shared.config import MqttConfig, VictronConfig
from bindaems.shared.mqtt import build_tls_context
from bindaems.shared.retry import Backoff, StatusLog

SOURCE = "victron"
FRESHNESS_S = 5.0
DISCOVERY_TIMEOUT_S = 10.0
KEEPALIVE_SUPPRESS = b'{"keepalive-options": ["suppress-republish"]}'
SUBSCRIPTIONS = (
    "system/0/#",
    "grid/+/#",
    "pvinverter/+/#",
    "acload/+/#",
    "vebus/+/#",
    "battery/+/#",
    "evcharger/+/#",
    "hub4/0/#",
    "settings/0/Settings/CGwacs/#",
    "settings/0/Settings/DynamicEss/#",
    "settings/0/Settings/SystemSetup/#",
    "bindaems/0/#",
)
# Die Schreibwege des EMS auf den Cerbo (Spec 6.2, 6.3, 6.5, 8.6), abschließend; alles andere
# bleibt lesend. Die EVCS-Pfade je Instanz der Konfiguration liefert ``writable_paths``.
# Heartbeat: Zähler des Watchdogs, 0 = Freigabe. Netz-Sollwert: flüchtiger Override in W, ``None``
# hebt ihn auf. Entladegrenze (W, −1 = unbegrenzt) und Min-SOC (%) sind persistente Einstellungen.
HEARTBEAT_PATH = "bindaems/0/Heartbeat"
SETPOINT_OVERRIDE_PATH = "hub4/0/Overrides/Setpoint"
MAX_DISCHARGE_PATH = "settings/0/Settings/CGwacs/MaxDischargePower"
MIN_SOC_PATH = "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"
OUTBOX_SIZE = 100

_Outbox = asyncio.Queue[tuple[str, bytes]]  # (Topic, Payload) der Schreibaufträge einer Sitzung


def evcharger_path(instance: int, name: Literal["SetCurrent", "StartStop"]) -> str:
    """Schreibpfad des GX-Dienstes ``evcharger``: Sollstrom (A) oder Laden an/aus (1/0)."""
    return f"evcharger/{instance}/{name}"


def writable_paths(cfg: VictronConfig) -> frozenset[str]:
    """Alle Pfade, auf die das EMS auf dem Cerbo schreiben darf, abgeleitet aus der Konfiguration.

    Das sind der Heartbeat, der Override des Netz-Sollwerts, die Entladegrenze, der Min-SOC und
    je Instanz in ``victron.instances.evcharger`` Sollstrom und Laden an/aus. Ohne Instanz gibt
    es keinen EVCS-Pfad; die EVCS ist dann nicht steuerbar.
    """
    paths = {HEARTBEAT_PATH, SETPOINT_OVERRIDE_PATH, MAX_DISCHARGE_PATH, MIN_SOC_PATH}
    for instance in cfg.instances.evcharger:
        paths.add(evcharger_path(instance, "SetCurrent"))
        paths.add(evcharger_path(instance, "StartStop"))
    return frozenset(paths)


def _ending(task: asyncio.Task[None]) -> str:
    """Wie ein beendeter Hilfs-Task geendet hat; für den Fehler, der die Sitzung beendet."""
    if task.cancelled():
        return "abgebrochen"
    error = task.exception()
    return "ohne Fehler" if error is None else f"{type(error).__name__}: {error}"


@dataclass(frozen=True)
class MqttMessage:
    topic: str
    payload: bytes


class MqttTransport(Protocol):
    async def __aenter__(self) -> MqttTransport: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def subscribe(self, topic: str) -> None: ...

    async def publish(self, topic: str, payload: bytes) -> None: ...

    def messages(self) -> AsyncIterator[MqttMessage]: ...


def _payload_bytes(payload: object) -> bytes:
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    if payload is None:
        return b""
    return str(payload).encode()


class AiomqttTransport:
    """Echte MQTT-Verbindung mit aiomqtt."""

    def __init__(self, cfg: MqttConfig, password: SecretStr | None) -> None:
        insecure = True if cfg.tls and not cfg.tls_verify else None
        self._client = aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=password.get_secret_value() if password is not None else None,
            identifier=f"bindaems-core-{secrets.token_hex(4)}",
            tls_context=build_tls_context(cfg),
            tls_insecure=insecure,
        )

    async def __aenter__(self) -> AiomqttTransport:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def subscribe(self, topic: str) -> None:
        await self._client.subscribe(topic)

    async def publish(self, topic: str, payload: bytes) -> None:
        await self._client.publish(topic, payload)

    async def messages(self) -> AsyncIterator[MqttMessage]:
        async for message in self._client.messages:
            yield MqttMessage(str(message.topic), _payload_bytes(message.payload))


class VictronMqttAdapter:
    """Liest alle relevanten Cerbo-Werte per MQTT in den Zustandsspeicher.

    Schreiben geht nur über ``write`` und nur auf Pfade aus ``writable_paths(cfg)``.
    """

    name = "victron"

    def __init__(
        self,
        cfg: VictronConfig,
        password: SecretStr | None,
        store: StateStore,
        transport_factory: Callable[[], MqttTransport],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cfg = cfg
        self._writable = writable_paths(cfg)
        self._password = password
        self._store = store
        self._transport_factory = transport_factory
        self._sleep = sleep
        self._resolver = InstanceResolver(cfg.instances)
        self._health = AdapterHealth(name=self.name)
        self._status = StatusLog(self.name)
        self._backoff = Backoff()
        self._session_messages = 0
        self._outbox: _Outbox | None = None  # nur während einer Sitzung
        self.portal_id: str | None = cfg.mqtt.portal_id
        # Der Cerbo sendet nur Änderungen: frisch ist, was er sendet, solange er überhaupt sendet.
        store.register_source(
            SOURCE, FRESHNESS_S, activity_based=True, invalidate_on_reconnect=True
        )

    def health(self) -> AdapterHealth:
        return replace(self._health)

    def write(self, path: str, value: float | int | None) -> bool:
        """Stellt einen Schreibauftrag in die Warteschlange der laufenden Sitzung.

        Erlaubt sind nur Pfade aus ``writable_paths(cfg)``; jeder andere ergibt einen
        ``ValueError``. Ohne laufende Sitzung oder bei voller Warteschlange liefert es ``False``
        und merkt sich nichts; der Aufrufer versucht es später erneut. ``True`` heißt: eingereiht,
        nicht zugestellt. Das Veröffentlichen übernimmt der Sender-Task der Sitzung; scheitert er,
        endet die Sitzung. Aufruf nur aus dem Event-Loop.
        """
        if path not in self._writable:
            raise ValueError(f"Schreibpfad nicht freigegeben: {path}")
        outbox, portal = self._outbox, self.portal_id
        if outbox is None or portal is None:
            return False
        try:
            outbox.put_nowait((f"W/{portal}/{path}", json.dumps({"value": value}).encode()))
        except asyncio.QueueFull:
            return False
        return True

    async def run(self) -> None:
        while True:
            self._session_messages = 0
            try:
                await self._session()
                error = "Verbindung beendet"
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except Exception as exc:  # jeder Verbindungsfehler führt zur Wiederverbindung
                error = f"{type(exc).__name__}: {exc}"
            self._set_connected(False)
            self._health.last_error = error
            self._health.error_count += 1
            if self._session_messages > 0:
                self._backoff.reset()
            delay = self._backoff.next()
            self._status.failed(error, delay)
            await self._sleep(delay)

    async def _session(self) -> None:
        """Eine Verbindung vom Aufbau bis zu ihrem Ende.

        Nachrichtenschleife, Keepalive und Sender laufen als eigene Tasks. Endet zuerst die
        Nachrichtenschleife, gilt wie bisher ihr Ergebnis: Sie reicht ihre Ausnahme weiter oder
        endet ohne Fehler. Endet zuerst ein Hilfs-Task, endet die Sitzung mit einem
        ``ConnectionError`` – auch wenn er ohne Ausnahme endet.
        """
        async with self._transport_factory() as transport:
            messages = transport.messages()
            if self.portal_id is None:
                await transport.subscribe("N/+/system/0/Serial")
                self.portal_id = await self._discover_portal(messages)
            portal = self.portal_id
            for topic_filter in SUBSCRIPTIONS:
                await transport.subscribe(f"N/{portal}/{topic_filter}")
            await transport.publish(f"R/{portal}/keepalive", b"")
            self._set_connected(True)
            outbox: _Outbox = asyncio.Queue(maxsize=OUTBOX_SIZE)
            self._outbox = outbox
            reader = asyncio.create_task(self._reader(messages, portal))
            helpers = {
                "Sender": asyncio.create_task(self._sender(transport, outbox)),
                "Keepalive": asyncio.create_task(self._keepalive(transport, portal)),
            }
            tasks = (reader, *helpers.values())
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:  # auch bei gleichzeitigem Ende gilt die Nachrichtenschleife
                    reader.result()
                    return
                for name, helper in helpers.items():
                    if helper in done:
                        raise ConnectionError(f"Hilfs-Task beendet: {name} ({_ending(helper)})")
            finally:
                self._outbox = None  # mit der Sitzung verfällt die Warteschlange
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    # Ein gescheiterter Hilfs-Task (Veröffentlichen schlug fehl) darf weder den
                    # Fehler der Sitzung noch einen Abbruch des Adapters überdecken. Jeder Task
                    # wird abgewartet, damit nach der Sitzung keiner mehr läuft.
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def _discover_portal(self, messages: AsyncIterator[MqttMessage]) -> str:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            async for message in messages:
                parts = message.topic.split("/")
                if len(parts) == 5 and parts[0] == "N" and parts[2:] == ["system", "0", "Serial"]:
                    return parts[1]
        raise ConnectionError("Portal-ID konnte nicht ermittelt werden")

    async def _reader(self, messages: AsyncIterator[MqttMessage], portal: str) -> None:
        async for message in messages:
            self._handle(message, portal)

    async def _keepalive(self, transport: MqttTransport, portal: str) -> None:
        while True:
            await asyncio.sleep(self._cfg.mqtt.keepalive_s)
            await transport.publish(f"R/{portal}/keepalive", KEEPALIVE_SUPPRESS)

    async def _sender(self, transport: MqttTransport, outbox: _Outbox) -> None:
        while True:
            topic, payload = await outbox.get()
            await transport.publish(topic, payload)

    def _handle(self, message: MqttMessage, portal: str) -> None:
        self._store.touch(SOURCE)
        for update in parse_message(message.topic, message.payload, portal, self._resolver):
            self._store.update(update.signal, update.value, source=SOURCE, kind=update.kind)
        self._session_messages += 1
        self._health.last_ok = datetime.now(UTC)

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(SOURCE, connected)
        self._health.connected = connected
        if connected:
            self._status.connected()
