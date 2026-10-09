"""Home-Assistant-Entitäten der app (Spec 12): nur Sensoren, keine Befehle.

Unbekannte Werte werden als ``None`` veröffentlicht; Home Assistant zeigt dann „unbekannt“.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from bindaems import __version__
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.service import ForecastService
from bindaems.app.prices.pipeline import TOMORROW_EXPECTED_FROM
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.config import Config, EvcsConfig, HaMqttConfig
from bindaems.shared.domain import Value
from bindaems.shared.timeutil import LOCAL_TZ, slot_start

NODE_ID = "bindaems"
MANUFACTURER = "BindaEMS"
UNKNOWN = "None"
PRICES_AHEAD = timedelta(hours=36)


@dataclass(frozen=True)
class DeviceSpec:
    identifier: str
    name: str
    model: str


MAIN_DEVICE = DeviceSpec(NODE_ID, "BindaEMS", "EMS")


@dataclass(frozen=True)
class HaInputs:
    core_connected: bool
    mode: str | None
    derived: Mapping[str, Any]
    signals: Mapping[str, Value]
    alarm_ids: Sequence[str]
    adapters_offline: Sequence[str] | None  # None = unbekannt
    price_now_ct: float | None
    prices_ahead: Sequence[tuple[datetime, float]]
    feed_in_ct: float | None
    prices_missing: bool
    pv_today_kwh: float | None
    pv_tomorrow_kwh: float | None


@dataclass(frozen=True)
class EntitySpec:
    object_id: str
    component: Literal["sensor", "binary_sensor"]
    name: str
    device: DeviceSpec
    value: Callable[[HaInputs], str]
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    attributes: Callable[[HaInputs], Mapping[str, Any]] | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def fmt(value: float | None, decimals: int) -> str:
    number = _number(value)
    return UNKNOWN if number is None else f"{number:.{decimals}f}"


def on_off(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return "ON" if value else "OFF"


def object_id(key: str) -> str:
    """Schlüssel aus ``config.yaml`` als Objekt-ID (nur ``a-z``, ``0-9`` und ``_``)."""
    return re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_") or "x"


def _derived_w(key: str) -> Callable[[HaInputs], str]:
    return lambda inputs: fmt(_number(inputs.derived.get(key)), 0)


def _signal(name: str, decimals: int) -> Callable[[HaInputs], str]:
    return lambda inputs: fmt(_number(inputs.signals.get(name)), decimals)


def _wallbox_w(name: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        wallboxes = inputs.derived.get("wallbox_w")
        return fmt(_number(wallboxes.get(name)) if isinstance(wallboxes, Mapping) else None, 0)

    return value


def _competitor(inputs: HaInputs) -> str:
    if not inputs.core_connected:
        return UNKNOWN
    return on_off(any(alarm.startswith("competitor.") for alarm in inputs.alarm_ids))


def _price_attributes(inputs: HaInputs) -> Mapping[str, Any]:
    if not inputs.prices_ahead:
        return {"prices": []}
    until = inputs.prices_ahead[0][0] + PRICES_AHEAD
    return {
        "prices": [
            {"start": start.astimezone(LOCAL_TZ).isoformat(), "ct": round(ct, 2)}
            for start, ct in inputs.prices_ahead
            if start < until
        ]
    }


def _power(oid: str, name: str, device: DeviceSpec, value: Callable[[HaInputs], str]) -> EntitySpec:
    return EntitySpec(
        oid,
        "sensor",
        name,
        device,
        value,
        unit="W",
        device_class="power",
        state_class="measurement",
    )


def _price(
    oid: str,
    name: str,
    value: Callable[[HaInputs], str],
    attributes: Callable[[HaInputs], Mapping[str, Any]] | None = None,
) -> EntitySpec:
    return EntitySpec(
        oid,
        "sensor",
        name,
        MAIN_DEVICE,
        value,
        unit="ct/kWh",
        state_class="measurement",
        attributes=attributes,
    )


def build_entities(cfg: Config) -> list[EntitySpec]:
    main = MAIN_DEVICE
    entities = [
        _price("price_now", "Bezugspreis", lambda i: fmt(i.price_now_ct, 2), _price_attributes),
        _price("feed_in_price", "Einspeisepreis", lambda i: fmt(i.feed_in_ct, 2)),
        EntitySpec(
            "pv_forecast_today",
            "sensor",
            "PV-Prognose heute",
            main,
            lambda i: fmt(i.pv_today_kwh, 1),
            unit="kWh",
            device_class="energy",
        ),
        EntitySpec(
            "pv_forecast_tomorrow",
            "sensor",
            "PV-Prognose morgen",
            main,
            lambda i: fmt(i.pv_tomorrow_kwh, 1),
            unit="kWh",
            device_class="energy",
        ),
        _power("grid_power", "Netzleistung", main, _derived_w("grid_w")),
        _power("pv_power", "PV-Leistung", main, _derived_w("pv_total_w")),
        _power("battery_power", "Akkuleistung", main, _derived_w("battery_w")),
        _power("house_power", "Hausverbrauch", main, _derived_w("house_load_w")),
        EntitySpec(
            "battery_soc",
            "sensor",
            "Akku-SOC",
            main,
            _signal("battery.soc_pct", 1),
            unit="%",
            device_class="battery",
            state_class="measurement",
        ),
        EntitySpec(
            "ems_mode",
            "sensor",
            "Betriebsart",
            main,
            lambda i: i.mode if i.mode is not None else UNKNOWN,
            entity_category="diagnostic",
        ),
        EntitySpec(
            "core_online",
            "binary_sensor",
            "core verbunden",
            main,
            lambda i: on_off(i.core_connected),
            device_class="connectivity",
            entity_category="diagnostic",
        ),
        EntitySpec(
            "problem_competitor",
            "binary_sensor",
            "Mitregelndes System",
            main,
            _competitor,
            device_class="problem",
        ),
        EntitySpec(
            "problem_prices",
            "binary_sensor",
            "Preise fehlen",
            main,
            lambda i: on_off(i.prices_missing),
            device_class="problem",
        ),
        EntitySpec(
            "problem_device_offline",
            "binary_sensor",
            "Gerät offline",
            main,
            lambda i: on_off(None if i.adapters_offline is None else bool(i.adapters_offline)),
            device_class="problem",
            attributes=lambda i: {"adapters": list(i.adapters_offline or [])},
        ),
    ]
    for key, vehicle in cfg.vehicles.items():
        oid = object_id(key)
        device = DeviceSpec(f"{NODE_ID}_{oid}", f"BindaEMS {vehicle.name}", "Fahrzeug")
        entities.append(
            EntitySpec(
                f"{oid}_soc",
                "sensor",
                "SOC",
                device,
                _signal(f"vehicle.{key}.soc_pct", 1),
                unit="%",
                device_class="battery",
                state_class="measurement",
            )
        )
    for key, wallbox in cfg.wallboxes.items():
        oid = object_id(key)
        label = "EVCS" if isinstance(wallbox, EvcsConfig) else "Wall Connector"
        device = DeviceSpec(f"{NODE_ID}_{oid}", f"BindaEMS {label}", label)
        entities.append(_power(f"{oid}_power", "Ladeleistung", device, _wallbox_w(key)))
    return entities


def discovery(spec: EntitySpec, mqtt: HaMqttConfig) -> tuple[str, str]:
    """Discovery-Topic und -Payload (kompaktes JSON mit sortierten Schlüsseln)."""
    base = mqtt.base_topic
    payload: dict[str, Any] = {
        "name": spec.name,
        "unique_id": f"{NODE_ID}_{spec.object_id}",
        "has_entity_name": True,
        "state_topic": f"{base}/state/{spec.object_id}",
        "availability_topic": f"{base}/status",
        "device": {
            "identifiers": [spec.device.identifier],
            "name": spec.device.name,
            "manufacturer": MANUFACTURER,
            "model": spec.device.model,
            "sw_version": __version__,
        },
        "origin": {"name": MANUFACTURER, "sw_version": __version__},
    }
    optional = {
        "unit_of_measurement": spec.unit,
        "device_class": spec.device_class,
        "state_class": spec.state_class,
        "entity_category": spec.entity_category,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    if spec.attributes is not None:
        payload["json_attributes_topic"] = f"{base}/state/{spec.object_id}/attributes"
    if spec.component == "binary_sensor":
        payload["payload_on"], payload["payload_off"] = "ON", "OFF"
    topic = f"{mqtt.discovery_prefix}/{spec.component}/{NODE_ID}/{spec.object_id}/config"
    return topic, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_inputs(
    live: LiveState,
    prices: PriceService,
    store: PriceStore,
    forecast: ForecastService,
    now: datetime,
) -> HaInputs:
    connected = live.connected
    signals: dict[str, Value] = {}
    raw_signals = live.state.get("signals") if connected and live.state is not None else None
    if isinstance(raw_signals, dict):
        for name in raw_signals:
            value = live.value(name)
            if value is not None:
                signals[name] = value
    health = live.health if connected and isinstance(live.health, dict) else None
    mode = health.get("mode") if health is not None else None
    adapters = health.get("adapters") if health is not None else None
    adapters_offline = (
        [
            adapter["name"]
            for adapter in adapters
            if isinstance(adapter, dict)
            and adapter.get("connected") is False
            and isinstance(adapter.get("name"), str)
        ]
        if isinstance(adapters, list)
        else None
    )
    current = slot_start(now)
    views = prices.slots(current, current + PRICES_AHEAD)
    present = views[0] if views and views[0].start == current else None
    local = now.astimezone(LOCAL_TZ)
    tomorrow = local.date() + timedelta(days=1)
    prices_missing = present is None or (
        local.time() >= TOMORROW_EXPECTED_FROM and not store.complete_day(tomorrow)
    )
    latest = forecast.latest()
    return HaInputs(
        core_connected=connected,
        mode=mode if isinstance(mode, str) else None,
        derived=dict(live.derived()),
        signals=signals,
        alarm_ids=[
            alarm["id"]
            for alarm in live.alarms
            if isinstance(alarm, dict) and isinstance(alarm.get("id"), str)
        ],
        adapters_offline=adapters_offline,
        price_now_ct=present.import_gross_ct if present is not None else None,
        prices_ahead=[(view.start, view.import_gross_ct) for view in views],
        feed_in_ct=present.feed_in_ct if present is not None else None,
        prices_missing=prices_missing,
        pv_today_kwh=latest.day_energy_kwh(local.date()) if latest is not None else None,
        pv_tomorrow_kwh=latest.day_energy_kwh(tomorrow) if latest is not None else None,
    )
