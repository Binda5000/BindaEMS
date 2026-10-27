"""Home-Assistant-Entitäten der app (Spec 12): Sensoren und Bedienelemente (Plan 2d, Präz. 15).

Unbekannte Werte werden als ``None`` veröffentlicht; Home Assistant zeigt dann „unbekannt“.
Bedienelemente (``select``, ``switch``, ``number``, ``text``, ``button``) tragen ``command=True``;
ihre Befehle kommen über ``{base_topic}/cmd/<object_id>`` und führt ``ha.commands`` aus.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from bindaems import __version__
from bindaems.app.control.intent import ASSIGNMENT_OVERRIDE
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.service import ForecastService
from bindaems.app.learning.driving_profile import (
    Confidence,
    TripSuggestion,
    VehicleProfile,
    WeekdayProfile,
    next_trip,
    weekday_of,
)
from bindaems.app.ledger.service import MonthPeak
from bindaems.app.planning.execution import ExecutionState, execution_state
from bindaems.app.planning.explain import slot_text
from bindaems.app.planning.vehicles import full_power_w
from bindaems.app.prices.pipeline import TOMORROW_EXPECTED_FROM
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore
from bindaems.shared.config import (
    ASSIGNMENT_AUTOMATIC,
    HA_THERMAL_PREFIX,
    THERMAL_DEFAULT_LABEL,
    Config,
    EvcsConfig,
    HaMqttConfig,
    WallboxConfig,
    ha_object_id,
)
from bindaems.shared.control import (
    TARGET_SOC_MAX,
    TARGET_SOC_MIN,
    ChargeMode,
    Intent,
    VehicleIntent,
    _local_instant,
)
from bindaems.shared.domain import Value
from bindaems.shared.numbers import finite_number
from bindaems.shared.plan import BatteryIntentKind, Plan, PlanSlot, PlanVehicle
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, slot_start

if TYPE_CHECKING:  # nur für Typen: ``charging.sessions`` importiert selbst aus diesem Modul
    from bindaems.app.charging.sessions import ChargingSession
    from bindaems.app.stats.service import TodayStats

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
    ChargeMode.PLANNED: "Geplant",
}
# Ein Gast kann nicht „Geplant“ laden: kein Ladestand, keine Abfahrt (Plan 4b, Entscheidung 2)
GUEST_MODE_LABELS: Final[dict[ChargeMode, str]] = {
    mode: label for mode, label in MODE_LABELS.items() if mode is not ChargeMode.PLANNED
}
# Konfidenz und Grundlage des Vorschlags (Plan 5b, Texte): „Wochentag“ oder die Gruppe des Tages
CONFIDENCE_LABELS: Final[dict[Confidence, str]] = {
    "high": "hoch",
    "medium": "mittel",
    "low": "niedrig",
}
BASIS_WEEKDAY: Final = "Wochentag"
GROUP_LABELS: Final = ("Gruppe Mo–Fr", "Gruppe Sa", "Gruppe So/Feiertag")  # Gruppen 0–4, 5, 6
YES: Final = "ja"  # Zustände von „Ziel erreichbar“ (Texte, „HA“)
NO: Final = "nein"
SOC_SOURCES: Final = ("measured", "estimated", "assumed")  # ``soc_source`` in Health
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
# Betriebsarten der Klimaanlage wie ``core.control.thermal.ThermalMode`` (Health ``thermal``)
THERMAL_MODES: Final = ("heizen", "kuehlen", "lueften", "aus", "unbekannt")
THERMAL_SHORT_NAME: Final = "Klima"  # so beginnen die Sensoren des Standardnamens


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
    execution: ExecutionState | None = None  # Stand der Ausführung des Plans; None = unbekannt
    support_allowed: bool | None = (
        None  # „Akku-Unterstützung erlaubt“ der Absicht; None = unbekannt
    )
    support_soc_pct: float | None = None  # Unterstützungsschwelle (Absicht, sonst Konfiguration)
    stats: TodayStats | None = None  # Ersparnis und Autarkie (Plan 4a); None = unbekannt
    # je Fahrzeug (Schlüssel aus ``config.yaml``) die laufende oder letzte Ladesession; ein
    # fehlender Schlüssel oder ``None`` heißt: keine bekannt
    sessions: Mapping[str, ChargingSession | None] = field(default_factory=dict)
    month_kwh: Mapping[str, float | None] = field(default_factory=dict)  # Ladeenergie im Monat
    # die geplanten Autos des gültigen Plans (Plan 4b), ohne erwartete (Plan 5b)
    plan_vehicles: Sequence[PlanVehicle] = ()
    # Plan 5b: das Profil je Fahrzeug (Schlüssel aus ``config.yaml``); ein fehlender Schlüssel heißt
    # „noch kein Profil“
    driving_profiles: Mapping[str, VehicleProfile] = field(default_factory=dict)
    # Plan 5b: Schalter „Automatik Ziel und Abfahrt“ je Fahrzeug; fehlt der Schlüssel: unbekannt
    auto_target: Mapping[str, bool] = field(default_factory=dict)
    # Health ``thermal``: je Gerät (Schlüssel aus ``config.yaml``) die Werte der Klimaanlage
    thermal: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    now: datetime | None = None  # Zeitpunkt der Werte; None = unbekannt


@dataclass(frozen=True)
class EntitySpec:
    object_id: str
    component: Literal["sensor", "binary_sensor", "select", "switch", "number", "time", "button"]
    name: str
    device: DeviceSpec
    value: Callable[[HaInputs], str]
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    attributes: Callable[[HaInputs], Mapping[str, Any]] | None = None
    options: tuple[str, ...] = ()  # Auswahl eines ``select``, Zustände eines ``enum``-Sensors
    command: bool = False  # nimmt Befehle über ``{base}/cmd/<object_id>`` an
    min: float | None = None  # Bereich und Schritt einer ``number``
    max: float | None = None
    step: float | None = None


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


def _thermal_item(name: str, inputs: HaInputs) -> Mapping[str, Any]:
    item = inputs.thermal.get(name)
    return item if isinstance(item, Mapping) else {}


def _thermal_offset(name: str) -> Callable[[HaInputs], str]:
    """Der Versatz, den die UVR anwendet (Echo ``offset_active_k``), sonst der gesendete.

    Das Echo ist, was die Anlage wirklich tut; fehlt es (UVR-Ausgang nicht konfiguriert,
    Paketverlust), zeigt der Sensor ersatzweise den zuletzt gesendeten Wert ``offset_sent_k``.
    """

    def value(inputs: HaInputs) -> str:
        item = _thermal_item(name, inputs)
        echo = _number(item.get("offset_active_k"))
        return fmt(echo if echo is not None else _number(item.get("offset_sent_k")), 1)

    return value


def _thermal_mode(name: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        mode = _thermal_item(name, inputs).get("mode")
        return mode if isinstance(mode, str) and mode in THERMAL_MODES else UNKNOWN

    return value


def _thermal_number(name: str, key: str, decimals: int) -> Callable[[HaInputs], str]:
    return lambda inputs: fmt(_number(_thermal_item(name, inputs).get(key)), decimals)


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


def _target_soc_state(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        if inputs.intent is None:
            return UNKNOWN
        return str(inputs.intent.vehicles.get(key, VehicleIntent()).target_soc_pct)

    return value


def _departure_state(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        if inputs.intent is None:
            return UNKNOWN
        return f"{inputs.intent.vehicles.get(key, VehicleIntent()).departure}:00"  # HH:MM:SS

    return value


@dataclass(frozen=True)
class PlannedView:
    """„Fertig um“ und „Ziel erreichbar“ eines Autos in „Geplant“ (Plan 4b, Entscheidung 12).

    - ``done_by``: wann das Auto voraussichtlich am Ziel ist; ``None`` ohne Laden (etwa am Ziel)
    - ``reachable``: erreicht es das Ziel bis zur Abfahrt?
    - ``expected_soc_pct``: der Ladestand an der Abfahrt; ``None`` unbekannt
    - ``departure_at``: die nächste Abfahrt; ``soc_source`` die Quelle des Ladestands
    """

    done_by: datetime | None
    reachable: bool
    expected_soc_pct: float | None
    departure_at: datetime
    soc_source: str | None


def _moment(value: Any) -> datetime | None:
    """Zeitpunkt aus ISO-Text mit Zeitzone (Health); sonst ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ensure_utc(moment) if moment.tzinfo is not None else None


