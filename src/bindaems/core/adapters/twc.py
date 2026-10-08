"""Tesla Wall Connector Gen3 über die lokale HTTP-API, ausschließlich lesend (nur GET)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx

from bindaems.core.adapters.base import AdapterHealth, Backoff
from bindaems.core.state.store import StateStore
from bindaems.shared.config import PhaseMap, TwcConfig
from bindaems.shared.domain import SignalKind, Value

M = SignalKind.MEASUREMENT
S = SignalKind.STATE

FRESHNESS_S = 10.0
TIMEOUT_S = 3.0
_TERMINALS = ("A", "B", "C")

Parsed = dict[str, tuple[Value, SignalKind]]


def _number(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _flag(data: Mapping[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _integer(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_vitals(data: Mapping[str, Any], phase_map: PhaseMap) -> Parsed:
    """Klemme A/B/C → Netzphase laut ``phase_map``; fehlende Felder ergeben ``None``."""
    result: Parsed = {
        "vehicle_connected": (_flag(data, "vehicle_connected"), S),
        "contactor_closed": (_flag(data, "contactor_closed"), S),
        "evse_state": (_integer(data, "evse_state"), S),
    }
    power: float | None = 0.0
    for terminal, phase in zip(_TERMINALS, phase_map, strict=True):
        current = _number(data, f"current{terminal}_a")
        voltage = _number(data, f"voltage{terminal}_v")
        result[f"{phase.lower()}.current_a"] = (current, M)
        result[f"{phase.lower()}.voltage_v"] = (voltage, M)
        if power is None or current is None or voltage is None:
            power = None
        else:
            power += current * voltage
    result["power_w"] = (power, M)
    energy_wh = _number(data, "session_energy_wh")
    result["session_kwh"] = (energy_wh / 1000 if energy_wh is not None else None, M)
    return result


def parse_lifetime(data: Mapping[str, Any]) -> Parsed:
    energy_wh = _number(data, "energy_wh")
    return {"total_kwh": (energy_wh / 1000 if energy_wh is not None else None, M)}


class TwcAdapter:
    """Fragt Vitals alle ``vitals_s`` ab, Lifetime seltener und die Version einmalig."""

    def __init__(
        self,
        name: str,
        cfg: TwcConfig,
        store: StateStore,
        client: httpx.AsyncClient,
        *,
        vitals_s: float = 2.0,
        lifetime_s: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self._cfg = cfg
        self._store = store
        self._client = client
        self._vitals_s = vitals_s
        self._lifetime_every = max(1, round(lifetime_s / vitals_s))
        self._sleep = sleep
        self._health = AdapterHealth(name=name)
        self._backoff = Backoff()
        self._version_read = False
        store.register_source(name, FRESHNESS_S)

    def health(self) -> AdapterHealth:
        return replace(self._health)

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                self._set_connected(False)
                raise
            except Exception as exc:  # jeder Abfragefehler führt zu Backoff und neuem Versuch
                self._set_connected(False)
                self._health.last_error = f"{type(exc).__name__}: {exc}"
                self._health.error_count += 1
            await self._sleep(self._backoff.next())

    async def _session(self) -> None:
        if not self._version_read:
            try:
                version = await self._get("/api/1/version")
            except httpx.HTTPStatusError:  # nur informativ: darf die Vitals nicht blockieren
                version = {}
            self._apply({"firmware": (_text(version, "firmware_version"), S)})
            self._version_read = True
        polls = 0
        while True:
            self._apply(parse_vitals(await self._get("/api/1/vitals"), self._cfg.phase_map))
            if polls % self._lifetime_every == 0:
                self._apply(parse_lifetime(await self._get("/api/1/lifetime")))
            polls += 1
            self._set_connected(True)
            self._health.last_ok = datetime.now(UTC)
            self._backoff.reset()
            await self._sleep(self._vitals_s)

    async def _get(self, path: str) -> Mapping[str, Any]:
        response = await self._client.get(f"http://{self._cfg.host}{path}", timeout=TIMEOUT_S)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"{path}: JSON-Objekt erwartet")
        return data

    def _apply(self, values: Parsed) -> None:
        for key, (value, kind) in values.items():
            self._store.update(f"wallbox.{self.name}.{key}", value, source=self.name, kind=kind)

    def _set_connected(self, connected: bool) -> None:
        self._store.set_connected(self.name, connected)
        self._health.connected = connected


def _text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None
