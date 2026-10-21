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


def coe_source(name: str) -> str:
    """Name der Quelle im Zustandsspeicher: ``thermal.<name>``.

    Mit Präfix, damit eine Klimaanlage nie die Quelle einer Wallbox oder eines anderen Adapters
    mit demselben Schlüssel überschreibt (``register_source`` ersetzt still).
    """
    return f"thermal.{name}"


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
        self.source = coe_source(name)
        self._cfg = cfg
        self._store = store
        self._port_factory = port_factory
        self._clock = clock
        self._sleep = sleep
        self._channels = cfg.receive.channels()
        self._health = AdapterHealth(name=self.source)
        self._status = StatusLog(self.source)
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
        self._warned_mismatch = False
        self._last_sent_k: float | None = None
        self._permit_changed = asyncio.Event()
        # nach einem Ausfall gelten alte Zustände (läuft, kühlt, heizt) als veraltet, bis das
        # C.M.I. sie erneut meldet
        store.register_source(self.source, cfg.stale_after_s, invalidate_on_reconnect=True)

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
        """Gibt den gehaltenen Versatz bis zu diesem Zeitpunkt (Uhr des Adapters) frei.

        Läuft die Freigabe ab (oder wird sie zurückgenommen), sendet der Adapter sofort 0 und
        vergisst den gehaltenen Wert; danach braucht es ein neues ``set_offset``. Das gilt auch,
        wenn die alte Freigabe schon abgelaufen war, als die neue kommt (etwa nach einem
        blockierten Event-Loop, bevor der Lapse-Loop lief): Ein gehaltener Wert lebt nie über eine
        Lücke der Freigabe hinweg.
        """
        expired = self._clock() >= self._permit_until  # der alte Wert braucht neue Entscheidung
        self._permit_until = until_monotonic
        if (expired or self._clock() >= until_monotonic) and self._held_k != 0.0:
            self._held_k = 0.0  # Rücknahme: nicht erst auf den Lapse-Loop warten
            try:
                self._send()
            except OSError as exc:
                # Ein Sendefehler darf den Zyklus nicht stören; der Lapse-Loop und die Sitzung
                # senden die 0 weiter, bis sie hinausgeht.
                log.warning(
                    "CoE: Rücknahme der Freigabe nicht gesendet",
                    adapter=self.name,
                    grund=f"{type(exc).__name__}: {exc}",
                )
        self._permit_changed.set()

    def permit_for(self, seconds: float) -> None:
        """Gibt den gehaltenen Versatz ``seconds`` ab jetzt frei (Uhr des Adapters).

        Die Laufzeit ruft das je Zyklus, ohne die Uhr des Adapters zu kennen; ``0`` nimmt die
        Freigabe sofort zurück (``permit``).
        """
        self.permit(self._clock() + seconds)

    def set_offset(self, value_k: float) -> bool:
        """Hält den Versatz und sendet sofort; ``False`` ohne Sitzung oder außerhalb der Grenze.

        Zweite Sicherung neben der Safety-Schicht: Ein Betrag über ``max_offset_k`` wird nie
        gehalten und nie gesendet. Ohne gültige Freigabe hält der Adapter 0 statt des Werts, damit
        ein alter Wert nie ohne neue Entscheidung hinausgeht.
        """
        if self._port is None:
            return False
        if not math.isfinite(value_k) or abs(value_k) > self._cfg.max_offset_k:
            return False
        held_k = value_k if self._clock() < self._permit_until else 0.0
        self._send(held_k)
        self._held_k = held_k  # erst nach dem Senden: scheitert es, bleibt der alte Wert
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
        # Der Lapse-Loop dieser Sitzung wacht auch über eine Freigabe, die noch aus der alten gilt
        self._permit_changed.set()
        tasks = [
            asyncio.create_task(self._send_loop()),
            asyncio.create_task(self._lapse_loop()),
            asyncio.create_task(self._watch_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._port = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):  # beim Ende noch 0 senden, soweit es geht
                port.sendto(self._packet(0.0), self._destination)
            with contextlib.suppress(Exception):
                port.close()

    async def _send_loop(self) -> None:
        while True:
            self._send()
            await self._sleep(self._cfg.send_interval_s)

    async def _lapse_loop(self) -> None:
        """Sendet 0, sobald die Freigabe abläuft – ohne den nächsten Takt abzuwarten."""
        while True:
            await self._permit_changed.wait()
            self._permit_changed.clear()
            while (remaining := self._permit_until - self._clock()) > 0:
                await self._sleep_unless_permit_changes(remaining)
            if self._held_k != 0.0:
                self._held_k = 0.0
                self._send()

    async def _wait_permit_change(self) -> None:
        await self._permit_changed.wait()

    async def _sleep_unless_permit_changes(self, seconds: float) -> None:
        """Schläft, endet aber früher, wenn ``permit`` die Freigabe ändert (Rücknahme, Kürzung)."""
        sleeper: asyncio.Future[None] = asyncio.ensure_future(self._sleep(seconds))
        changed: asyncio.Future[None] = asyncio.ensure_future(self._wait_permit_change())
        try:
            await asyncio.wait({sleeper, changed}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            changed.cancel()
            await asyncio.gather(sleeper, changed, return_exceptions=True)
        self._permit_changed.clear()

    async def _watch_loop(self) -> None:
        while True:
            await self._sleep(WATCH_S)
            last = self._last_packet
            if (
                last is not None
                and self._health.connected
                and self._clock() - last >= self._cfg.stale_after_s
            ):
                self._set_connected(False)
                error = (
                    f"Seit {self._clock() - last:.0f} Sekunden ist kein gültiges CoE-Paket "
                    "vom C.M.I. eingegangen."
                )
                self._health.last_error = error
                self._status.failed(error)

    @property
    def _destination(self) -> tuple[str, int]:
        return (str(self._cfg.cmi_host), self._cfg.port)

    def _packet(self, value: float) -> bytes:
        return offset_packet(value, node=self._cfg.own_node, output=self._cfg.own_output)

    def _send(self, held_k: float | None = None) -> None:
        """Sendet den gehaltenen Versatz (``held_k``, sonst der gehaltene Wert), ohne Freigabe 0."""
        port = self._port
        if port is None:
            return
        if held_k is None:
            held_k = self._held_k
        value = held_k if self._clock() < self._permit_until else 0.0
        port.sendto(self._packet(value), self._destination)
        self._last_sent_k = value + 0.0  # aus -0.0 wird 0.0
        self._publish_sent()

    def _publish_sent(self) -> None:
        if self._last_sent_k is not None:
            self._store.update(
                f"thermal.{self.name}.offset_sent_k",
                self._last_sent_k,
                source=self.source,
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
            self._handle(data)
        except CoeError as exc:
            self._count_bad(
                "Ein ungültiges CoE-Paket wurde verworfen; weitere werden nur gezählt.", exc
            )
        except Exception as exc:  # ein Paket darf den Empfang nie beenden
            self._count_bad(
                "Beim Verarbeiten eines CoE-Pakets trat ein unerwarteter Fehler auf; "
                "weitere werden nur gezählt.",
                exc,
            )

    def _count_bad(self, message: str, exc: Exception) -> None:
        self._bad += 1
        if not self._warned_bad:
            self._warned_bad = True
            log.warning(message, adapter=self.name, grund=f"{type(exc).__name__}: {exc}")

    def _handle(self, data: bytes) -> None:
        values = decode(data)
        matches = [
            (key, channel, value)
            for key, channel in self._channels.items()
            if (value := _find(values, channel)) is not None
        ]
        if not matches:
            self._foreign += 1  # kein Wert der Empfangstabelle: nicht von der Klimaanlage
            return
        was_connected = self._health.connected
        # erst verbinden, dann schreiben: Werte vor Beginn der Verbindung gälten als veraltet
        self._set_connected(True)
        if not was_connected:
            self._publish_sent()  # der zuletzt gesendete Wert gilt in der neuen Verbindung weiter
        self._last_packet = self._clock()
        self._last_packet_at = datetime.now(UTC).isoformat()
        self._health.last_ok = datetime.now(UTC)
        for key, channel, value in matches:
            self._write(key, channel, value)
        self._store.touch(self.source)

    def _write(self, key: str, channel: CoeChannel, value: CoeValue) -> None:
        converted = scaled(value, channel)
        if converted is None:
            self._unit_mismatch += 1
            if not self._warned_mismatch:
                self._warned_mismatch = True
                log.warning(
                    "Ein CoE-Wert passt nicht zur Empfangstabelle (Einheit oder Art) und gilt als "
                    "ungültig; weitere werden nur gezählt.",
                    adapter=self.name,
                    wert=key,
                )
        signal = "power_w" if key == "power_kw" else key
        self._store.update(
            f"thermal.{self.name}.{signal}",
            _to_signal(signal, converted),
            source=self.source,
            kind=SignalKind.MEASUREMENT if signal in _MEASUREMENTS else SignalKind.STATE,
        )

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(self.source, connected)
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