def planned_view(cfg: Config, key: str, inputs: HaInputs) -> PlannedView | None:
    """Der Stand von „Geplant“ für das Fahrzeug ``key``; ``None``, wenn es nicht geplant lädt.

    Meldet Health das Ziel erreicht (``reached``), gilt das. Sonst gelten, wenn der gültige Plan
    das Auto nennt, sein Ende des Ladens (``finish_at``) und ``reachable``; ohne Plan für das Auto
    rechnet die app aus der spätesten Startzeit in Health (``planned_from_health``). Nicht geplant
    ist ein Auto, dessen Absicht einen anderen Modus nennt oder das laut Health abgesteckt ist.
    Ein erwartetes Auto des Plans (Plan 5b) zählt nicht als Plan für das Auto.
    """
    intent = inputs.intent
    mode = None if intent is None else intent.vehicles.get(key, VehicleIntent()).mode
    if mode is not None and mode is not ChargeMode.PLANNED:
        return None
    entry = _entry(inputs.vehicles, key)
    if entry is not None and entry.get("plugged") is False:
        return None
    source = entry.get("soc_source") if entry is not None else None
    soc_source = source if source in SOC_SOURCES else None
    planned = entry.get("planned") if entry is not None else None
    reached = isinstance(planned, Mapping) and planned.get("state") == "reached"
    car = next((car for car in inputs.plan_vehicles if car.key == key and not car.expected), None)
    if car is not None and not reached:
        if soc_source is None and car.soc_assumed:
            soc_source = "assumed"
        return PlannedView(
            car.finish_at, car.reachable, car.expected_soc_pct, car.departure_at, soc_source
        )
    if entry is None:
        return None
    return planned_from_health(cfg, key, entry, inputs.now, soc_source)


