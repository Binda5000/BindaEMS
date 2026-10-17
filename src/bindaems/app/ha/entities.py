"""Home-Assistant-Entitäten der app (Spec 12): Sensoren und Bedienelemente (Plan 2d, Präz. 15).

Unbekannte Werte werden als ``None`` veröffentlicht; Home Assistant zeigt dann „unbekannt“.
Bedienelemente (``select``, ``switch``, ``button``) tragen ``command=True``; ihre Befehle kommen
über ``{base_topic}/cmd/<object_id>`` und führt ``ha.commands`` aus.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from bindaems import __version__
from bindaems.app.control.intent import ASSIGNMENT_OVERRIDE
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.service import ForecastService
from bindaems.app.ledger.service import MonthPeak
from bindaems.app.planning.explain import slot_text
from bindaems.app.prices.pipeline import TOMORROW_EXPECTED_FROM
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.config import (
    ASSIGNMENT_AUTOMATIC,
    Config,
    EvcsConfig,
    HaMqttConfig,
    WallboxConfig,
    ha_object_id,
)
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.domain import Value
from bindaems.shared.plan import BatteryIntentKind, Plan, PlanSlot
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, slot_start

NODE_ID = "bindaems"
MANUFACTURER = "BindaEMS"
UNKNOWN = "None"
PRICES_AHEAD = timedelta(hours=36)

# Optionen der Lademodi wie im UI, ohne „Boost“ (Texte, „Home Assistant“)
MODE_LABELS: Final[dict[ChargeMode, str]] = {
    ChargeMode.PV: "PV-Überschuss",
    ChargeMode.MIN_PV: "Min+PV",
    ChargeMode.NOW: "Sofort",
    ChargeMode.OFF: "Aus",
}
# Zustand der Sensoren „Akku-Plan“: die Absicht des Slots (Texte, „Absichten“)
INTENT_LABELS: Final[dict[BatteryIntentKind, str]] = {
    BatteryIntentKind.EIGENVERBRAUCH: "Eigenverbrauch",
    BatteryIntentKind.HALTEN: "Halten",
    BatteryIntentKind.NETZLADEN: "Netzladen",
}
AUTOMATIC: Final = ASSIGNMENT_AUTOMATIC  # Zuordnung ohne Übersteuerung
NO_WALLBOX: Final = "keine"
NO_VEHICLE: Final = "keins"
UNCLEAR: Final = "unclear"  # ``assignment`` in Health


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
    watchdog_state: str | None = None  # Zustand des Watchdogs am Cerbo; None = unbekannt
    intent: Intent | None = None  # gültige Absicht; None = unbekannt
    intent_version: int | None = None  # Health ``intent_version``; None = unbekannt
    vehicles: list[dict[str, Any]] = field(default_factory=list)  # Health ``vehicles``
    wallboxes: list[dict[str, Any]] = field(default_factory=list)  # Health ``wallboxes``
    wallbox_names: dict[str, str] = field(default_factory=dict)  # eigene Namen der Wallboxes
    month_peak: MonthPeak | None = None  # höchste Viertelstunde des Monats; None = keine
    plan: Plan | None = None  # Akkuplan, nur wenn er jetzt gilt
    plan_now: PlanSlot | None = None  # Slot des Plans zu jetzt
    plan_next: PlanSlot | None = None  # der Slot danach; None am Ende des Plans
    grid_charge_allowed: bool | None = None  # Einstellung „Netzladen erlaubt“; None = unbekannt


@dataclass(frozen=True)
class EntitySpec:
    object_id: str
    component: Literal["sensor", "binary_sensor", "select", "switch", "button"]
    name: str
    device: DeviceSpec
    value: Callable[[HaInputs], str]
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    attributes: Callable[[HaInputs], Mapping[str, Any]] | None = None
    options: tuple[str, ...] = ()  # Auswahl eines ``select``
    command: bool = False  # nimmt Befehle über ``{base}/cmd/<object_id>`` an


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


object_id = (
    ha_object_id  # Schlüssel aus ``config.yaml`` als Objekt-ID (nur ``a-z``, ``0-9``, ``_``)
)


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


def _watchdog_tripped(inputs: HaInputs) -> str:
    """AN, wenn der Watchdog am Cerbo ausgelöst hat; ohne Auskunft des Watchdogs unbekannt."""
    state = inputs.watchdog_state
    if state is None or state == "unknown":
        return on_off(None)
    return on_off(state == "tripped")


def _entry(entries: Sequence[Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    return next((item for item in entries if item.get("id") == key), None)


def _mode_state(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        if inputs.intent is None:
            return UNKNOWN
        return MODE_LABELS[inputs.intent.vehicles.get(key, VehicleIntent()).mode]

    return value


def _guest_mode_state(inputs: HaInputs) -> str:
    return UNKNOWN if inputs.intent is None else MODE_LABELS[inputs.intent.guest.mode]


def _regulation_state(inputs: HaInputs) -> str:
    return on_off(None if inputs.intent is None else inputs.intent.control)


def _assignment_state(cfg: Config, wallbox: str) -> Callable[[HaInputs], str]:
    """„Automatisch“ oder der Name des Fahrzeugs, das diese Wallbox übersteuert.

    Dieselbe Regel wie ``overrideVehicle`` im UI: Die Absicht nennt die Wallbox, und Health
    bestätigt die Übersteuerung, zeigt sie wartend oder kennt den Stand nicht (``mode`` null,
    ohne Eintrag, oder Health ist älter als die Absicht). Eine beendete Übersteuerung bleibt in
    der Absicht stehen, gilt aber nicht.
    """

    def value(inputs: HaInputs) -> str:
        intent = inputs.intent
        if intent is None:
            return UNKNOWN
        version = inputs.intent_version
        current = version is not None and version >= intent.version
        for key, vehicle in intent.vehicles.items():
            if vehicle.wallbox != wallbox or key not in cfg.vehicles:
                continue
            shown = _entry(inputs.vehicles, key)
            if not current or shown is None or shown.get("mode") is None:
                return cfg.vehicles[key].name
            override = shown.get("assignment") == ASSIGNMENT_OVERRIDE
            active = override and shown.get("wallbox") == wallbox
            if active or shown.get("override_pending"):
                return cfg.vehicles[key].name
        return AUTOMATIC

    return value


def wallbox_type_label(wallbox: WallboxConfig) -> str:
    """Art der Ladestation, wie das UI sie nennt, wenn kein eigener Name eingestellt ist."""
    return "EVCS" if isinstance(wallbox, EvcsConfig) else "Wall Connector"


def _vehicle_wallbox(cfg: Config, key: str) -> Callable[[HaInputs], str]:
    """Anzeigename der Wallbox des Fahrzeugs wie im UI: eigener Name, sonst die Art."""

    def value(inputs: HaInputs) -> str:
        item = _entry(inputs.vehicles, key)
        if item is None:
            return UNKNOWN
        wallbox = item.get("wallbox")
        if not isinstance(wallbox, str):
            return NO_WALLBOX
        if wallbox in inputs.wallbox_names:
            return inputs.wallbox_names[wallbox]
        known = cfg.wallboxes.get(wallbox)
        return wallbox_type_label(known) if known is not None else wallbox

    return value


def _occupant(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        item = _entry(inputs.wallboxes, key)
        if item is None:
            return UNKNOWN
        name = item.get("name")
        return name if isinstance(name, str) else NO_VEHICLE

    return value


def _assignment_unclear(inputs: HaInputs) -> str:
    entries = [*inputs.vehicles, *inputs.wallboxes]
    if not entries:
        return UNKNOWN
    return on_off(any(item.get("assignment") == UNCLEAR for item in entries))


def _control(
    oid: str,
    component: Literal["select", "switch", "button"],
    name: str,
    device: DeviceSpec,
    value: Callable[[HaInputs], str],
    options: tuple[str, ...] = (),
) -> EntitySpec:
    return EntitySpec(oid, component, name, device, value, options=options, command=True)


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


def _month_peak_kw(inputs: HaInputs) -> str:
    return fmt(None if inputs.month_peak is None else inputs.month_peak.kw, 1)


def _month_peak_attributes(inputs: HaInputs) -> Mapping[str, Any]:
    peak = inputs.month_peak
    return {"slot": None if peak is None else peak.slot_start.astimezone(UTC).isoformat()}


# Der core führt den Plan noch nicht aus (Plan 3c); die Plan-Sensoren sagen das jedem Leser.
PREVIEW: Final[Mapping[str, Any]] = {"preview": True}


def _plan_slot_state(pick: Callable[[HaInputs], PlanSlot | None]) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        slot = pick(inputs)
        return UNKNOWN if slot is None else INTENT_LABELS[slot.intent.kind]

    return value


def _plan_slot_attributes(
    pick: Callable[[HaInputs], PlanSlot | None],
) -> Callable[[HaInputs], Mapping[str, Any]]:
    def attributes(inputs: HaInputs) -> Mapping[str, Any]:
        slot = pick(inputs)
        if slot is None or inputs.plan is None:
            return dict(PREVIEW)
        return {
            **PREVIEW,
            "reason": slot_text(slot),
            "codes": [reason.code for reason in slot.reasons],
            "planned_soc_pct": round(slot.soc_end_pct, 1),
            "grid_charge_w": slot.intent.grid_charge_w,
            "start": slot.start.astimezone(LOCAL_TZ).isoformat(),
            "source": inputs.plan.source,
        }

    return attributes


def _plan_now(inputs: HaInputs) -> PlanSlot | None:
    return inputs.plan_now


def _plan_next(inputs: HaInputs) -> PlanSlot | None:
    return inputs.plan_next


def _planned_soc(inputs: HaInputs) -> str:
    return fmt(None if inputs.plan_now is None else inputs.plan_now.soc_end_pct, 1)


def _planned_soc_attributes(inputs: HaInputs) -> Mapping[str, Any]:
    """SOC (zu Beginn des Slots) je volle Stunde des Plans, Zeiten lokal wie bei ``prices``."""
    if inputs.plan is None:
        return {**PREVIEW, "hourly": []}
    return {
        **PREVIEW,
        "hourly": [
            {
                "start": slot.start.astimezone(LOCAL_TZ).isoformat(),
                "soc_pct": round(slot.soc_start_pct, 1),
            }
            for slot in inputs.plan.slots
            if slot.start.astimezone(LOCAL_TZ).minute == 0
        ],
    }


def _preview_attributes(inputs: HaInputs) -> Mapping[str, Any]:
    return dict(PREVIEW)


def _budget_kw(inputs: HaInputs) -> str:
    return fmt(None if inputs.plan_now is None else inputs.plan_now.budget_kw, 1)


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
        EntitySpec(
            "month_peak",
            "sensor",
            "Monatsspitze",
            main,
            _month_peak_kw,
            unit="kW",
            device_class="power",
            state_class="measurement",
            attributes=_month_peak_attributes,
        ),
        EntitySpec(
            "battery_plan_now",
            "sensor",
            "Akku-Plan jetzt",
            main,
            _plan_slot_state(_plan_now),
            attributes=_plan_slot_attributes(_plan_now),
        ),
        EntitySpec(
            "battery_plan_next",
            "sensor",
            "Akku-Plan nächster Slot",
            main,
            _plan_slot_state(_plan_next),
            attributes=_plan_slot_attributes(_plan_next),
        ),
        EntitySpec(
            "planned_soc",
            "sensor",
            "Geplanter SOC",
            main,
            _planned_soc,
            unit="%",
            state_class="measurement",
            attributes=_planned_soc_attributes,
        ),
        EntitySpec(
            "budget",
            "sensor",
            "Budget",
            main,
            _budget_kw,
            unit="kW",
            device_class="power",
            state_class="measurement",
            attributes=_preview_attributes,
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
        EntitySpec(
            "watchdog_tripped",
            "binary_sensor",
            "Watchdog ausgelöst",
            main,
            _watchdog_tripped,
            device_class="problem",
        ),
        _control("regulation", "switch", "Regelbetrieb", main, _regulation_state),
        _control(
            "guest_mode",
            "select",
            "Gast-Lademodus",
            main,
            _guest_mode_state,
            tuple(MODE_LABELS.values()),
        ),
        _control(
            "grid_charge",
            "switch",
            "Netzladen erlaubt",
            main,
            lambda i: on_off(i.grid_charge_allowed),
        ),
        _control("replan", "button", "Neu planen", main, lambda _: ""),
        EntitySpec(
            "problem_assignment_unclear",
            "binary_sensor",
            "Zuordnung unklar",
            main,
            _assignment_unclear,
            device_class="problem",
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
        entities += [
            _control(
                f"{oid}_mode",
                "select",
                "Lademodus",
                device,
                _mode_state(key),
                tuple(MODE_LABELS.values()),
            ),
            _control(f"{oid}_boost", "button", "Boost", device, lambda _: ""),
            _control(f"{oid}_boost_stop", "button", "Boost beenden", device, lambda _: ""),
            EntitySpec(f"{oid}_wallbox", "sensor", "Wallbox", device, _vehicle_wallbox(cfg, key)),
        ]
    options = (AUTOMATIC, *(vehicle.name for vehicle in cfg.vehicles.values()))
    for key, wallbox in cfg.wallboxes.items():
        oid = object_id(key)
        label = wallbox_type_label(wallbox)
        device = DeviceSpec(f"{NODE_ID}_{oid}", f"BindaEMS {label}", label)
        entities += [
            _power(f"{oid}_power", "Ladeleistung", device, _wallbox_w(key)),
            _control(
                f"{oid}_assignment",
                "select",
                "Zuordnung",
                device,
                _assignment_state(cfg, key),
                options,
            ),
            EntitySpec(f"{oid}_occupant", "sensor", "Fahrzeug", device, _occupant(key)),
        ]
    ids = [spec.object_id for spec in entities]
    for oid in ids:
        if ids.count(oid) > 1:  # die Konfiguration schließt das aus; hier nie still doppeln
            raise ValueError(f"Objekt-ID „{oid}“ gehört zu mehreren Entitäten")
    return entities


def discovery(spec: EntitySpec, mqtt: HaMqttConfig) -> tuple[str, str]:
    """Discovery-Topic und -Payload (kompaktes JSON mit sortierten Schlüsseln).

    Ein Bedienelement trägt zusätzlich ``command_topic`` (``{base}/cmd/<object_id>``); ein
    ``button`` hat keinen Zustand und darum kein ``state_topic``.
    """
    base = mqtt.base_topic
    payload: dict[str, Any] = {
        "name": spec.name,
        "unique_id": f"{NODE_ID}_{spec.object_id}",
        "has_entity_name": True,
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
    if spec.component != "button":
        payload["state_topic"] = f"{base}/state/{spec.object_id}"
    if spec.command:
        payload["command_topic"] = f"{base}/cmd/{spec.object_id}"
    if spec.attributes is not None:
        payload["json_attributes_topic"] = f"{base}/state/{spec.object_id}/attributes"
    if spec.component in ("binary_sensor", "switch"):
        payload["payload_on"], payload["payload_off"] = "ON", "OFF"
    elif spec.component == "select":
        payload["options"] = list(spec.options)
    elif spec.component == "button":
        payload["payload_press"] = "PRESS"
    topic = f"{mqtt.discovery_prefix}/{spec.component}/{NODE_ID}/{spec.object_id}/config"
    return topic, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def build_inputs(
    live: LiveState,
    prices: PriceService,
    store: PriceStore,
    forecast: ForecastService,
    now: datetime,
    intent: Intent | None = None,
    wallbox_names: Mapping[str, str] | None = None,
    month_peak: MonthPeak | None = None,
    plan: Plan | None = None,
    grid_charge_allowed: bool | None = None,
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
    watchdog = health.get("watchdog") if health is not None else None
    watchdog_state = watchdog.get("state") if isinstance(watchdog, dict) else None
    adapters = health.get("adapters") if health is not None else None
    vehicles = health.get("vehicles") if health is not None else None
    wallboxes = health.get("wallboxes") if health is not None else None
    intent_version = health.get("intent_version") if health is not None else None
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
    valid = plan if plan is not None and plan.valid_at(now) else None
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
        watchdog_state=watchdog_state if isinstance(watchdog_state, str) else None,
        intent=intent,
        intent_version=intent_version if isinstance(intent_version, int) else None,
        vehicles=_dicts(vehicles),
        wallboxes=_dicts(wallboxes),
        wallbox_names=dict(wallbox_names or {}),
        month_peak=month_peak,
        plan=valid,
        plan_now=valid.slot_at(now) if valid is not None else None,
        plan_next=valid.slot_at(now + SLOT) if valid is not None else None,
        grid_charge_allowed=grid_charge_allowed,
    )
