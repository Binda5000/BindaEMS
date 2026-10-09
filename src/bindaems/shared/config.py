"""Konfigurationsschema (``config.yaml``) und Secrets.

``config.yaml`` enthält Geräte, Verbindungen und die **harten Grenzen** (Spec 8.2). Sie wird beim
Start vollständig validiert; unbekannte Schlüssel sind Fehler. Secrets kommen ausschließlich aus
Umgebungsvariablen mit dem Präfix ``BINDAEMS_``.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Any, Literal, Self

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

    @model_validator(mode="after")
    def _margin_below_fuse(self) -> Self:
        if self.fuse_margin_a >= self.fuse_a:
            raise ValueError("fuse_margin_a muss kleiner als fuse_a sein")
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


class TessieVehicle(_Model):
    vin: Annotated[str, Field(min_length=17, max_length=17)]


class VehicleConfig(_Model):
    name: str
    usable_kwh: Annotated[float, Field(gt=0)]
    phases: Literal[1, 2, 3]
    min_a: Annotated[int, Field(ge=1, le=32)]
    max_a: Annotated[int, Field(ge=1, le=32)]
    default_wallbox: str
    live_allowed: bool = False
    tessie: TessieVehicle | None = None
    soc_entity: str | None = None
    home_radius_m: Annotated[float, Field(gt=0)] = 150.0

    @model_validator(mode="after")
    def _min_below_max(self) -> Self:
        if self.min_a > self.max_a:
            raise ValueError("min_a darf nicht größer als max_a sein")
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


class HomeAssistantConfig(_Model):
    url: Annotated[str, Field(pattern=r"^https?://")]
    entities: dict[str, str] = Field(default_factory=dict)
    mqtt: HaMqttConfig | None = None


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

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        for key, vehicle in self.vehicles.items():
            if vehicle.default_wallbox not in self.wallboxes:
                raise ValueError(
                    f"vehicles.{key}.default_wallbox: Wallbox „{vehicle.default_wallbox}“ "
                    "existiert nicht"
                )
            if vehicle.soc_entity is not None and self.homeassistant is None:
                raise ValueError(
                    f"vehicles.{key}.soc_entity verlangt einen homeassistant-Abschnitt"
                )
        if self.wallbox_priority is not None and sorted(self.wallbox_priority) != sorted(
            self.wallboxes
        ):
            raise ValueError("wallbox_priority muss jede Wallbox genau einmal enthalten")
        return self

    @property
    def wallbox_order(self) -> list[str]:
        """Wallbox-Reihenfolge für die PV-Zuordnung (Priorität oder Konfigurationsreihenfolge)."""
        if self.wallbox_priority is not None:
            return list(self.wallbox_priority)
        return list(self.wallboxes)


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