def planned_from_health(
    cfg: Config,
    key: str,
    entry: Mapping[str, Any],
    now: datetime | None,
    soc_source: str | None,
) -> PlannedView | None:
    """„Fertig um“ und „Ziel erreichbar“ ohne Plan, aus dem Teil ``planned`` in Health.

    Das Auto lädt ab der spätesten Startzeit (ist sie erreicht, ``due``: ab jetzt) mit voller
    Leistung ``full_power_w`` an seiner Wallbox, bis der Bedarf ``need_kwh`` geladen ist. Reicht
    die Zeit bis zur Abfahrt nicht, ist das Ziel nicht erreichbar, und der erwartete Ladestand ist
    der, den die volle Leistung bis zur Abfahrt bringt. Ohne Bedarf oder am Ziel (``reached``) gibt
    es kein „Fertig um“, das Ziel ist erreichbar. „Fertig um“ ist auf die volle Minute aufgerundet:
    Ab jetzt gerechnet änderte es sich sonst mit jedem Publish.
    """
    planned = entry.get("planned")
    if not isinstance(planned, Mapping):
        return None
    departure_at = _moment(planned.get("departure_at"))
    need = finite_number(planned.get("need_kwh"))
    if departure_at is None or need is None:
        return None
    soc = finite_number(entry.get("soc_pct"))
    if planned.get("state") == "reached" or need <= 0:
        return PlannedView(None, True, soc, departure_at, soc_source)
    start = _moment(planned.get("latest_start"))
    at = entry.get("wallbox")
    wallbox = cfg.wallboxes.get(at) if isinstance(at, str) else None
    if start is None or wallbox is None:
        return None
    vehicle = cfg.vehicles[key]
    full_kw = full_power_w(vehicle, wallbox, cfg.grid.voltage_nominal_v) / 1000
    if now is not None:
        start = ensure_utc(now) if planned.get("state") == "due" else max(start, ensure_utc(now))
    done_by = _ceil_minute(start + timedelta(hours=need / full_kw))
    if done_by <= departure_at:
        return PlannedView(
            done_by, True, finite_number(planned.get("target_soc_pct")), departure_at, soc_source
        )
    hours = max(0.0, (departure_at - start).total_seconds() / 3600)
    gained = hours * full_kw * vehicle.charge_efficiency_pct / 100 / vehicle.usable_kwh * 100
    expected = None if soc is None else min(100.0, soc + gained)
    return PlannedView(done_by, False, expected, departure_at, soc_source)


