"""MQTT-Discovery-Publisher für Home Assistant (Spec 12) – nur lesend.

Die app abonniert ausschließlich ``<prefix>/status`` (Neustart von HA) und veröffentlicht nur
Topics, die ``allowed_topic`` erlaubt; Befehle aus HA gibt es in dieser Phase nicht.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from types import TracebackType
from typing import Protocol

import aiomqtt
import structlog
from pydantic import SecretStr

from bindaems.app.ha.entities import EntitySpec, HaInputs, discovery
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import HaMqttConfig
from bindaems.shared.mqtt import build_tls_context
from bindaems.shared.retry import Backoff, StatusLog

log = structlog.get_logger(__name__)

QOS = 1
OFFLINE_TIMEOUT_S = 2.0


class HaTransport(Protocol):
    async def __aenter__(self) -> HaTransport: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def publish(self, topic: str, payload: str, *, retain: bool) -> None: ...

    async def subscribe(self, topic: str) -> None: ...

    def messages(self) -> AsyncIterator[tuple[str, bytes]]: ...


def _payload_bytes(payload: object) -> bytes:
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    if payload is None:
        return b""
    return str(payload).encode()


class AiomqttHaTransport:
    """Verbindung zum Mosquitto von Home Assistant; Last Will setzt die app auf offline."""

    def __init__(self, cfg: HaMqttConfig, password: SecretStr | None) -> None:
        self.will = aiomqtt.Will(f"{cfg.base_topic}/status", "offline", qos=QOS, retain=True)
        self._client = aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=password.get_secret_value() if password is not None else None,
            identifier=f"bindaems-app-{secrets.token_hex(4)}",
            will=self.will,
            tls_context=build_tls_context(cfg),
            tls_insecure=True if cfg.tls and not cfg.tls_verify else None,
        )

    async def __aenter__(self) -> AiomqttHaTransport:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def publish(self, topic: str, payload: str, *, retain: bool) -> None:
        await self._client.publish(topic, payload, qos=QOS, retain=retain)

    async def subscribe(self, topic: str) -> None:
        await self._client.subscribe(topic, qos=QOS)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        async for message in self._client.messages:
            yield str(message.topic), _payload_bytes(message.payload)


def allowed_topic(mqtt: HaMqttConfig) -> re.Pattern[str]:
    prefix, base = re.escape(mqtt.discovery_prefix), re.escape(mqtt.base_topic)
    return re.compile(
        rf"^({prefix}/(sensor|binary_sensor)/bindaems/[a-z0-9_]+/config"
        rf"|{base}/(status|state/[a-z0-9_]+(/attributes)?))$"
    )


class HaPublisher:
    def __init__(
        self,
        cfg: HaMqttConfig,
        entities: Sequence[EntitySpec],
        inputs: Callable[[], HaInputs],
        *,
        transport_factory: Callable[[], HaTransport],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: Backoff | None = None,
    ) -> None:
        self._cfg = cfg
        self._entities = list(entities)
        self._inputs = inputs
        self._transport_factory = transport_factory
        self._sleep = sleep
        self._backoff = backoff or Backoff()
        self._allowed = allowed_topic(cfg)
        self._sent: dict[str, str] = {}  # Topic → zuletzt veröffentlichter Zustand
        self._status_log = StatusLog("ha")
        self._connected = False
        self._error: str | None = None

    async def run(self) -> None:
        while True:
            try:
                async with self._transport_factory() as transport:
                    try:
                        await self._session(transport)
                    except asyncio.CancelledError:
                        await self._goodbye(transport)
                        raise
                error = "Verbindung beendet"
            except Exception as exc:  # jede Störung führt zum Neuverbinden
                error = f"{type(exc).__name__}: {exc}"
            self._connected, self._error = False, error
            self._sent.clear()
            delay = self._backoff.next()
            self._status_log.failed(error, delay)
            await self._sleep(delay)

    async def _goodbye(self, transport: HaTransport) -> None:
        """Beim geordneten Ende selbst „offline“ melden: ein normales DISCONNECT verwirft den
        Last Will, sonst zeigte HA eingefrorene Werte als aktuell an."""
        self._connected, self._error = False, "app beendet"
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self._publish(transport, f"{self._cfg.base_topic}/status", "offline"),
                OFFLINE_TIMEOUT_S,
            )

    async def _session(self, transport: HaTransport) -> None:
        lock = asyncio.Lock()  # Veröffentlichungen aus beiden Aufgaben nicht verschränken
        await self._publish(transport, f"{self._cfg.base_topic}/status", "online")
        await transport.subscribe(f"{self._cfg.discovery_prefix}/status")
        async with lock:
            await self._publish_all(transport)
        self._connected, self._error = True, None
        self._backoff.reset()
        self._status_log.connected()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._periodic(transport, lock))
            group.create_task(self._listen(transport, lock))

    async def _periodic(self, transport: HaTransport, lock: asyncio.Lock) -> None:
        while True:
            await self._sleep(self._cfg.publish_interval_s)
            async with lock:
                await self._publish_states(transport, force=False)

    async def _listen(self, transport: HaTransport, lock: asyncio.Lock) -> None:
        birth = f"{self._cfg.discovery_prefix}/status"
        async for topic, payload in transport.messages():
            if topic == birth and payload.strip() == b"online":
                async with lock:
                    await self._publish_all(transport)  # HA neu gestartet

    async def _publish_all(self, transport: HaTransport) -> None:
        for spec in self._entities:
            topic, payload = discovery(spec, self._cfg)
            await self._publish(transport, topic, payload)
        await self._publish_states(transport, force=True)

    async def _publish_states(self, transport: HaTransport, *, force: bool) -> None:
        inputs = self._inputs()
        base = self._cfg.base_topic
        for spec in self._entities:
            payloads = [(f"{base}/state/{spec.object_id}", spec.value(inputs))]
            if spec.attributes is not None:
                attributes = json.dumps(spec.attributes(inputs), sort_keys=True, ensure_ascii=False)
                payloads.append((f"{base}/state/{spec.object_id}/attributes", attributes))
            for topic, payload in payloads:
                if force or self._sent.get(topic) != payload:
                    await self._publish(transport, topic, payload)
                    self._sent[topic] = payload

    async def _publish(self, transport: HaTransport, topic: str, payload: str) -> None:
        if not self._allowed.fullmatch(topic):
            raise RuntimeError(f"Topic nicht erlaubt: {topic}")
        await transport.publish(topic, payload, retain=True)

    def component_status(self) -> ComponentStatus:
        if self._connected:
            return ComponentStatus("ha", True, f"MQTT verbunden mit {self._cfg.host}")
        if self._error is None:
            return ComponentStatus("ha", False, "MQTT noch nicht verbunden")
        return ComponentStatus("ha", False, f"MQTT nicht verbunden: {self._error}")
