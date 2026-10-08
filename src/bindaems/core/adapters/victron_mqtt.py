"""Victron-Cerbo-Adapter über MQTT (dbus-flashmq), ausschließlich lesend.

Publiziert wird nur der Keepalive auf ``R/<portal>/keepalive``: nach jedem Verbindungsaufbau
einmal leer (vollständige Neuveröffentlichung aller Werte), danach im Takt ``keepalive_s`` mit
``suppress-republish``.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol

import aiomqtt
from pydantic import SecretStr

from bindaems.core.adapters.base import AdapterHealth, Backoff
from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.state.store import StateStore
from bindaems.shared.config import MqttConfig, VictronConfig

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
)


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


def build_tls_context(cfg: MqttConfig) -> ssl.SSLContext | None:
    """TLS-Kontext für den Cerbo; ``tls_verify=False`` akzeptiert das selbstsignierte Zertifikat."""
    if not cfg.tls:
        return None
    cafile = str(cfg.tls_ca_file) if cfg.tls_ca_file is not None else None
    context = ssl.create_default_context(cafile=cafile)
    if not cfg.tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


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
    """Liest alle relevanten Cerbo-Werte per MQTT in den Zustandsspeicher."""

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
        self._password = password
        self._store = store
        self._transport_factory = transport_factory
        self._sleep = sleep
        self._resolver = InstanceResolver(cfg.instances)
        self._health = AdapterHealth(name=self.name)
        self._backoff = Backoff()
        self._session_messages = 0
        self.portal_id: str | None = cfg.mqtt.portal_id
        store.register_source(SOURCE, FRESHNESS_S)

    def health(self) -> AdapterHealth:
        return replace(self._health)

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
            await self._sleep(self._backoff.next())

    async def _session(self) -> None:
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
            keepalive = asyncio.create_task(self._keepalive(transport, portal))
            try:
                async for message in messages:
                    self._handle(message, portal)
            finally:
                keepalive.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive

    async def _discover_portal(self, messages: AsyncIterator[MqttMessage]) -> str:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            async for message in messages:
                parts = message.topic.split("/")
                if len(parts) == 5 and parts[0] == "N" and parts[2:] == ["system", "0", "Serial"]:
                    return parts[1]
        raise ConnectionError("Portal-ID konnte nicht ermittelt werden")

    async def _keepalive(self, transport: MqttTransport, portal: str) -> None:
        while True:
            await asyncio.sleep(self._cfg.mqtt.keepalive_s)
            await transport.publish(f"R/{portal}/keepalive", KEEPALIVE_SUPPRESS)

    def _handle(self, message: MqttMessage, portal: str) -> None:
        for update in parse_message(message.topic, message.payload, portal, self._resolver):
            self._store.update(update.signal, update.value, source=SOURCE, kind=update.kind)
        self._session_messages += 1
        self._health.last_ok = datetime.now(UTC)

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(SOURCE, connected)
        self._health.connected = connected
