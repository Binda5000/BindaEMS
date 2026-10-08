"""Tessie-Cloud-API für Tesla-Fahrzeuge, ausschließlich lesend und ohne das Fahrzeug zu wecken.

Abgefragt wird nur ``GET /{vin}/state?use_cache=true``. Fehler trennen die Quelle nicht: Die
Werte gelten bis zur Frischegrenze von 900 s weiter, damit eine kurze Störung der Cloud nicht
sofort den Ladestand des Fahrzeugs verliert.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from bindaems.core.adapters.base import AdapterHealth, Backoff
from bindaems.core.state.store import StateStore
from bindaems.shared.config import LatLon, VehicleConfig
from bindaems.shared.domain import SignalKind, Value

TESSIE_BASE_URL = "https://api.tessie.com"
FRESHNESS_S = 900.0
TIMEOUT_S = 20.0
AUTH_RETRY_S = 900.0
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _at_home(drive: Mapping[str, Any], home: LatLon, radius_m: float) -> bool | None:
    lat, lon = _number(drive.get("latitude")), _number(drive.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return haversine_m(home, LatLon(lat=lat, lon=lon)) <= radius_m


def parse_state(data: Mapping[str, Any], home: LatLon, radius_m: float) -> dict[str, Value]:
    charge = _mapping(data.get("charge_state"))
    charging_state = _text(charge.get("charging_state"))
    scheduled_mode = _text(charge.get("scheduled_charging_mode"))
    state = _text(data.get("state"))
    return {
        "soc_pct": _number(charge.get("battery_level")),
        "charge_limit_pct": _number(charge.get("charge_limit_soc")),
        "charging_state": charging_state,
        "charge_amps": _number(charge.get("charge_amps")),
        "current_request_a": _number(charge.get("charge_current_request")),
        "actual_current_a": _number(charge.get("charger_actual_current")),
        "phases": _number(charge.get("charger_phases")),
        "charger_power_kw": _number(charge.get("charger_power")),
        "plugged": None if charging_state is None else charging_state != "Disconnected",
        "scheduled_charging": None if scheduled_mode is None else scheduled_mode != "Off",
        "at_home": _at_home(_mapping(data.get("drive_state")), home, radius_m),
        "online": None if state is None else state == "online",
    }


def poll_interval_s(charging_state: str | None, plugged: bool | None) -> float:
    if charging_state == "Charging":
        return 30.0
    if plugged:
        return 60.0
    return 600.0


class TessieAdapter:
    def __init__(
        self,
        name: str,
        cfg: VehicleConfig,
        home: LatLon,
        token: SecretStr,
        store: StateStore,
        client: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if cfg.tessie is None:
            raise ValueError(f"Fahrzeug {name}: keine Tessie-Konfiguration")
        self.name = name
        self._source = f"tessie:{name}"
        self._url = f"{TESSIE_BASE_URL}/{cfg.tessie.vin}/state"
        self._cfg = cfg
        self._home = home
        self._token = token
        self._store = store
        self._client = client
        self._sleep = sleep
        self._health = AdapterHealth(name=self._source)
        self._backoff = Backoff(initial=60.0, maximum=900.0)
        store.register_source(self._source, FRESHNESS_S)

    def health(self) -> AdapterHealth:
        return replace(self._health)

    async def run(self) -> None:
        try:
            while True:
                await self._sleep(await self._poll())
        except asyncio.CancelledError:
            self._store.set_connected(self._source, False)
            self._health.connected = False
            raise

    async def _poll(self) -> float:
        """Eine Abfrage; liefert die Wartezeit bis zur nächsten."""
        try:
            response = await self._client.get(
                self._url,
                params={"use_cache": "true"},
                headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                timeout=TIMEOUT_S,
            )
            if response.status_code in (401, 403):
                self._fail("Tessie-Token ungültig")
                return AUTH_RETRY_S
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("JSON-Objekt erwartet")
        except Exception as exc:  # 429, 5xx, Zeitüberschreitung, Netzwerk: Backoff ab 60 s
            self._fail(f"{type(exc).__name__}: {exc}")
            return self._backoff.next()
        values = parse_state(data, self._home, self._cfg.home_radius_m)
        for key, value in values.items():
            self._store.update(
                f"vehicle.{self.name}.{key}",
                value,
                source=self._source,
                kind=SignalKind.MEASUREMENT,
            )
        self._store.set_connected(self._source, True)
        self._health.connected = True
        self._health.last_ok = datetime.now(UTC)
        self._backoff.reset()
        charging, plugged = values["charging_state"], values["plugged"]
        return poll_interval_s(
            charging if isinstance(charging, str) else None,
            plugged if isinstance(plugged, bool) else None,
        )

    def _fail(self, message: str) -> None:
        self._health.connected = False
        self._health.last_error = message
        self._health.error_count += 1