def _ceil_minute(moment: datetime) -> datetime:
    """``moment`` auf die volle Minute aufgerundet; eine volle Minute bleibt."""
    floor = moment.replace(second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(minutes=1)


def _done_by(cfg: Config, key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        view = planned_view(cfg, key, inputs)
        if view is None or view.done_by is None:
            return UNKNOWN
        return view.done_by.astimezone(LOCAL_TZ).isoformat()

    return value


def _target_reachable(cfg: Config, key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        view = planned_view(cfg, key, inputs)
        if view is None:
            return UNKNOWN
        return YES if view.reachable else NO

    return value


def _target_reachable_attributes(cfg: Config, key: str) -> Callable[[HaInputs], Mapping[str, Any]]:
    """Erwarteter Ladestand an der Abfahrt, die Abfahrt (Ortszeit) und die Quelle des
    Ladestands; ``None`` ohne geplantes Laden."""

    def attributes(inputs: HaInputs) -> Mapping[str, Any]:
        view = planned_view(cfg, key, inputs)
        if view is None:
            return {"expected_soc_pct": None, "departure_at": None, "soc_source": None}
        expected = view.expected_soc_pct
        return {
            "expected_soc_pct": None if expected is None else round(expected, 1),
            "departure_at": view.departure_at.astimezone(LOCAL_TZ).isoformat(),
            "soc_source": view.soc_source,
        }

    return attributes


@dataclass(frozen=True)
class Suggestion:
    """Die nächste erwartete Fahrt eines Autos und der Wochentag, dessen Profil sie nennt."""

    trip: TripSuggestion
    weekday: WeekdayProfile

    @property
    def basis(self) -> str:
        """„Wochentag“, bei einem Profil aus der Gruppe der Name der Gruppe des Tages."""
        if self.weekday.basis == "weekday":
            return BASIS_WEEKDAY
        weekday = self.weekday.weekday
        return GROUP_LABELS[0] if weekday < 5 else GROUP_LABELS[weekday - 4]


def suggestion(key: str, inputs: HaInputs) -> Suggestion | None:
    """Die nächste erwartete Fahrt des Autos ``key``; ``None`` ohne Profil, ohne Zeitpunkt oder
    ohne Fahrt in den nächsten Tagen (Plan 5b: unbekannt bleibt unbekannt)."""
    profile = inputs.driving_profiles.get(key)
    if profile is None or inputs.now is None:
        return None
    trip = next_trip(profile, inputs.now)
    if trip is None:
        return None
    # ein Feiertag gilt wie ein Sonntag (Eintrag 6 des Profils)
    return Suggestion(trip, profile.weekdays[weekday_of(trip.date)])


def _suggested_departure(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        found = suggestion(key, inputs)
        if found is None:
            return UNKNOWN
        hour, minute = (int(part) for part in found.trip.departure.split(":"))
        # Tag und Uhrzeit der Fahrt sind lokal; HA bekommt den Zeitpunkt in UTC
        return _local_instant(found.trip.date, time(hour, minute)).isoformat()

    return value


def _suggested_target(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        found = suggestion(key, inputs)
        if found is None or found.trip.target_soc_pct is None:
            return UNKNOWN
        return str(found.trip.target_soc_pct)

    return value


def _suggestion_attributes(key: str, *, target: bool) -> Callable[[HaInputs], Mapping[str, Any]]:
    """Konfidenz („hoch“, „mittel“, „niedrig“) und Grundlage des Vorschlags für die Abfahrt oder
    das Ziel; ``None`` ohne den jeweiligen Vorschlag."""

    def attributes(inputs: HaInputs) -> Mapping[str, Any]:
        found = suggestion(key, inputs)
        if found is None or (target and found.trip.target_soc_pct is None):
            return {"confidence": None, "basis": None}
        confidence = found.trip.target_confidence if target else found.trip.departure_confidence
        if confidence is None:
            return {"confidence": None, "basis": None}
        return {"confidence": CONFIDENCE_LABELS[confidence], "basis": found.basis}

    return attributes


def _next_departure(key: str) -> Callable[[HaInputs], str]:
    """Die nächste Abfahrt aus Health (``planned.departure_at``) als UTC-Zeitpunkt."""

    def value(inputs: HaInputs) -> str:
        entry = _entry(inputs.vehicles, key)
        planned = entry.get("planned") if entry is not None else None
        moment = _moment(planned.get("departure_at")) if isinstance(planned, Mapping) else None
        return UNKNOWN if moment is None else moment.isoformat()

    return value


def _auto_target_state(key: str) -> Callable[[HaInputs], str]:
    return lambda inputs: on_off(inputs.auto_target.get(key))


def _unreachable(cfg: Config, inputs: HaInputs) -> list[str]:
    """Die Namen der Fahrzeuge, deren Ziel-SOC nicht erreichbar ist, in der Reihenfolge der
    Konfiguration."""
    names = []
    for key, vehicle in cfg.vehicles.items():
        view = planned_view(cfg, key, inputs)
        if view is not None and not view.reachable:
            names.append(vehicle.name)
    return names


def _target_unreachable(cfg: Config) -> Callable[[HaInputs], str]:
    """AN, wenn ein Auto in „Geplant“ sein Ziel nicht erreicht; ohne core und ohne Plan für ein
    Auto unbekannt (ein erwartetes Auto ist kein Plan für ein Auto)."""

    def value(inputs: HaInputs) -> str:
        planned = any(not car.expected for car in inputs.plan_vehicles)
        if not inputs.core_connected and not planned:
            return UNKNOWN
        return on_off(bool(_unreachable(cfg, inputs)))

    return value


def _regulation_state(inputs: HaInputs) -> str:
    return on_off(None if inputs.intent is None else inputs.intent.control)


def _support_allowed_state(inputs: HaInputs) -> str:
    return on_off(inputs.support_allowed)


def _support_soc_state(inputs: HaInputs) -> str:
    return fmt(inputs.support_soc_pct, 1)


def support_of(
    intent: Intent | None, default_pct: float | None
) -> tuple[bool | None, float | None]:
    """„Akku-Unterstützung erlaubt“ und Unterstützungsschwelle für HA aus der Absicht.

    Ohne Schwelle in der Absicht gilt ``default_pct`` (``control.support_soc_pct``); ohne Absicht
    ist beides unbekannt.
    """
    if intent is None:
        return None, None
    soc = intent.support.soc_pct
    return intent.support.allowed, default_pct if soc is None else soc


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


def _eur(value: float | None) -> float | None:
    number = _number(value)
    return None if number is None else round(number, 2)


def _percent(fraction: float | None) -> float | None:
    number = _number(fraction)
    return None if number is None else number * 100


def _savings(month: bool) -> Callable[[HaInputs], str]:
    """Ersparnis „ohne Anlage“ von heute bzw. vom Monat bis heute (Plan 4a, Präzisierung 13)."""

    def value(inputs: HaInputs) -> str:
        stats = inputs.stats
        if stats is None:
            return UNKNOWN
        return fmt(stats.savings_month_eur if month else stats.savings_today_eur, 2)

    return value


def _savings_attributes(month: bool) -> Callable[[HaInputs], Mapping[str, Any]]:
    def attributes(inputs: HaInputs) -> Mapping[str, Any]:
        stats = inputs.stats
        if stats is None:
            return {"same_import_eur": None}
        same = stats.savings_same_import_month_eur if month else stats.savings_same_import_today_eur
        return {"same_import_eur": _eur(same)}

    return attributes


def _autarky_today(inputs: HaInputs) -> str:
    return UNKNOWN if inputs.stats is None else fmt(_percent(inputs.stats.autarky_today), 0)


def _autarky_attributes(inputs: HaInputs) -> Mapping[str, Any]:
    month = None if inputs.stats is None else _percent(inputs.stats.autarky_month)
    return {"month_pct": None if month is None else round(month)}


def _charge_energy(key: str) -> Callable[[HaInputs], str]:
    def value(inputs: HaInputs) -> str:
        session = inputs.sessions.get(key)
        return UNKNOWN if session is None else fmt(session.split.kwh, 1)

    return value


def _kwh(value: float | None) -> float | None:
    number = _number(value)
    return None if number is None else round(number, 3)


def _charge_energy_attributes(key: str) -> Callable[[HaInputs], Mapping[str, Any]]:
    """Die laufende oder letzte Session mit Herkunft der Energie, Kosten und Energie im Monat;
    Zeiten lokal wie bei ``prices``, ``end`` ``None`` solange die Session läuft."""

    def attributes(inputs: HaInputs) -> Mapping[str, Any]:
        month = _kwh(inputs.month_kwh.get(key))
        session = inputs.sessions.get(key)
        if session is None:
            return {
                "start": None,
                "end": None,
                "pv_kwh": None,
                "battery_kwh": None,
                "grid_kwh": None,
                "cost_eur": None,
                "month_kwh": month,
            }
        split = session.split
        return {
            "start": session.start.astimezone(LOCAL_TZ).isoformat(),
            "end": None if session.end is None else session.end.astimezone(LOCAL_TZ).isoformat(),
            "pv_kwh": _kwh(split.pv_kwh),
            "battery_kwh": _kwh(split.battery_kwh),
            "grid_kwh": _kwh(split.grid_kwh),
            "cost_eur": _eur(split.cost_eur),
            "month_kwh": month,
        }

    return attributes


def _execution(inputs: HaInputs) -> Mapping[str, Any]:
    """Der Stand der Ausführung (Plan 3c) an jedem Plan-Sensor, mit und ohne Plan; ``None`` heißt
    unbekannt."""
    return {"execution": inputs.execution}


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
            return _execution(inputs)
        return {
            **_execution(inputs),
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
        return {**_execution(inputs), "hourly": []}
    return {
        **_execution(inputs),
        "hourly": [
            {
                "start": slot.start.astimezone(LOCAL_TZ).isoformat(),
                "soc_pct": round(slot.soc_start_pct, 1),
            }
            for slot in inputs.plan.slots
            if slot.start.astimezone(LOCAL_TZ).minute == 0
        ],
    }


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
            "savings_today",
            "sensor",
            "Ersparnis heute",
            main,
            _savings(month=False),
            unit="EUR",
            device_class="monetary",
            attributes=_savings_attributes(month=False),
        ),
        EntitySpec(
            "savings_month",
            "sensor",
            "Ersparnis Monat",
            main,
            _savings(month=True),
            unit="EUR",
            device_class="monetary",
            attributes=_savings_attributes(month=True),
        ),
        EntitySpec(
            "autarky_today",
            "sensor",
            "Autarkie heute",
            main,
            _autarky_today,
            unit="%",
            state_class="measurement",
            attributes=_autarky_attributes,
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
            attributes=_execution,
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
            tuple(GUEST_MODE_LABELS.values()),
        ),
        _control(
            "grid_charge",
            "switch",
            "Netzladen erlaubt",
            main,
            lambda i: on_off(i.grid_charge_allowed),
        ),
        _control(
            "battery_support",
            "switch",
            "Akku-Unterstützung erlaubt",
            main,
            _support_allowed_state,
        ),
        EntitySpec(
            "support_soc",
            "number",
            "Unterstützungsschwelle",
            main,
            _support_soc_state,
            unit="%",
            command=True,
            min=cfg.battery.reserve_soc_pct,
            max=100.0,
            step=1.0,
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
        EntitySpec(
            "problem_target_unreachable",
            "binary_sensor",
            "Ziel-SOC unerreichbar",
            main,
            _target_unreachable(cfg),
            device_class="problem",
            attributes=lambda i: {"vehicles": _unreachable(cfg, i)},
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
            EntitySpec(
                f"{oid}_charge_energy",
                "sensor",
                "Ladeenergie",
                device,
                _charge_energy(key),
                unit="kWh",
                device_class="energy",  # ohne ``state_class``: HA warnt bei ``measurement``
                attributes=_charge_energy_attributes(key),
            ),
            EntitySpec(
                f"{oid}_target_soc",
                "number",
                "Ziel-SOC",
                device,
                _target_soc_state(key),
                unit="%",
                command=True,
                min=float(TARGET_SOC_MIN),
                max=float(TARGET_SOC_MAX),
                step=1.0,
            ),
            EntitySpec(
                f"{oid}_departure",
                "time",
                "Abfahrtszeit",
                device,
                _departure_state(key),
                command=True,
            ),
            EntitySpec(
                f"{oid}_done_by",
                "sensor",
                "Fertig um",
                device,
                _done_by(cfg, key),
                device_class="timestamp",
            ),
            EntitySpec(
                f"{oid}_target_reachable",
                "sensor",
                "Ziel erreichbar",
                device,
                _target_reachable(cfg, key),
                device_class="enum",
                attributes=_target_reachable_attributes(cfg, key),
                options=(YES, NO),
            ),
            _control(
                f"{oid}_auto_target",
                "switch",
                "Automatik Ziel und Abfahrt",
                device,
                _auto_target_state(key),
            ),
            EntitySpec(
                f"{oid}_suggested_departure",
                "sensor",
                "Vorschlag Abfahrt",
                device,
                _suggested_departure(key),
                device_class="timestamp",
                attributes=_suggestion_attributes(key, target=False),
            ),
            EntitySpec(
                f"{oid}_suggested_target",
                "sensor",
                "Vorschlag Ziel-SOC",
                device,
                _suggested_target(key),
                unit="%",
                attributes=_suggestion_attributes(key, target=True),
            ),
            EntitySpec(
                f"{oid}_next_departure",
                "sensor",
                "Nächste Abfahrt",
                device,
                _next_departure(key),
                device_class="timestamp",
            ),
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
    for key, thermal in cfg.thermal.devices.items():
        # Sensoren des Systems (Hauptgerät). Der Standardname „Klimaanlage“ heißt kurz „Klima“
        # (Plan 6a, Entscheidung 12); ein eigener Name steht ganz davor („Büro Versatz“).
        oid = f"{HA_THERMAL_PREFIX}{object_id(key)}"
        title = THERMAL_SHORT_NAME if thermal.label == THERMAL_DEFAULT_LABEL else thermal.label
        entities += [
            EntitySpec(
                f"{oid}_offset",
                "sensor",
                f"{title} Versatz",
                main,
                _thermal_offset(key),
                unit="K",
                state_class="measurement",
            ),
            EntitySpec(
                f"{oid}_mode",
                "sensor",
                f"{title} Betriebsart",
                main,
                _thermal_mode(key),
                device_class="enum",
                options=THERMAL_MODES,
            ),
            _power(f"{oid}_power", f"{title} Leistung", main, _thermal_number(key, "power_w", 0)),
            EntitySpec(
                f"{oid}_wish",
                "sensor",
                f"{title} Wunsch",
                main,
                _thermal_number(key, "wish", 0),
                state_class="measurement",  # ganze Stufen, ohne Einheit
            ),
        ]
    ids = [spec.object_id for spec in entities]
    for oid in ids:
        if ids.count(oid) > 1:  # die Konfiguration schließt das aus; hier nie still doppeln
            raise ValueError(f"Objekt-ID „{oid}“ gehört zu mehreren Entitäten")
    return entities


def discovery(spec: EntitySpec, mqtt: HaMqttConfig) -> tuple[str, str]:
    """Discovery-Topic und -Payload (kompaktes JSON mit sortierten Schlüsseln).

    Ein Bedienelement trägt zusätzlich ``command_topic`` (``{base}/cmd/<object_id>``); ein
    ``button`` hat keinen Zustand und darum kein ``state_topic``. Eine ``number`` trägt ``min``,
    ``max`` und ``step`` und die Eingabe als Feld (``mode`` ``box``), ein ``select`` und ein
    ``enum``-Sensor ihre ``options``; eine ``time`` hat keine weiteren Felder.
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
    elif spec.component == "select" or spec.options:
        payload["options"] = list(spec.options)
    elif spec.component == "number":
        payload.update({"min": spec.min, "max": spec.max, "step": spec.step, "mode": "box"})
    elif spec.component == "button":
        payload["payload_press"] = "PRESS"
    topic = f"{mqtt.discovery_prefix}/{spec.component}/{NODE_ID}/{spec.object_id}/config"
    return topic, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def retired_discovery(cfg: Config, mqtt: HaMqttConfig) -> tuple[str, ...]:
    """Discovery-Topics, die nicht mehr gelten und beim Verbinden geleert werden.

    Die Abfahrtszeit war ein ``text``; als ``time`` bekommt sie ein anderes Topic, und das alte
    gehaltene Textfeld bliebe in Home Assistant stehen.
    """
    return tuple(
        f"{mqtt.discovery_prefix}/text/{NODE_ID}/{ha_object_id(key)}_departure/config"
        for key in cfg.vehicles
    )


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
    support_default_pct: float | None = None,
    stats: TodayStats | None = None,
    sessions: Mapping[str, ChargingSession | None] | None = None,
    month_kwh: Mapping[str, float | None] | None = None,
    driving_profiles: Mapping[str, VehicleProfile] | None = None,
    auto_target: Mapping[str, bool] | None = None,
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
    thermal = health.get("thermal") if health is not None else None
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
    execution = (
        None
        if grid_charge_allowed is None
        else execution_state(
            plan,
            valid=valid is not None,
            connected=connected,
            health=live.health,
            grid_charge_allowed=grid_charge_allowed,
        )
    )
    support_allowed, support_soc_pct = support_of(intent, support_default_pct)
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
        execution=execution,
        support_allowed=support_allowed,
        support_soc_pct=support_soc_pct,
        stats=stats,
        sessions=dict(sessions or {}),
        month_kwh=dict(month_kwh or {}),
        plan_vehicles=(
            [car for car in valid.vehicles if not car.expected] if valid is not None else []
        ),
        driving_profiles=dict(driving_profiles or {}),
        auto_target=dict(auto_target or {}),
        thermal=(
            {name: item for name, item in thermal.items() if isinstance(item, dict)}
            if isinstance(thermal, dict)
            else {}
        ),
        now=now,
    )
