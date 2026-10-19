"""CoE-V2-Adapter für die Klimaanlage an der UVR610 (UDP, Technische Alternative C.M.I.).

Der Adapter bindet einen UDP-Port, nimmt die Pakete des C.M.I. entgegen und schreibt sie nach der
Empfangstabelle als ``thermal.<name>.*`` in den Zustandsspeicher. Vom selben Port sendet er der UVR
zyklisch den Sollwertversatz (Plan 6a, Entscheidungen 2, 3 und 10).

Sperre: Gesendet wird der gehaltene Versatz nur, solange der core ihn in den letzten Sekunden
freigegeben hat (``permit``); sonst geht 0 hinaus. So fällt der Versatz auch bei einem hängenden
Zyklus nach kurzer Zeit auf 0, ohne die 5 min der UVR abzuwarten. Beim Start hält der Adapter 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

import structlog

from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.state.store import StateStore
from bindaems.shared.coe import CoeError, CoeValue, decode, offset_packet, scaled
from bindaems.shared.config import CoeChannel, CoeOffsetConfig
from bindaems.shared.domain import SignalKind, Value
from bindaems.shared.retry import Backoff, StatusLog

log = structlog.get_logger(__name__)

WATCH_S = 10.0
"""Abstand, in dem der Adapter prüft, ob noch Pakete des C.M.I. kommen."""

_MEASUREMENTS = frozenset({"actual_c", "setpoint_c", "offset_active_k", "power_w"})


class DatagramPort(Protocol):
    """Ein gebundener UDP-Port: sendet Datagramme, Empfangenes geht an den Rückruf."""

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None: ...

    def close(self) -> None: ...


PortFactory = Callable[[Callable[[bytes, tuple[str, int]], None], int], Awaitable[DatagramPort]]
"""Öffnet den Port: bekommt den Empfangsrückruf und den Port, auf dem gehört wird."""


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_datagram(data, (addr[0], addr[1]))

    def error_received(self, exc: Exception) -> None:
        # ICMP-Fehler (etwa „Port nicht erreichbar“) sind bei UDP normal und ohne Folgen:
        # die UVR fällt ohne Paket selbst auf 0
        log.debug("UDP-Fehlermeldung der Gegenstelle", error=str(exc))


class _UdpPort:
    def __init__(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self._transport.sendto(data, addr)

    def close(self) -> None:
        self._transport.close()


async def udp_port(
    on_datagram: Callable[[bytes, tuple[str, int]], None], port: int
) -> DatagramPort:
    """Der echte UDP-Port auf allen Schnittstellen (``loop.create_datagram_endpoint``)."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Protocol(on_datagram),
        local_addr=("0.0.0.0", port),  # noqa: S104 – der core läuft im Container, Docker veröffentlicht
    )
    return _UdpPort(transport)


