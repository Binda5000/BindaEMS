"""Victron EV Charging Station NS über Modbus TCP, ausschließlich lesend.

Gelesen wird zyklisch ein Block ab Register 5000. Die Belegung folgt Victrons eigenem GX-Treiber
(``dbus-modbus-client``, ``ev_charger.py``, Stand v1.83). Leistung, Status und Energie der
Wallbox liefert zusätzlich der GX über MQTT (``wallbox.<id>.*`` aus ``victron_topics``).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import struct
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.pdu import ModbusPDU

from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.state.store import StateStore
from bindaems.shared.config import EvcsConfig
from bindaems.shared.domain import SignalKind, Value
from bindaems.shared.retry import Backoff, StatusLog

FRESHNESS_S = 5.0

BLOCK_START = 5000
BLOCK_COUNT = 20

REG_PRODUCT_ID = 5000
REG_FW_HIGH = 5007
REG_FW_LOW = 5008
REG_MODE = 5009
REG_START_STOP = 5010
REG_SET_CURRENT = 5016
REG_MAX_CURRENT = 5017
REG_ACTUAL_CURRENT_X10 = 5018

# EVCS 32A V2, AC22, AC22E, AC22NS, EVCS 32A NS V2 – alle mit derselben Belegung
KNOWN_PRODUCT_IDS = frozenset({0xC023, 0xC024, 0xC025, 0xC026, 0xC027})


class ModbusReadError(Exception):
    """Fehlerantwort oder unvollständige Antwort beim Lesen von Registern."""


class RegisterReader(Protocol):
    """Lesender Registerzugriff – absichtlich ohne Schreibmethode."""

    async def connect(self) -> None: ...

    async def read_holding(self, address: int, count: int) -> list[int]: ...

    async def close(self) -> None: ...


# pymodbus ≥ 3.10 erwartet die Unit-ID als ``device_id=``, ältere Versionen als ``slave=``.
_UNIT_KWARG = (
    "device_id"
    if "device_id" in inspect.signature(AsyncModbusTcpClient.read_holding_registers).parameters
    else "slave"
)


async def _read(client: AsyncModbusTcpClient, address: int, count: int, unit_id: int) -> ModbusPDU:
    read = cast(Callable[..., Awaitable[ModbusPDU]], client.read_holding_registers)
    return await read(address, count=count, **{_UNIT_KWARG: unit_id})


class PymodbusReader:
    """Modbus-TCP-Lesezugriff mit pymodbus; Wiederverbindung übernimmt der Adapter."""

    def __init__(self, host: str, port: int, unit_id: int) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._client = AsyncModbusTcpClient(
            host, port=port, timeout=2.0, retries=1, reconnect_delay=0
        )

    async def connect(self) -> None:
        if not await self._client.connect():
            raise ConnectionError(f"Modbus-Verbindung zu {self._host}:{self._port} fehlgeschlagen")

    async def read_holding(self, address: int, count: int) -> list[int]:
        response = await _read(self._client, address, count, self._unit_id)
        if response.isError():
            raise ModbusReadError(f"Fehlerantwort beim Lesen ab Register {address}: {response}")
        registers = list(response.registers)
        if len(registers) != count:
            raise ModbusReadError(
                f"{len(registers)} statt {count} Register ab Register {address} erhalten"
            )
        return registers

    async def close(self) -> None:
        self._client.close()


def firmware_text(high: int, low: int) -> str:
    """Firmware wie im GX, z. B. ``v2.09``; Vorabversionen als ``v1.21-beta-01``."""
    _, major, minor, beta = struct.unpack("4B", struct.pack(">2H", high, low))
    version = f"v{major:x}.{minor:02x}"
    return version if beta == 0xFF else f"{version}-beta-{beta:02x}"


def decode_block(start: int, regs: Sequence[int]) -> dict[str, Value]:
    def reg(address: int) -> int:
        return regs[address - start]

    return {
        "product_id": reg(REG_PRODUCT_ID),
        "firmware": firmware_text(reg(REG_FW_HIGH), reg(REG_FW_LOW)),
        "mode": reg(REG_MODE),
        "start_stop": reg(REG_START_STOP),
        "set_current_a": reg(REG_SET_CURRENT),
        "max_current_a": reg(REG_MAX_CURRENT),
        "current_a": reg(REG_ACTUAL_CURRENT_X10) / 10,
    }


class EvcsAdapter:
    """Fragt die EVCS zyklisch ab und schreibt ``wallbox.<name>.*`` in den Zustandsspeicher."""

    def __init__(
        self,
        name: str,
        cfg: EvcsConfig,
        store: StateStore,
        reader_factory: Callable[[], RegisterReader],
        *,
        poll_s: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self._cfg = cfg
        self._store = store
        self._reader_factory = reader_factory
        self._poll_s = poll_s
        self._sleep = sleep
        self._health = AdapterHealth(name=name)
        self._status = StatusLog(name)
        self._backoff = Backoff()
        store.register_source(name, FRESHNESS_S)

    def health(self) -> AdapterHealth:
        return replace(self._health)

    async def run(self) -> None:
        while True:
            error = "Sitzung beendet"
            try:
                await self._session()
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except Exception as exc:  # jeder Lesefehler führt zur Wiederverbindung
                error = f"{type(exc).__name__}: {exc}"
            self._set_connected(False)
            self._health.last_error = error
            self._health.error_count += 1
            delay = self._backoff.next()
            self._status.failed(error, delay)
            await self._sleep(delay)

    async def _session(self) -> None:
        reader = self._reader_factory()
        try:
            await reader.connect()
            while True:
                regs = await reader.read_holding(BLOCK_START, BLOCK_COUNT)
                self._apply(decode_block(BLOCK_START, regs))
                self._backoff.reset()
                await self._sleep(self._poll_s)
        finally:
            with contextlib.suppress(Exception):
                await reader.close()

    def _apply(self, values: dict[str, Value]) -> None:
        product_id = values["product_id"]
        known = product_id in KNOWN_PRODUCT_IDS
        if not known:
            self._health.last_error = f"Unbekannte Produkt-ID 0x{product_id:04X}"
        for key, value in values.items():
            kind = SignalKind.MEASUREMENT if key == "current_a" else SignalKind.STATE
            # ohne bekannte Produkt-ID ist das Abbild keines einer EVCS (Prüfprotokoll 10.10.2026:
            # lauter Nullen): ungültig, damit Strom und Modus vom Cerbo kommen statt 0
            usable = value if known or key == "product_id" else None
            self._store.update(f"wallbox.{self.name}.{key}", usable, source=self.name, kind=kind)
        self._set_connected(True)
        self._health.last_ok = datetime.now(UTC)

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(self.name, connected)
        self._health.connected = connected
        if connected:
            self._status.connected()
