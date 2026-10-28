"""Konfigurationsschema (``config.yaml``) und Secrets.

``config.yaml`` enthält Geräte, Verbindungen und die **harten Grenzen** (Spec 8.2). Sie wird beim
Start vollständig validiert; unbekannte Schlüssel sind Fehler. Secrets kommen ausschließlich aus
Umgebungsvariablen mit dem Präfix ``BINDAEMS_``.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Phase = Literal["L1", "L2", "L3"]
PhaseMap = tuple[Phase, Phase, Phase]
_DEFAULT_PHASE_MAP: PhaseMap = ("L1", "L2", "L3")
# So heißen die Gäste der Zuordnung (``core.control.fleet``): ``guest`` im Health und
# ``guest@<Wallbox>`` als Schlüssel. Ein Fahrzeug darf nicht so heißen.
_GUEST_KEY = "guest"
_GUEST_PREFIX = f"{_GUEST_KEY}@"
# Die Auswahl der Zuordnung in Home Assistant heißt „Automatisch“ oder trägt einen Fahrzeugnamen;
# kein Fahrzeug darf so heißen (app.ha.entities und app.ha.commands bilden die Namen zurück ab).
ASSIGNMENT_AUTOMATIC = "Automatisch"
# Objekt-IDs der Entitäten in Home Assistant (app.ha.entities). Die Konfiguration prüft damit, dass
# Schlüssel von Fahrzeugen und Wallboxes keine Entität doppeln; ein Test hält die Listen gleich.
HA_SYSTEM_IDS = (
    "price_now",
    "feed_in_price",
    "pv_forecast_today",
    "pv_forecast_tomorrow",
    "month_peak",
    "battery_plan_now",
    "battery_plan_next",
    "planned_soc",
    "budget",
    "grid_power",
    "pv_power",
    "battery_power",
    "house_power",
    "battery_soc",
    "ems_mode",
    "core_online",
    "problem_competitor",
    "problem_prices",
    "problem_device_offline",
    "watchdog_tripped",
    "regulation",
    "guest_mode",
    "grid_charge",
    "battery_support",
    "support_soc",
    "replan",
    "problem_assignment_unclear",
    "savings_today",
    "savings_month",
    "autarky_today",
    "problem_target_unreachable",
)
# Höchststrom der Gäste in „Sofort“: nur mit EVCS (dort allein hat ein Gast einen Aktor). Ein
# Fahrzeug „guest“ stößt schon an ``guest_mode``, deshalb steht die ID nicht in ``HA_SYSTEM_IDS``.
HA_GUEST_LIMIT_ID = "guest_now_max_a"
HA_VEHICLE_SUFFIXES = (
    "soc",
    "mode",
    "boost",
    "boost_stop",
    "wallbox",
    "charge_energy",
    "target_soc",
    "now_max_a",
    "departure",
    "done_by",
    "target_reachable",
    "auto_target",  # Fahrverhalten (Plan 5b, Präzisierung 13)
    "suggested_departure",
    "suggested_target",
    "next_departure",
)
HA_WALLBOX_SUFFIXES = ("power", "assignment", "occupant")
# Sensoren je Klimaanlage: ``thermal_<Schlüssel>_<Endung>`` (Plan 6a, Entscheidung 12). Das Präfix
# hält sie getrennt von Fahrzeugen und Wallboxes mit ähnlichem Schlüssel.
HA_THERMAL_PREFIX = "thermal_"
HA_THERMAL_SUFFIXES = ("offset", "mode", "power", "wish", "planned_offset")
# Schalter „Vorheizen und Vorkühlen“ (Plan 5c): einer für das System, nur mit Klimaanlage
HA_THERMAL_PLAN_ID = "thermal_plan"
# CoE-Einheiten, die in Zehnteln kommen: 1 = °C, 7 = K (Plan 6a, Entscheidung 10)
_TENTHS_UNITS = (1, 7)
# Stufe des Sollwertversatzes in K (Plan 6a, Präzisierung 2: nur 0,5 K)
_STEP_K = 0.5


def ha_object_id(key: str) -> str:
    """Schlüssel aus ``config.yaml`` als Objekt-ID in Home Assistant (``a-z``, ``0-9``, ``_``)."""
    return re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_") or "x"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_phase_map(value: PhaseMap) -> PhaseMap:
    if set(value) != {"L1", "L2", "L3"}:
        raise ValueError("phase_map muss L1, L2 und L3 je genau einmal enthalten")
    return value


class LatLon(_Model):
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]


class SiteConfig(_Model):
    timezone: str = "Europe/Vienna"
    location: LatLon


class GridConfig(_Model):
    phases: Literal[3] = 3
    voltage_nominal_v: Annotated[float, Field(gt=0)] = 230.0
    fuse_a: Annotated[float, Field(gt=0, le=200)]
    fuse_margin_a: Annotated[float, Field(ge=0)] = 2.0
    # Importlimit je Phase in A: Peak Shaving am Cerbo (Spec 8.1), wirkt nur mit dem Multi auf „Ein“
    import_limit_a: Annotated[float, Field(gt=0)] = 20.0
    # optionale maximale Bezugsleistung in kW (Spec 8.2)
    max_import_kw: Annotated[float, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _margin_below_fuse(self) -> Self:
        if self.fuse_margin_a >= self.fuse_a:
            raise ValueError("fuse_margin_a muss kleiner als fuse_a sein")
        return self

    @model_validator(mode="after")
    def _import_limit_below_fuse_margin(self) -> Self:
        if self.import_limit_a >= self.fuse_a - self.fuse_margin_a:
            raise ValueError("import_limit_a muss unter fuse_a − fuse_margin_a liegen")
        return self


class BatteryConfig(_Model):
    usable_kwh: Annotated[float, Field(gt=0)]
    reserve_soc_pct: Annotated[float, Field(ge=0, le=100)]
    max_charge_w: Annotated[float, Field(gt=0)]
    max_discharge_w: Annotated[float, Field(gt=0)]
    soc_max_pct: Annotated[float, Field(gt=0, le=100)] = 100.0

    @model_validator(mode="after")
    def _reserve_below_max(self) -> Self:
        if self.reserve_soc_pct >= self.soc_max_pct:
            raise ValueError("reserve_soc_pct muss kleiner als soc_max_pct sein")
        return self


class MqttConfig(_Model):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 8883
    tls: bool = True
    tls_verify: bool = True
    tls_ca_file: Path | None = None
    username: str | None = None
    portal_id: str | None = None
    keepalive_s: Annotated[float, Field(gt=0)] = 30.0


class ModbusEndpoint(_Model):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 502


class VictronInstances(_Model):
    """Victron-Geräteinstanzen; die Dicts bilden Instanz → logische ID ab."""

    grid: int | None = None
    vebus: int | None = None
    battery: int | None = None
    pv: dict[int, str] = Field(default_factory=dict)
    acload: dict[int, str] = Field(default_factory=dict)
    evcharger: dict[int, str] = Field(default_factory=dict)


class VictronExpected(_Model):
    hub4_mode: int = 1
    # „Optimiert ohne BatteryLife“: 10 normal, 11 SOC unter Min-SOC, 12 Nachladen
    batterylife_states: list[int] = Field(default_factory=lambda: [10, 11, 12])


class WriteBudget(_Model):
    per_hour: Annotated[int, Field(ge=0)] = 12
    per_day: Annotated[int, Field(ge=0)] = 100


class WatchdogConfig(_Model):
    heartbeat_s: Annotated[float, Field(gt=0)] = 10.0
    timeout_s: Annotated[float, Field(gt=0)] = 90.0


class VictronConfig(_Model):
    mqtt: MqttConfig
    modbus: ModbusEndpoint
    instances: VictronInstances = Field(default_factory=VictronInstances)
    expected: VictronExpected = Field(default_factory=VictronExpected)
    max_grid_charge_setpoint_w: Annotated[float, Field(ge=0)]
    persistent_writes: WriteBudget = Field(default_factory=WriteBudget)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    live_allowed: bool = False


class EvcsConfig(_Model):
    type: Literal["victron_evcs_ns"]
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 502
    unit_id: Annotated[int, Field(ge=0, le=247)] = 1
    min_a: int = 6
    max_a: int = 16
    safe_a: int = 6
    phase_map: PhaseMap = _DEFAULT_PHASE_MAP
    live_allowed: bool = False

    _phase_map_valid = field_validator("phase_map")(_check_phase_map)

    @model_validator(mode="after")
    def _currents_ordered(self) -> Self:
        if not 6 <= self.min_a <= self.safe_a <= self.max_a <= 32:
            raise ValueError("es muss gelten: 6 ≤ min_a ≤ safe_a ≤ max_a ≤ 32")
        return self


class TwcConfig(_Model):
    type: Literal["tesla_wall_connector_gen3"]
    host: str
    max_a: Annotated[int, Field(ge=6, le=48)] = 16
    phase_map: PhaseMap = _DEFAULT_PHASE_MAP

    _phase_map_valid = field_validator("phase_map")(_check_phase_map)


WallboxConfig = Annotated[EvcsConfig | TwcConfig, Field(discriminator="type")]


class CoeChannel(_Model):
    """Ein Wert, den das C.M.I. per CoE sendet: Knoten plus Analog- oder Digitalausgang.

    ``unit`` ist die CoE-Einheit des Analogwerts. Einheit 1 (°C) und 7 (K) kommen in Zehnteln, für
    jede andere Einheit ist ``scale`` (Teiler) Pflicht (Plan 6a, Entscheidung 10). Die Einheit ist
    ein Byte im Paket, also 0 bis 255.
    """

    node: Annotated[int, Field(ge=1, le=62)]
    analog: Annotated[int, Field(ge=1, le=64)] | None = None
    digital: Annotated[int, Field(ge=1, le=64)] | None = None
    unit: Annotated[int, Field(ge=0, le=255)] | None = None
    scale: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _one_address(self) -> Self:
        if (self.analog is None) == (self.digital is None):
            raise ValueError("Genau eines von analog und digital muss gesetzt sein.")
        return self

    @model_validator(mode="after")
    def _scale_for_other_units(self) -> Self:
        if (
            self.analog is not None
            and self.unit is not None
            and self.unit not in _TENTHS_UNITS
            and self.scale is None
        ):
            raise ValueError(
                f"Für die Einheit {self.unit} muss scale gesetzt sein (nur die Einheiten "
                "1 und 7 kommen in Zehnteln)."
            )
        return self


class CoeReceive(_Model):
    """Empfangstabelle: Welcher Wert des C.M.I. welche Größe der Klimaanlage ist."""

    actual_c: CoeChannel
    setpoint_c: CoeChannel
    offset_active_k: CoeChannel
    cooling: CoeChannel
    venting: CoeChannel
    heating: CoeChannel
    running: CoeChannel
    power_kw: CoeChannel | None = None
    cmd_on: CoeChannel | None = None

    def channels(self) -> dict[str, CoeChannel]:
        """Die belegten Kanäle unter ihrem Feldnamen (ohne die optionalen, die fehlen)."""
        found = {name: getattr(self, name) for name in type(self).model_fields}
        return {name: channel for name, channel in found.items() if channel is not None}

    @model_validator(mode="after")
    def _each_value_once(self) -> Self:
        # Lesen zwei Größen denselben Wert, wäre etwa beim Kühlen die Betriebsart immer Heizen,
        # und das Vorzeichen des Versatzes kippte.
        seen: dict[tuple[int, str, int], str] = {}
        for name, channel in self.channels().items():
            kind, output = (
                ("analog", channel.analog)
                if channel.analog is not None
                else ("digital", channel.digital)
            )
            if output is None:
                continue  # das prüft ``CoeChannel``
            key = (channel.node, kind, output)
            if key in seen:
                raise ValueError(
                    f"{seen[key]} und {name} lesen denselben Wert "
                    f"(Knoten {channel.node}, {kind} {output})."
                )
            seen[key] = name
        return self


class ThermalStrategyConfig(_Model):
    """Schwellen der Strategie der Klimaanlage (Plan 6a, Entscheidung 5); keine harten Grenzen."""

    more_above_w: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 800.0
    import_above_w: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 300.0
    battery_soc_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 90.0
    less_max_steps: Annotated[int, Field(ge=0, le=6)] = 2


THERMAL_DEFAULT_LABEL: Final = "Klimaanlage"  # ``CoeOffsetConfig.label`` ohne eigenen Namen


class CoeOffsetConfig(_Model):
    """Klimaanlage an einer UVR610: Sollwertversatz per CoE V2 über das C.M.I. (Plan 6a).

    Der Versatz liegt in ``−max_offset_k … +max_offset_k``; die UVR begrenzt nicht, das tut die
    Safety-Schicht mit diesen harten Grenzen (Spec 8.2). Ohne Paket fällt die UVR nach 5 min selbst
    auf 0.
    """

    kind: Literal["coe_offset"]
    label: str = THERMAL_DEFAULT_LABEL
    cmi_host: ipaddress.IPv4Address
    port: Annotated[int, Field(ge=1, le=65535)] = 5442
    listen_port: Annotated[int, Field(ge=1, le=65535)] = 5442
    own_node: Annotated[int, Field(ge=1, le=62)] = 60
    own_output: Annotated[int, Field(ge=1, le=64)] = 1
    max_offset_k: Annotated[float, Field(ge=0.5, le=3.0, allow_inf_nan=False)] = 3.0
    step_k: float = _STEP_K
    step_interval_s: Annotated[float, Field(ge=60, allow_inf_nan=False)] = 600.0
    send_interval_s: Annotated[float, Field(ge=5, le=60, allow_inf_nan=False)] = 30.0
    stale_after_s: Annotated[float, Field(ge=60, le=290, allow_inf_nan=False)] = 180.0
    receive: CoeReceive
    strategy: ThermalStrategyConfig = Field(default_factory=ThermalStrategyConfig)
    live_allowed: bool = False

    @field_validator("step_k")
    @classmethod
    def _step_is_half_a_kelvin(cls, value: float) -> float:
        # ein Literal[0.5] kennt mypy nicht; die Stufe ist fest (Plan 6a, Präzisierung 2)
        if value != _STEP_K:
            raise ValueError("step_k muss 0,5 sein.")
        return value

    @model_validator(mode="after")
    def _max_offset_is_a_multiple_of_the_step(self) -> Self:
        steps = self.max_offset_k / self.step_k
        if abs(steps - round(steps)) > 1e-9:
            raise ValueError("max_offset_k muss ein Vielfaches von step_k sein.")
        return self

    @model_validator(mode="after")
    def _own_node_is_not_a_receive_node(self) -> Self:
        if any(channel.node == self.own_node for channel in self.receive.channels().values()):
            raise ValueError("own_node ist auch ein Empfangsknoten.")
        return self


# 6b ergänzt die Wärmepumpe als weitere Art in dieser Union.
ThermalDevice = Annotated[CoeOffsetConfig, Field(discriminator="kind")]


class ThermalConfig(_Model):
    """Wärme und Kälte: Geräte unter ihrem Namen (``thermal.<name>`` als Aktor-ID)."""

    devices: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")], ThermalDevice] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _one_coe_device(self) -> Self:
        if sum(device.kind == "coe_offset" for device in self.devices.values()) > 1:
            raise ValueError("Phase 6a kennt nur eine Klimaanlage über CoE.")
        return self


class TessieVehicle(_Model):
    vin: Annotated[str, Field(min_length=17, max_length=17)]


class VehicleCommandLimits(_Model):
    """Befehlslimits für Fahrzeugbefehle über Tessie (Spec 6.5).

    Der Ladestrom wird höchstens alle ``current_interval_s`` Sekunden und nur ab
    ``current_step_a`` A Unterschied geändert. ``per_hour`` begrenzt alle Befehle zusammen,
    ``start_stop_per_hour`` davon die Start/Stopp-Befehle.
    """

    current_interval_s: Annotated[float, Field(ge=0)] = 90.0
    current_step_a: Annotated[int, Field(ge=1)] = 2
    per_hour: Annotated[int, Field(ge=0)] = 20
    start_stop_per_hour: Annotated[int, Field(ge=0)] = 6

    @model_validator(mode="after")
    def _start_stop_within_per_hour(self) -> Self:
        if self.start_stop_per_hour > self.per_hour:
            raise ValueError("start_stop_per_hour darf per_hour nicht übersteigen")
        return self


class WakeConfig(_Model):
    """Weckregeln eines Fahrzeugs mit Tessie (Plan 2c-1, Design 7.1).

    Geweckt wird nur zum Starten: höchstens alle ``interval_s`` Sekunden und höchstens
    ``per_day``-mal je Kalendertag (Wiener Zeit).
    """

    interval_s: Annotated[float, Field(ge=0)] = 1800.0
    per_day: Annotated[int, Field(ge=0)] = 6


class VehicleConfig(_Model):
    name: str
    usable_kwh: Annotated[float, Field(gt=0)]
    phases: Literal[1, 2, 3]
    min_a: Annotated[int, Field(ge=1, le=32)]
    max_a: Annotated[int, Field(ge=1, le=32)]
    default_wallbox: str
    live_allowed: bool = False
    tessie: TessieVehicle | None = None
    commands: VehicleCommandLimits = Field(default_factory=VehicleCommandLimits)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    soc_entity: str | None = None
    # device_tracker in Home Assistant, ``home`` heißt zu Hause; der core abonniert ihn selbst unter
    # ``home_signal`` (Plan 2c-2b, Design 3.2)
    home_entity: str | None = None
    home_radius_m: Annotated[float, Field(gt=0)] = 150.0
    # Wirkungsgrad des Ladens in % (Netz bis Akku): Bedarf und späteste Startzeit von „Geplant“
    # rechnen damit, Planer und core gleich (Plan 4b, Entscheidung 5)
    charge_efficiency_pct: Annotated[float, Field(ge=50, le=100)] = 90.0
    # Ladelimit des Tesla in „Geplant“ an das Ziel angleichen (Plan 4b, Entscheidung 11)
    charge_limit_sync: bool = False

    @field_validator("name")
    @classmethod
    def _usable_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name darf nicht leer sein")
        if value != value.strip():
            raise ValueError("name darf nicht mit Leerzeichen beginnen oder enden")
        if value.casefold() == ASSIGNMENT_AUTOMATIC.casefold():
            raise ValueError(f"„{ASSIGNMENT_AUTOMATIC}“ ist in der Zuordnung vorbehalten")
        return value

    @model_validator(mode="after")
    def _min_below_max(self) -> Self:
        if self.min_a > self.max_a:
            raise ValueError("min_a darf nicht größer als max_a sein")
        return self

    @model_validator(mode="after")
    def _live_needs_tessie(self) -> Self:
        if self.live_allowed and self.tessie is None:
            raise ValueError("live_allowed verlangt einen tessie-Abschnitt")
        return self

    @model_validator(mode="after")
    def _limit_sync_needs_tessie(self) -> Self:
        if self.charge_limit_sync and self.tessie is None:
            raise ValueError("charge_limit_sync verlangt einen tessie-Abschnitt")
        return self


class PvPlane(_Model):
    name: str
    kwp: Annotated[float, Field(gt=0)]
    tilt_deg: Annotated[float, Field(ge=0, le=90)]
    azimuth_deg: Annotated[float, Field(ge=-180, le=180)]


class PvConfig(_Model):
    planes: Annotated[list[PvPlane], Field(min_length=1)]
    inverter_ac_max_w: Annotated[float, Field(gt=0)]


class HaMqttConfig(_Model):
    """MQTT Discovery am bestehenden Mosquitto von Home Assistant (eigener Benutzer)."""

    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 1883
    tls: bool = False
    tls_verify: bool = True
    tls_ca_file: Path | None = None
    username: str | None = "bindaems"
    discovery_prefix: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")] = "homeassistant"
    base_topic: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")] = "bindaems"
    publish_interval_s: Annotated[float, Field(gt=0)] = 10.0


HaSensor = Annotated[str, Field(pattern=r"^sensor\.[a-z0-9_]+$")]


class LoadHistoryConfig(_Model):
    """Hauslast aus der HA-Datenbank für Zeiten ohne vollständige Abrechnung (Lastprognose)."""

    entity: HaSensor  # Leistung des Hauses in W oder kW
    subtract: list[HaSensor] = Field(default_factory=list)  # Leistungen, die nicht zählen


class HomeAssistantConfig(_Model):
    url: Annotated[str, Field(pattern=r"^https?://")]
    entities: dict[str, str] = Field(default_factory=dict)
    mqtt: HaMqttConfig | None = None
    notify_service: Annotated[str | None, Field(pattern=r"^notify\.[a-z0-9_]+$")] = None
    load_history: LoadHistoryConfig | None = None


class InfluxConfig(_Model):
    url: Annotated[str, Field(pattern=r"^https?://")]
    database: str = "bindaems"
    username: str | None = None
    ha_database: str | None = None


class TelemetryConfig(_Model):
    spool_dir: Path = Path("/data/spool")
    spool_max_mb: Annotated[int, Field(gt=0)] = 500
    spool_max_age_days: Annotated[int, Field(gt=0)] = 7


class CoreApiConfig(_Model):
    host: str = "0.0.0.0"  # noqa: S104 - nur im internen Docker-Netz erreichbar
    port: Annotated[int, Field(ge=1, le=65535)] = 8081


class AppConfig(_Model):
    """ems-app: HTTP-Server, Datenablage und Anbindung an den core."""

    host: str = "0.0.0.0"  # noqa: S104 - erreichbar nur über den Reverse Proxy
    port: Annotated[int, Field(ge=1, le=65535)] = 8080
    data_dir: Path = Path("/data")
    backup_dir: Path = Path("/backup")
    core_url: Annotated[str, Field(pattern=r"^https?://")] = "http://ems-core:8081"
    trusted_proxies: list[str] = Field(default_factory=list)
    cookie_secure: bool = True
    ui_dir: Path | None = Path("/app/ui")
    decisions_keep_days: Annotated[int, Field(ge=1, le=3650)] = 180  # Entscheidungslog in SQLite

    @field_validator("trusted_proxies")
    @classmethod
    def _proxies_are_networks(cls, value: list[str]) -> list[str]:
        for entry in value:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(
                    f"keine gültige IP-Adresse oder kein gültiges Netz: {entry}"
                ) from None
        return value


class SafetyConfig(_Model):
    """Sicherheitsschicht des ems-core."""

    # Freigabe einer Sperre nach so vielen Sekunden ohne Ursache (Spec 7.9)
    lock_release_s: Annotated[float, Field(gt=0, le=3600)] = 60.0


class ControlConfig(_Model):
    """Parameter der Laderegelung (Plan 2c-1, Design 3.2), keine harten Grenzen.

    Die Safety-Schicht prüft weiter gegen ``grid.fuse_a`` und ``grid.fuse_margin_a``.

    - ``import_margin_a``: Abstand zum Importlimit (``grid.import_limit_a``) für PV-Überschuss und
      Min+PV in A
    - ``surplus_margin_w``: Marge im Überschuss in W (Spec 7.5)
    - ``start_after_s``: So lange muss der Überschuss für einen Start reichen.
    - ``stop_after_s``: So lange liegt er darunter bis zum Stopp.
    - ``min_on_s``, ``min_off_s``: Mindestlaufzeit nach einem Start, Mindestpause nach einem Stopp
    - ``reserve_guard_pct``: Abstand über der USV-Reserve in Prozentpunkten SOC; darunter richten
      sich auch Sofort und Boost am Importlimit aus
    - ``tesla_average_s``: gleitendes Mittel für den Tesla-Strom
    - ``tesla_caution_s``: Fenster der vorsichtigen Zuteilung an den Tesla (Spec 7.3)
    - ``evcs_hysteresis_a``: Hysterese der EVCS beim Erhöhen in A (Design 6.5): Ihr Strom steigt
      erst, wenn der Anteil am Überschuss ihn um 1 A plus diesen Wert übersteigt

    Akku-Sollwert und 15-min-Wächter (Plan 2c-2a, Design 3.2): Die Unterstützung ist die
    Akkuleistung für Autos in PV-Überschuss und Min+PV (Spec 7.6). Diese Werte sind endlich;
    unendliche Werte und NaN lehnt die Konfiguration ab.

    - ``support_soc_pct``: Ab diesem SOC in % ist sie möglich.
    - ``support_hysteresis_pct``: Gesperrt wird sie erst unter der Schwelle minus diesem Wert.
    - ``support_max_w``: höchstens so viel Akkuleistung in W für diese Autos zusammen
    - ``support_max_s``: längste Überbrückung am Stück in s
    - ``setpoint_deadband_w``: Abstand in W, ab dem ein Override entsteht oder sich ändert; über 0,
      sonst entstünde auch ohne gewollten Netzbezug ein Override (auf der Basis)
    - ``override_refresh_s``: So oft in s frischt der core einen Override auf (Cerbo-Timeout 300 s)
    - ``peak_target_kw``: Ziel des 15-min-Wächters in kW; ohne Wert gilt nur das Budget eines Plans
      (ohne beides ruht er)
    - ``peak_margin_kw``: Marge der Hochrechnung des Wächters in kW (Spec 7.4)

    Stundenbudget des Tesla (Plan 2d, Präzisierung 9):

    - ``tesla_reserve_commands``: So viele Befehle je Stunde hält die Regelung für den Tesla frei.
      Ist das Budget (``vehicles.<v>.commands.per_hour``) bis auf diese Reserve verbraucht, ändert
      sie seinen Strom nur noch, wenn ein Wächter es verlangt. Die Reserve hält Start, Stopp und Aus
      nicht zurück; die Limits der Safety-Schicht (``per_hour``, ``start_stop_per_hour``) gelten
      weiter, nur der Stopp in „Aus“ umgeht sie. Der Wert liegt unter ``per_hour`` jedes Fahrzeugs
      mit Tessie.

    Selbststart des Tesla (Plan 2d, Präzisierung 10):

    - ``tesla_against_s``: So lange in s lädt ein Fahrzeug mit Tessie ohne Unterbrechung gegen
      seinen Modus (Tessie: lädt mit mindestens 1 A, der Modus will nicht laden, die Zuordnung ist
      klar), bevor die Regelung „Laden aus“ erneut sendet.
    - ``tesla_against_retry_s``: Frühestens so viele s später folgt je Fahrzeug ein weiterer
      Versuch, solange es gegen den Modus lädt.
    """

    import_margin_a: Annotated[float, Field(ge=0)] = 1.0
    surplus_margin_w: Annotated[float, Field(ge=0)] = 100.0
    start_after_s: Annotated[float, Field(ge=0)] = 120.0
    stop_after_s: Annotated[float, Field(ge=0)] = 300.0
    min_on_s: Annotated[float, Field(ge=0)] = 600.0
    min_off_s: Annotated[float, Field(ge=0)] = 600.0
    reserve_guard_pct: Annotated[float, Field(ge=0, le=100)] = 5.0
    tesla_average_s: Annotated[float, Field(gt=0)] = 90.0
    tesla_caution_s: Annotated[float, Field(gt=0)] = 60.0
    evcs_hysteresis_a: Annotated[float, Field(ge=0)] = 0.5
    support_soc_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 70.0
    support_hysteresis_pct: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 5.0
    support_max_w: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 3000.0
    support_max_s: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 900.0
    setpoint_deadband_w: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 200.0
    # höchstens 280 s: 20 s Reserve zum Timeout des Override am Cerbo (300 s)
    override_refresh_s: Annotated[float, Field(gt=0, le=280, allow_inf_nan=False)] = 240.0
    peak_target_kw: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    peak_margin_kw: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.3
    tesla_reserve_commands: Annotated[int, Field(ge=0)] = 4
    tesla_against_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 120.0
    tesla_against_retry_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 600.0


class CoreConfig(_Model):
    """ems-core: Datenablage für den persistenten Zustand der Steuerung."""

    data_dir: Path = Path("/data")


class Config(_Model):
    site: SiteConfig
    grid: GridConfig
    battery: BatteryConfig
    victron: VictronConfig
    wallboxes: dict[str, WallboxConfig]
    wallbox_priority: list[str] | None = None
    vehicles: dict[str, VehicleConfig]
    pv: PvConfig
    homeassistant: HomeAssistantConfig | None = None
    influxdb: InfluxConfig
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    core_api: CoreApiConfig = Field(default_factory=CoreApiConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    thermal: ThermalConfig = Field(default_factory=ThermalConfig)
    core: CoreConfig = Field(default_factory=CoreConfig)

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        for key, vehicle in self.vehicles.items():
            if key == _GUEST_KEY or key.startswith(_GUEST_PREFIX):
                raise ValueError(
                    f"vehicles.{key}: Der Schlüssel ist dem Gast der Zuordnung vorbehalten "
                    f"(„{_GUEST_KEY}“ und „{_GUEST_PREFIX}<Wallbox>“)"
                )
            if vehicle.default_wallbox not in self.wallboxes:
                raise ValueError(
                    f"vehicles.{key}.default_wallbox: Wallbox „{vehicle.default_wallbox}“ "
                    "existiert nicht"
                )
            if vehicle.soc_entity is not None and self.homeassistant is None:
                raise ValueError(
                    f"vehicles.{key}.soc_entity verlangt einen homeassistant-Abschnitt"
                )
            if vehicle.home_entity is not None and self.homeassistant is None:
                raise ValueError(
                    f"vehicles.{key}.home_entity verlangt einen homeassistant-Abschnitt"
                )
            if (
                vehicle.soc_entity is not None
                and self.homeassistant is not None
                and self.homeassistant.entities.get(soc_signal(key), vehicle.soc_entity)
                != vehicle.soc_entity
            ):
                raise ValueError(
                    f"{soc_signal(key)} ist in homeassistant.entities anders belegt als soc_entity"
                )
        if (
            self.homeassistant is not None
            and self.homeassistant.load_history is not None
            and self.influxdb.ha_database is None
        ):
            raise ValueError("homeassistant.load_history verlangt influxdb.ha_database")
        if self.wallbox_priority is not None and sorted(self.wallbox_priority) != sorted(
            self.wallboxes
        ):
            raise ValueError("wallbox_priority muss jede Wallbox genau einmal enthalten")
        return self

    @model_validator(mode="after")
    def _vehicle_names_are_unique(self) -> Self:
        """Namen sind die Optionen der Zuordnung und müssen sich eindeutig zurückabbilden lassen."""
        seen: dict[str, str] = {}
        for key, vehicle in self.vehicles.items():
            folded = vehicle.name.casefold()
            if folded in seen:
                raise ValueError(
                    f"vehicles.{key}.name: „{vehicle.name}“ gibt es schon bei "
                    f"vehicles.{seen[folded]} (Groß- und Kleinschreibung zählt nicht)"
                )
            seen[folded] = key
        return self

    @model_validator(mode="after")
    def _ha_object_ids_are_unique(self) -> Self:
        """Schlüssel dürfen in Home Assistant keine Entität und kein Gerät doppeln."""
        owners = {object_id: "den Entitäten des Systems" for object_id in HA_SYSTEM_IDS}
        keys: dict[str, str] = {}
        for kind, entries, suffixes, prefix in (
            ("vehicles", self.vehicles, HA_VEHICLE_SUFFIXES, ""),
            ("wallboxes", self.wallboxes, HA_WALLBOX_SUFFIXES, ""),
            ("thermal", self.thermal.devices, HA_THERMAL_SUFFIXES, HA_THERMAL_PREFIX),
        ):
            for key in entries:
                oid = ha_object_id(key)
                if not prefix:  # nur Fahrzeuge und Wallboxes teilen sich ein Gerät je Schlüssel
                    if oid in keys:
                        raise ValueError(
                            f"{kind}.{key}: Objekt-ID „{oid}“ in Home Assistant gehört schon zu "
                            f"{keys[oid]}"
                        )
                    keys[oid] = f"{kind}.{key}"
                for suffix in suffixes:
                    entity = f"{prefix}{oid}_{suffix}"
                    if entity in owners:
                        raise ValueError(
                            f"{kind}.{key}: Die Entität „{entity}“ in Home Assistant gehört "
                            f"schon zu {owners[entity]}"
                        )
                    owners[entity] = f"{kind}.{key}"
        return self

    @model_validator(mode="after")
    def _import_margin_below_import_limit(self) -> Self:
        if self.control.import_margin_a >= self.grid.import_limit_a:
            raise ValueError("control.import_margin_a muss kleiner als grid.import_limit_a sein")
        return self

    @model_validator(mode="after")
    def _reserve_below_hourly_limit(self) -> Self:
        for key, vehicle in self.vehicles.items():
            if vehicle.tessie is not None and (
                self.control.tesla_reserve_commands >= vehicle.commands.per_hour
            ):
                raise ValueError(
                    f"control.tesla_reserve_commands muss unter vehicles.{key}.commands.per_hour "
                    "liegen"
                )
        return self

    @model_validator(mode="after")
    def _live_evcs_needs_gx_instance(self) -> Self:
        """Eine live erlaubte EVCS braucht ihre evcharger-Instanz (Spec 8.6).

        Der Watchdog am Cerbo findet die EVCS nur über diese Instanz; ohne sie erzeugt
        ``watchdog_config`` keine Aktion für den sicheren Strom.
        """
        mapped = set(self.victron.instances.evcharger.values())
        for key, wallbox in self.wallboxes.items():
            if isinstance(wallbox, EvcsConfig) and wallbox.live_allowed and key not in mapped:
                raise ValueError(
                    f"wallboxes.{key}.live_allowed verlangt einen Eintrag „<Instanz>: {key}“ in "
                    "victron.instances.evcharger; ohne ihn setzt der Watchdog am Cerbo für diese "
                    "EVCS keinen sicheren Strom"
                )
        return self

    @property
    def wallbox_order(self) -> list[str]:
        """Wallbox-Reihenfolge für die PV-Zuordnung (Priorität oder Konfigurationsreihenfolge)."""
        if self.wallbox_priority is not None:
            return list(self.wallbox_priority)
        return list(self.wallboxes)


def ha_object_ids(cfg: Config) -> list[str]:
    """Objekt-IDs aller Entitäten, die ``app.ha.entities`` für diese Konfiguration baut."""
    ids = list(HA_SYSTEM_IDS)
    if any(isinstance(box, EvcsConfig) for box in cfg.wallboxes.values()):
        ids.append(HA_GUEST_LIMIT_ID)
    for key in cfg.vehicles:
        ids += [f"{ha_object_id(key)}_{suffix}" for suffix in HA_VEHICLE_SUFFIXES]
    for key in cfg.wallboxes:
        ids += [f"{ha_object_id(key)}_{suffix}" for suffix in HA_WALLBOX_SUFFIXES]
    for key in cfg.thermal.devices:
        oid = f"{HA_THERMAL_PREFIX}{ha_object_id(key)}"
        ids += [f"{oid}_{suffix}" for suffix in HA_THERMAL_SUFFIXES]
    if cfg.thermal.devices:
        ids.append(HA_THERMAL_PLAN_ID)
    return ids


def home_signal(vehicle: str) -> str:
    """Das Signal, unter dem der core ``home_entity`` des Fahrzeugs ``vehicle`` abonniert."""
    return f"vehicle.{vehicle}.home_state"


def soc_signal(vehicle: str) -> str:
    """Das Signal, unter dem der core ``soc_entity`` des Fahrzeugs ``vehicle`` abonniert."""
    return f"vehicle.{vehicle}.soc_pct"


def ha_entities(cfg: Config) -> dict[str, str]:
    """Signal → Entität, die der core in Home Assistant abonniert (Plan 2c-2b, Präzisierung 9).

    Das sind ``homeassistant.entities`` und dazu je Fahrzeug mit ``soc_entity`` ``soc_signal`` →
    ``soc_entity`` und mit ``home_entity`` ``home_signal`` → ``home_entity`` (Plan 4b, Präzisierung
    4); ohne Abschnitt ``homeassistant`` ist es leer. Die Laufzeit gibt diese Zuordnung dem
    HA-Adapter.
    """
    if cfg.homeassistant is None:
        return {}
    entities = dict(cfg.homeassistant.entities)
    for key, vehicle in cfg.vehicles.items():
        if vehicle.soc_entity is not None:
            entities[soc_signal(key)] = vehicle.soc_entity
        if vehicle.home_entity is not None:
            entities[home_signal(key)] = vehicle.home_entity
    return entities


class ConfigError(Exception):
    """Die Konfiguration ist ungültig; die Meldung ist für Menschen gedacht (deutsch)."""


def _format_errors(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        path = ".".join(str(part) for part in item["loc"]) or "<wurzel>"
        lines.append(f"Konfiguration ungültig: {path}: {item['msg']}")
    return "\n".join(lines)


def load_config(path: Path) -> Config:
    """Liest und validiert ``config.yaml``; jeder Fehler wird zu ``ConfigError``."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Konfiguration ungültig: Datei nicht gefunden: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Konfiguration ungültig: YAML-Fehler: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Konfiguration ungültig: die Datei muss ein YAML-Objekt enthalten")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from exc


class Secrets(BaseSettings):
    """Secrets aus Umgebungsvariablen ``BINDAEMS_*`` (nie im Repository)."""

    # leere Einträge (z. B. aus .env.example) gelten als nicht gesetzt
    model_config = SettingsConfigDict(env_prefix="BINDAEMS_", extra="ignore", env_ignore_empty=True)

    mqtt_password: SecretStr | None = None
    tessie_token: SecretStr | None = None
    ha_token: SecretStr | None = None
    influx_password: SecretStr | None = None
    ha_mqtt_password: SecretStr | None = None
    internal_token: SecretStr

    @field_validator("internal_token")
    @classmethod
    def _token_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("internal_token muss mindestens 32 Zeichen lang sein")
        return value


def load_secrets() -> Secrets:
    """Liest die Secrets aus der Umgebung."""
    return Secrets()  # Werte stammen aus der Umgebung