class CoeAdapter:
    """Empfängt die Werte der Klimaanlage und sendet ihr den Sollwertversatz."""

    def __init__(
        self,
        name: str,
        cfg: CoeOffsetConfig,
        store: StateStore,
        port_factory: PortFactory = udp_port,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self._cfg = cfg
        self._store = store
        self._port_factory = port_factory
        self._clock = clock
        self._sleep = sleep
        self._channels = cfg.receive.channels()
        self._health = AdapterHealth(name=name)
        self._status = StatusLog(name)
        self._backoff = Backoff()
        self._port: DatagramPort | None = None
        self._held_k = 0.0
        self._permit_until = float("-inf")
        self._last_packet: float | None = None
        self._last_packet_at: str | None = None
        self._foreign = 0
        self._bad = 0
        self._unit_mismatch = 0
        self._warned_foreign = False
        self._warned_bad = False
        store.register_source(name, cfg.stale_after_s)

    @property
    def held_k(self) -> float:
        """Der gehaltene Versatz in Kelvin; gesendet wird er nur mit Freigabe."""
        return self._held_k

    def health(self) -> AdapterHealth:
        return replace(self._health)

    def stats(self) -> dict[str, int | str | None]:
        return {
            "last_packet_at": self._last_packet_at,
            "foreign_packets": self._foreign,
            "bad_packets": self._bad,
            "unit_mismatch": self._unit_mismatch,
        }

    def permit(self, until_monotonic: float) -> None:
        """Gibt den gehaltenen Versatz bis zu diesem Zeitpunkt (Uhr des Adapters) frei."""
        self._permit_until = until_monotonic

    def set_offset(self, value_k: float) -> bool:
        """Hält den Versatz und sendet sofort; ``False`` ohne Sitzung oder außerhalb der Grenze.

        Zweite Sicherung neben der Safety-Schicht: Ein Betrag über ``max_offset_k`` wird nie
        gehalten und nie gesendet.
        """
        if self._port is None:
            return False
        if not math.isfinite(value_k) or abs(value_k) > self._cfg.max_offset_k:
            return False
        self._held_k = value_k
        self._send()
        return True

    async def run(self) -> None:
        while True:
            error = "Sitzung beendet"
            try:
                await self._session()
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except Exception as exc:  # jeder Fehler am Port führt zum erneuten Öffnen
                error = f"{type(exc).__name__}: {exc}"
            self._set_connected(False)
            self._health.last_error = error
            self._health.error_count += 1
            delay = self._backoff.next()
            self._status.failed(error, delay)
            await self._sleep(delay)

    async def _session(self) -> None:
        port = await self._port_factory(self._on_datagram, self._cfg.listen_port)
        self._backoff.reset()
        self._held_k = 0.0  # ein neuer Port beginnt mit 0, ein alter Versatz lebt nicht weiter
        self._port = port
        tasks = [asyncio.create_task(self._send_loop()), asyncio.create_task(self._watch_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._port = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                port.close()

    async def _send_loop(self) -> None:
        while True:
            self._send()
            await self._sleep(self._cfg.send_interval_s)

    async def _watch_loop(self) -> None:
        while True:
            await self._sleep(WATCH_S)
            last = self._last_packet
            if last is not None and self._clock() - last >= self._cfg.stale_after_s:
                self._set_connected(False)

    def _send(self) -> None:
        port = self._port
        if port is None:
            return
        value = self._held_k if self._clock() < self._permit_until else 0.0
        port.sendto(
            offset_packet(value, node=self._cfg.own_node, output=self._cfg.own_output),
            (str(self._cfg.cmi_host), self._cfg.port),
        )
        self._store.update(
            f"thermal.{self.name}.offset_sent_k",
            value + 0.0,  # aus -0.0 wird 0.0
            source=self.name,
            kind=SignalKind.STATE,
        )

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr[0] != str(self._cfg.cmi_host):
            self._foreign += 1
            if not self._warned_foreign:
                self._warned_foreign = True
                log.warning(
                    "Ein CoE-Paket von einer fremden Adresse wurde verworfen; "
                    "weitere werden nur gezählt.",
                    adapter=self.name,
                    absender=addr[0],
                )
            return
        try:
            values = decode(data)
        except CoeError as exc:
            self._bad += 1
            if not self._warned_bad:
                self._warned_bad = True
                log.warning(
                    "Ein ungültiges CoE-Paket wurde verworfen; weitere werden nur gezählt.",
                    adapter=self.name,
                    grund=str(exc),
                )
            return
        if not self._apply(values):
            self._foreign += 1  # kein Wert der Empfangstabelle: nicht von der Klimaanlage
            return
        self._last_packet = self._clock()
        self._last_packet_at = datetime.now(UTC).isoformat()
        self._health.last_ok = datetime.now(UTC)
        self._set_connected(True)
        self._store.touch(self.name)

    def _apply(self, values: list[CoeValue]) -> bool:
        """Schreibt die Werte der Empfangstabelle; ``False``, wenn das Paket keinen enthält."""
        found = False
        for key, channel in self._channels.items():
            value = _find(values, channel)
            if value is None:
                continue
            found = True
            converted = scaled(value, channel)
            if converted is None:
                self._unit_mismatch += 1
            signal = "power_w" if key == "power_kw" else key
            self._store.update(
                f"thermal.{self.name}.{signal}",
                _to_signal(signal, converted),
                source=self.name,
                kind=SignalKind.MEASUREMENT if signal in _MEASUREMENTS else SignalKind.STATE,
            )
        return found

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(self.name, connected)
        self._health.connected = connected
        if connected:
            self._status.connected()


def _find(values: list[CoeValue], channel: CoeChannel) -> CoeValue | None:
    analog = channel.analog is not None
    output = channel.analog if analog else channel.digital
    for value in values:
        if value.node == channel.node and value.output == output and value.analog == analog:
            return value
    return None


def _to_signal(signal: str, converted: float | bool | None) -> Value:
    if converted is not None and signal == "power_w":
        return float(converted) * 1000.0  # kW → W
    return converted
