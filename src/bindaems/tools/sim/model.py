"""Anlagenmodell des Simulators: Netz, ESS, Akku, PV, Watchdog, Ausfälle und fremder Regler.

Der Simulator lässt die echte Laufzeit des core mit ihren echten Aktoren gegen dieses Modell laufen
(Plan 2c-1, Präzisierung 16). Je simulierter Sekunde ruft der Lauf ``step`` (eine Sekunde Physik)
und ``publish`` (die Signale in den Zustandsspeicher), nach dem Zyklus des core ``resolve`` (fällige
Antworten von Tessie). Der core schreibt über ``mqtt_write`` statt über den Victron-Adapter und
erreicht Tessie über ``tessie``, den Handler für ``httpx.MockTransport``.

**Topologie** (Design 13.1): Haus und Wallboxen hängen am AC-Ausgang der Multis, die PV am
AC-Eingang, gleich auf drei Phasen. Je Phase gilt ``P_acout = Haus + Wallboxen``,
``P_acin = P_acout + Akku`` und am Netz ``P_netz = P_acin − PV``; die Spannung ist
``grid.voltage_nominal_v``.

- **ESS** (Multi „Ein“): Der Akku regelt die Summe der Phasen auf den Netz-Sollwert ``S``,
  ``clamp(PV − Lasten + S, −max_discharge_w, max_charge_w)``, je Phase ein Drittel. ``S`` ist der
  Override, solange er gilt, sonst ``AcPowerSetPoint`` (was keine Zahl ist, zählt als 0). Bei
  SOC ≤ Reserve entlädt der Akku nicht, und in der Sekunde, in der er sie erreicht, nur bis zu
  ihr; bei 100 % lädt er nicht.
- **Peak Shaving:** Liegt eine Phase über ``grid.import_limit_a``, entlädt der Multi dieser Phase
  zusätzlich, bis höchstens ``max_discharge_w / 3``, nur über der Reserve.
- **„Nur Laden“** (``charger_only``, ``vebus.mode`` 1): Der Akku entlädt nie, kein Peak Shaving.
- **Inselbetrieb** (Ausfall „grid“): Am Netz 0 W und 0 V, ``system.active_in_source`` 240. Die PV
  am AC-Eingang steht still; der Akku trägt die Lasten, auch unter der Reserve.
- **Wirkungsgrad** (``roundtrip_pct``, Plan 3c, Präzisierung 15): ``η = √(roundtrip_pct / 100)``
  je Richtung. Laden speichert ``η`` mal die Energie am Multi, Entladen zieht ``1/η`` mal
  so viel aus dem Akku, auch in der Sekunde, in der er die Reserve erreicht. Ohne Angabe ist das
  Modell verlustfrei.

**Override des Netz-Sollwerts** (``hub4/0/Overrides/Setpoint``, Plan 2c-2a, Präzisierung 17): Eine
Zahl, die der core dorthin schreibt, gilt als ``S`` und verfällt ``OVERRIDE_TIMEOUT_S`` nach dem
letzten Schreiben einer Zahl; ``None`` hebt ihn auf. Was keine endliche Zahl ist, ändert nichts.
Mit Dynamic ESS (``dess``) verwirft der Cerbo den Override und nimmt keinen an: Ein
Schreibauftrag liefert ``True`` und wirkt nicht. Der Cerbo meldet den Override unter seinem Pfad,
ohne ihn ``None``, und ``Settings/DynamicEss/Mode`` mit 1 bei Dynamic ESS, sonst mit 0.

**Signale** entstehen wie an der Anlage: Der Cerbo liefert Topics (``<Dienst>/<Instanz>/<Pfad>``),
die EVCS ihren Registerblock, der Wall Connector seine Vitals, Tessie den Zustand des Fahrzeugs
(``cars``), Home Assistant den Zustand des ``device_tracker`` eines Fahrzeugs. Die Parser der
echten Adapter machen daraus die Signale mit Namen, Quelle, Art und Einheit; so unterscheidet der
core das Modell nicht von der Anlage. Für die Selbstprüfung meldet der Cerbo, was eine richtig
eingestellte Anlage meldet.

**Home Assistant** (Quelle ``ha``, Plan 2c-2b, Präzisierung 12): Je Fahrzeug mit ``home_entity``
meldet er ``vehicle.<Schlüssel>.home_state`` (``home_signal``) mit ``home``, für die Fahrzeuge in
``away`` mit ``not_home``. In der Beispielkonfiguration hat der e-Golf eine ``home_entity``; er ist
also in jedem Szenario zu Hause, außer ein Szenario nennt ihn in ``away``. Die Quelle gilt wie
beim Adapter ohne Frischegrenze und mit ``invalidate_on_reconnect``. Sie ist nach dem ersten
``publish`` verbunden und fällt nie aus (``OUTAGES`` kennt keinen Ausfall für sie); ohne ein
Fahrzeug mit ``home_entity`` gibt es sie nicht.

**Gast** (``GUEST_CAR``, Präzisierung 12): ein Auto ohne Tessie und ohne Eintrag in der
Konfiguration, mit 3 Phasen (ein Szenario setzt 1 bis 3), 6 bis 16 A und 50 kWh. Er steht
abgesteckt, bis ein Szenario ihn ansteckt; jedes Auto lässt sich an jede Wallbox stecken. Der core
sieht von ihm nur den Strom an der Wallbox, denn er liefert keine Signale eines Fahrzeugs.

**Tesla woanders** (``Tesla.elsewhere``): Er steckt an keiner Wallbox, aber Tessie meldet ihn
„Stopped“, also angesteckt und zu Hause, wie ein Ladekabel an einer Steckdose. Der core sieht so
einen echten Widerspruch zwischen Tessie und den Wallboxen.

**Watchdog** am Cerbo (``bindaems/0/…``): Ein Heartbeat über 0 macht ihn nach ``REARM_S`` scharf.
Bleibt der Heartbeat danach ``victron.watchdog.timeout_s`` aus, löst er aus, und jede EVCS mit
GX-Instanz bekommt ihren ``safe_a``. Die 0 gibt ihn frei; er wartet. Als Echo meldet er den zuletzt
angenommenen Heartbeat, auch die 0.

**Ausfälle** (``outages``, Namen aus ``OUTAGES``): „evcs“ ist der Verlust der Modbus-Verbindung
zum core: Die Quelle ``evcs`` ist getrennt und liefert nichts, die GX-Werte über ``victron`` kommen
weiter, wie wenn nur der Netzweg zwischen core und EVCS bricht. „twc“ liefert keine Werte, die
Quelle ist getrennt. Bei „tessie“ scheitern die Abfragen, und der nächste Versuch folgt nach dem
Backoff des Adapters (60 s, dann doppelt, höchstens 900 s), auch nach dem Ausfall; die alten Werte
veralten nach 900 s, und Befehle enden mit HTTP 503. Die Quelle bleibt verbunden, weil auch der
echte Tessie-Adapter sie bei Fehlern nicht trennt. Bei „cerbo“ kommen keine Werte über
``victron``, und ``mqtt_write`` liefert ``False``. „grid“ ist der Inselbetrieb. Die Geräte selbst
laufen weiter, auch ein Override am Cerbo bis zu seinem Verfall.

**Fremder Regler** (``foreign``): Alle ``FOREIGN_EVERY`` stellt er direkt am Gerät jede EVCS auf
einen Sollstrom aus ``FOREIGN_EVCS_A`` mit Laden an und jeden Tesla auf einen Strom aus
``FOREIGN_TESLA_A``.

Den Zufall liefert das übergebene ``random.Random``; eine Uhr liest das Modell nie. Schreibaufträge
und Befehle an Tessie gelten zur Zeit des letzten ``step``.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Final

import httpx

from bindaems.core.actuators.victron import OVERRIDE_TIMEOUT_S
from bindaems.core.adapters.evcs import BLOCK_START, decode_block
from bindaems.core.adapters.evcs import FRESHNESS_S as EVCS_FRESHNESS_S
from bindaems.core.adapters.homeassistant import SOURCE as HA_SOURCE
from bindaems.core.adapters.homeassistant import convert_state
from bindaems.core.adapters.tessie import FRESHNESS_S as TESSIE_FRESHNESS_S
from bindaems.core.adapters.tessie import TESSIE_BASE_URL
from bindaems.core.adapters.twc import FRESHNESS_S as TWC_FRESHNESS_S
from bindaems.core.adapters.twc import parse_vitals
from bindaems.core.adapters.victron_mqtt import FRESHNESS_S as VICTRON_FRESHNESS_S
from bindaems.core.adapters.victron_mqtt import (
    HEARTBEAT_PATH,
    MAX_DISCHARGE_PATH,
    MIN_SOC_PATH,
    SETPOINT_OVERRIDE_PATH,
    evcharger_path,
    writable_paths,
)
from bindaems.core.adapters.victron_mqtt import SOURCE as VICTRON_SOURCE
from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.state.derived import PHASES
from bindaems.core.state.store import StateStore
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.config import (
    Config,
    EvcsConfig,
    Phase,
    VehicleConfig,
    VictronInstances,
    home_signal,
)
from bindaems.shared.domain import SignalKind, Value
from bindaems.shared.numbers import finite_number
from bindaems.tools.sim.cars import Car, Evcs, GxPath, Tesla, WallConnector
from bindaems.tools.watchdog_config import REARM_S

EVCS: Final = "evcs"
TWC: Final = "twc"
TESSIE: Final = "tessie"
CERBO: Final = "cerbo"
GRID: Final = "grid"
OUTAGES: Final = frozenset({EVCS, TWC, TESSIE, CERBO, GRID})
GUEST_CAR: Final = "gast"  # Schlüssel des Gastes in ``PlantModel.cars``
GUEST_MIN_A: Final = 6  # Mindeststrom des Gastes; an einer EVCS gilt ihr eigener, falls höher

STEP_S: Final = 1.0  # ein Schritt ist eine Sekunde
BATTERY_SOC_PCT: Final = 50.0  # SOC des Akkus, solange das Szenario keinen setzt
FOREIGN_EVERY: Final = timedelta(seconds=45)
FOREIGN_EVCS_A: Final = (6, 16)
FOREIGN_TESLA_A: Final = (5, 16)

# Zustände des ``device_tracker`` in Home Assistant
HA_HOME: Final = "home"
HA_AWAY: Final = "not_home"

# Werte des Cerbo
GRID_SOURCE: Final = 1  # ``system.active_in_source``: Netz am AC-Eingang
ISLAND_SOURCE: Final = 240  # kein Netz am AC-Eingang, die Multis versorgen das Haus
MULTI_ON: Final = 3  # ``vebus.mode`` „Ein“
MULTI_CHARGER_ONLY: Final = 1  # „Nur Laden“
HUB4_MODE: Final = 1  # ESS mit Phasenkompensation: Der Akku gleicht die Summe der Phasen aus
BATTERYLIFE_STATE: Final = 10  # „Optimiert ohne BatteryLife“, normal
SCHEDULE_OFF: Final = -7  # Tag eines abgeschalteten Ladefensters
_SETTINGS: Final = "settings/0/Settings"
_AC_SETPOINT: Final = f"{_SETTINGS}/CGwacs/AcPowerSetPoint"  # der Basis-Sollwert des Netzes
_DESS_MODE: Final = f"{_SETTINGS}/DynamicEss/Mode"  # 0 aus, 1 an
_OVERRIDE_TIMEOUT: Final = timedelta(seconds=OVERRIDE_TIMEOUT_S)
_PORTAL: Final = "sim"  # Portal-ID der Topics; die Abbildung des Adapters vergleicht nur mit ihr

# Zustände des Watchdogs (``/State``)
WAITING, ARMED, TRIPPED = 0, 1, 2


def _instance(configured: int | None) -> int:
    """Die Instanz eines Geräts am Cerbo; ohne Eintrag nimmt der Adapter die erste, die er sieht."""
    return configured if configured is not None else 0


def _guest_config(cfg: Config) -> VehicleConfig:
    """Das Gastfahrzeug: 3 Phasen, 6 bis 16 A und 50 kWh, ohne Tessie. Seine Standard-Wallbox ist
    die erste der Konfiguration; sie hat im Modell keine Wirkung, denn es steckt kein Auto von
    selbst an."""
    return VehicleConfig(
        name="Gast",
        usable_kwh=50.0,
        phases=3,
        min_a=GUEST_MIN_A,
        max_a=16,
        default_wallbox=next(iter(cfg.wallboxes)),
    )


class _Routes:
    """Topic des Cerbo → Signale, über die Abbildung des Victron-Adapters; je Topic einmal."""

    def __init__(self, instances: VictronInstances) -> None:
        self._resolver = InstanceResolver(instances)
        self._signals: dict[str, tuple[tuple[str, SignalKind], ...]] = {}

    def __call__(self, topic: str) -> tuple[tuple[str, SignalKind], ...]:
        signals = self._signals.get(topic)
        if signals is None:
            message = f"N/{_PORTAL}/{topic}"
            updates = parse_message(message, b'{"value": 0}', _PORTAL, self._resolver)
            if not updates:
                raise ValueError(f"Kein Signal zum Topic {topic}")
            signals = tuple((update.signal, update.kind) for update in updates)
            self._signals[topic] = signals
        return signals


class _Watchdog:
    """Der Totmann-Watchdog am Cerbo, so weit der Simulator ihn braucht.

    Ein Heartbeat über 0 beginnt einen Lauf; ``REARM_S`` danach ist er scharf. Bleibt der Heartbeat
    ``timeout_s`` aus, während er scharf ist, löst er aus (``tick``). Die 0 gibt ihn frei. Werte,
    die der echte Watchdog ablehnt (keine ganze Zahl ≥ 0), ändern nichts.
    """

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = int(timeout_s)  # so meldet ihn der Watchdog
        self._timeout = timedelta(seconds=timeout_s)
        self.state = WAITING
        self.echo: int | None = None
        self.trips = 0
        self.last_trip = 0  # Unix-Sekunden, 0 = nie
        self._run_start: datetime | None = None
        self._last_seen: datetime | None = None

    def heartbeat(self, value: float | int | None, now: datetime) -> None:
        """Ein Heartbeat zur Zeit ``now``: über 0 ein Lebenszeichen, die 0 die Freigabe."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return
        self.echo = value
        self._last_seen = now
        if value == 0:
            self.state = WAITING
            self._run_start = None
        elif self._run_start is None:
            self._run_start = now

    def tick(self, now: datetime) -> bool:
        """Prüft die Zeiten; ``True`` heißt: jetzt ausgelöst."""
        if self.state == ARMED:
            if self._last_seen is not None and now - self._last_seen >= self._timeout:
                self.state = TRIPPED
                self._run_start = None
                self.trips += 1
                self.last_trip = int(now.timestamp())
                return True
        elif self._run_start is not None and now - self._run_start >= timedelta(seconds=REARM_S):
            self.state = ARMED
        return False

    def topics(self) -> dict[str, Value]:
        """Die Pfade des Dienstes ``com.victronenergy.bindaems``."""
        return {
            "bindaems/0/State": self.state,
            "bindaems/0/TripCount": self.trips,
            "bindaems/0/LastTripTime": self.last_trip,
            "bindaems/0/LastTripFailures": 0,
            "bindaems/0/TimeoutSeconds": self.timeout_s,
            "bindaems/0/Version": WATCHDOG_VERSION,
            HEARTBEAT_PATH: self.echo,
        }


class PlantModel:
    """Die simulierte Anlage hinter dem core.

    Stellgrößen der Szenarien:

    - ``pv_w``: PV-Leistung gesamt in W
    - ``house_w``: Hauslast je Phase L1, L2, L3 in W
    - ``soc_pct``: SOC des Akkus; das Modell führt ihn mit der Akkuleistung und dem Wirkungsgrad
      (``roundtrip_pct``, ohne Angabe 100 %) fort
    - ``charger_only``: Multi auf „Nur Laden“ statt „Ein“
    - ``outages``: laufende Ausfälle aus ``OUTAGES``
    - ``foreign``: der fremde Regler ist an
    - ``dess``: Dynamic ESS ist an; der Cerbo meldet ``Settings/DynamicEss/Mode`` 1, verwirft den
      Override und nimmt keinen an
    - ``away``: die Fahrzeuge, die Home Assistant nicht zu Hause meldet (Schlüssel aus der
      Konfiguration); ohne Eintrag ist ein Fahrzeug mit ``home_entity`` zu Hause
    - die Geräte: ``wallboxes`` (``Evcs`` und ``WallConnector``), ``cars`` (``teslas`` davon mit
      Tessie, dazu der Gast ``GUEST_CAR`` hinter den Fahrzeugen der Konfiguration); ``plug`` und
      ``unplug`` stecken an und ab

    Messgrößen für die Kennzahlen, Stand des letzten ``step``:

    - ``grid_a``: Netzstrom je Phase, positiv beim Bezug
    - ``grid_w``: Netzleistung, die Summe der Phasen, positiv beim Bezug
    - ``battery_feed_a``: was der Multi je Phase aus dem Akku liefert
    - ``battery_w``: Akkuleistung, positiv beim Laden, und ``soc_pct``
    - ``pv_now_w``: die PV-Leistung des Schritts; im Inselbetrieb 0, anders als die Stellgröße
      ``pv_w``
    - ``override_w``, ``override_age_s``: der Override, der im Schritt gilt, und die Sekunden seit
      seinem letzten Schreiben; ohne Override beide ``None``
    - ``car_a``, ``car_w``: Strom je Phase und Ladeleistung je Fahrzeug
    """

    def __init__(self, cfg: Config, rng: random.Random, *, roundtrip_pct: float = 100.0) -> None:
        if not 0 < roundtrip_pct <= 100:
            raise ValueError(f"Roundtrip {roundtrip_pct} % liegt nicht in (0, 100] %.")
        self._cfg = cfg
        self._rng = rng
        self._eta = math.sqrt(roundtrip_pct / 100)
        self._volts = cfg.grid.voltage_nominal_v
        self.pv_w = 0.0
        self.house_w: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.soc_pct = BATTERY_SOC_PCT
        self.charger_only = False
        self.outages: set[str] = set()
        self.foreign = False
        self.dess = False
        self.away: set[str] = set()
        self.grid_a: dict[Phase, float] = dict.fromkeys(PHASES, 0.0)
        self.grid_w = 0.0
        self.battery_feed_a: dict[Phase, float] = dict.fromkeys(PHASES, 0.0)
        self.battery_w = 0.0
        self.pv_now_w = 0.0
        self.override_w: float | None = None
        self.override_age_s: float | None = None
        self.cars: dict[str, Car] = {}
        self.teslas: dict[str, Tesla] = {}
        for key, vehicle in cfg.vehicles.items():
            if vehicle.tessie is None:
                self.cars[key] = Car(key, vehicle)
            else:
                self.cars[key] = self.teslas[key] = Tesla(key, vehicle, cfg.site.location, rng)
        # der Gast steht abgesteckt, bis ein Szenario ihn ansteckt
        self.cars[GUEST_CAR] = Car(GUEST_CAR, _guest_config(cfg))
        self._by_vin = {tesla.vin: tesla for tesla in self.teslas.values()}
        # die Fahrzeuge, deren ``home_entity`` Home Assistant meldet
        self._home_keys = tuple(
            key for key, vehicle in cfg.vehicles.items() if vehicle.home_entity is not None
        )
        instances = cfg.victron.instances
        self.wallboxes: dict[str, Evcs | WallConnector] = {}
        self._evcs: dict[str, Evcs] = {}
        self._twcs: dict[str, WallConnector] = {}
        for key, wallbox in cfg.wallboxes.items():
            if isinstance(wallbox, EvcsConfig):
                gx = next((i for i, name in instances.evcharger.items() if name == key), None)
                self.wallboxes[key] = self._evcs[key] = Evcs(key, wallbox, gx)
            else:
                self.wallboxes[key] = self._twcs[key] = WallConnector(key, wallbox)
        self._writable = writable_paths(cfg.victron)
        gx_paths: tuple[GxPath, ...] = ("SetCurrent", "StartStop")
        self._gx_paths = {
            evcharger_path(evcs.instance, path): (evcs, path)
            for evcs in self._evcs.values()
            if evcs.instance is not None
            for path in gx_paths
        }
        self._grid_instance = _instance(instances.grid)
        self._vebus_instance = _instance(instances.vebus)
        self._pv_instances = list(instances.pv) or [0]
        self._routes = _Routes(instances)
        # Einstellungen am Cerbo, wie die Selbstprüfung sie erwartet; die Schreibpfade für Victron
        # sind dieselben Topics. Der Override und der Modus von Dynamic ESS fehlen hier: Sie hängen
        # vom Zustand des Modells ab (``_override``, ``dess``).
        self._settings: dict[str, Value] = {
            f"{_SETTINGS}/CGwacs/Hub4Mode": HUB4_MODE,
            f"{_SETTINGS}/CGwacs/BatteryLife/State": BATTERYLIFE_STATE,
            f"{_SETTINGS}/CGwacs/BatteryLife/Schedule/Charge/0/Day": SCHEDULE_OFF,
            _AC_SETPOINT: 0,
            f"{_SETTINGS}/CGwacs/AcInputLimit": cfg.grid.import_limit_a,
            MIN_SOC_PATH: cfg.battery.reserve_soc_pct,
            MAX_DISCHARGE_PATH: -1,
        }
        # der Override des Netz-Sollwerts: Wert und Zeit seines letzten Schreibens
        self._override: tuple[float, datetime] | None = None
        self._watchdog = _Watchdog(cfg.victron.watchdog.timeout_s)
        self._foreign_next: datetime | None = None
        self._now: datetime | None = None
        # Leistungen je Phase im letzten Schritt, für die Topics des Cerbo
        self._grid_w: dict[Phase, float] = dict.fromkeys(PHASES, 0.0)
        self._ac_in_w: dict[Phase, float] = dict.fromkeys(PHASES, 0.0)
        self._ac_out_w: dict[Phase, float] = dict.fromkeys(PHASES, 0.0)

    # --- Geräte und Messgrößen ------------------------------------------------------------------

    def plug(self, vehicle: str, wallbox: str) -> None:
        """Steckt das Fahrzeug an die Wallbox; beide müssen frei sein, sonst ``ValueError``.
        Jedes Auto lässt sich an jede Wallbox stecken, auch der Gast.

        Ein angesteckter Tesla startet nicht von selbst: Er lädt erst mit
        ``Tesla.charge_enabled = True``, das ein Szenario für ein Auto setzt, das selbst startet
        (Präzisierung 9), oder ein Befehl ``start_charging``. An der EVCS lädt ein Auto, sobald
        ``Evcs.start_stop`` 1 ist.
        """
        car, box = self.cars[vehicle], self.wallboxes[wallbox]
        if box.car is not None:
            raise ValueError(f"Wallbox „{wallbox}“ ist belegt.")
        if car.wallbox is not None:
            raise ValueError(f"Fahrzeug „{vehicle}“ steckt schon an „{car.wallbox}“.")
        box.car, car.wallbox = car, wallbox

    def unplug(self, vehicle: str) -> None:
        """Steckt das Fahrzeug ab; es zieht keinen Strom mehr."""
        car = self.cars[vehicle]
        if car.wallbox is not None:
            self.wallboxes[car.wallbox].car = None
        car.wallbox = None
        car.current_a = 0.0

    @property
    def car_a(self) -> dict[str, float]:
        """Der Strom je Phase jedes Fahrzeugs im letzten Schritt."""
        return {key: car.current_a for key, car in self.cars.items()}

    @property
    def car_w(self) -> dict[str, float]:
        """Die Ladeleistung jedes Fahrzeugs im letzten Schritt."""
        return {key: car.current_a * car.phases * self._volts for key, car in self.cars.items()}

    # --- Wege des core --------------------------------------------------------------------------

    def attach(self, store: StateStore) -> None:
        """Registriert die Quellen wie die Adapter: ``victron`` ereignisgesteuert, dazu je EVCS,
        Wall Connector und Fahrzeug mit Tessie eine Quelle mit der Frischegrenze ihres Adapters.
        Hat ein Fahrzeug ``home_entity``, kommt ``ha`` dazu, wie beim HA-Adapter ohne Frischegrenze
        und mit ``invalidate_on_reconnect``."""
        store.register_source(
            VICTRON_SOURCE, VICTRON_FRESHNESS_S, activity_based=True, invalidate_on_reconnect=True
        )
        for key in self._evcs:
            store.register_source(key, EVCS_FRESHNESS_S)
        for key in self._twcs:
            store.register_source(key, TWC_FRESHNESS_S)
        for key in self.teslas:
            store.register_source(f"tessie:{key}", TESSIE_FRESHNESS_S)
        if self._home_keys:
            store.register_source(HA_SOURCE, None, invalidate_on_reconnect=True)

    def mqtt_write(self, path: str, value: float | int | None) -> bool:
        """Der MQTT-Schreibweg des core zum Cerbo, wie ``VictronMqttAdapter.write``.

        Nur Pfade aus ``writable_paths``, sonst ``ValueError``. Während des Ausfalls „cerbo“ ist das
        Ergebnis ``False``, und nichts geschieht. Sonst wirkt der Auftrag zur Zeit des letzten
        ``step``: der Heartbeat beim Watchdog, Sollstrom und Laden an/aus bei der EVCS (verzögert),
        der Override des Netz-Sollwerts (``_write_override``), die Einstellungen sofort am Cerbo.
        """
        if path not in self._writable:
            raise ValueError(f"Schreibpfad nicht freigegeben: {path}")
        if CERBO in self.outages:
            return False
        now = self._time()
        if path == HEARTBEAT_PATH:
            self._watchdog.heartbeat(value, now)
        elif path == SETPOINT_OVERRIDE_PATH:
            self._write_override(value, now)
        elif path in self._gx_paths:
            evcs, name = self._gx_paths[path]
            if value is not None:
                evcs.write(name, value, now)
        elif path in self._settings:
            self._settings[path] = value
        return True

    def _write_override(self, value: float | int | None, now: datetime) -> None:
        """Ein Schreibauftrag an den Override: Eine Zahl setzt ihn und die Zeit des Schreibens,
        ``None`` hebt ihn auf. Was keine endliche Zahl ist (``bool``, Text, NaN, ±∞), ändert nichts,
        wie beim Watchdog. Mit Dynamic ESS nimmt der Cerbo den Auftrag an, und er wirkt nicht."""
        if self.dess:
            return
        if value is None:
            self._override = None
            return
        number = finite_number(value)
        if number is not None:
            self._override = (number, now)

    async def tessie(self, request: httpx.Request) -> httpx.Response:
        """Tessie für die Teslas des Modells, als Handler für ``httpx.MockTransport``.

        Bekannt ist nur ``POST {TESSIE_BASE_URL}/{vin}/command/<Name>`` für eine VIN des Modells;
        alles andere ergibt 404. Während des Ausfalls „tessie“ kommt sofort 503. Sonst wartet die
        Antwort, bis ``resolve`` sie freigibt (``Tesla.handle``).
        """
        url = request.url
        parts = url.path.split("/")
        tesla = self._by_vin.get(parts[1]) if len(parts) == 4 and parts[2] == "command" else None
        if f"{url.scheme}://{url.host}" != TESSIE_BASE_URL or request.method != "POST":
            tesla = None
        if tesla is None:
            return httpx.Response(404)
        if TESSIE in self.outages:
            return httpx.Response(503)
        return await tesla.handle(parts[3], url.params, self._time())

    def resolve(self, now: datetime) -> None:
        """Gibt die fälligen Antworten von Tessie frei; erst dann wirken die Befehle."""
        for tesla in self.teslas.values():
            tesla.resolve(now)

    def _time(self) -> datetime:
        if self._now is None:
            raise RuntimeError("PlantModel.step fehlt: Das Modell hat noch keine Zeit.")
        return self._now

    # --- Physik ---------------------------------------------------------------------------------

    def step(self, now: datetime) -> None:
        """Eine Sekunde Physik bis ``now``.

        In dieser Reihenfolge: fremder Regler, Watchdog, Wallboxen (fällige Aufträge, dann der Strom
        der Autos), Schlaf der Teslas, Verfall des Overrides, Lasten, ESS und Akku. Ein unbekannter
        Ausfall ist ein ``ValueError``.
        """
        unknown = self.outages - OUTAGES
        if unknown:
            raise ValueError(f"Unbekannter Ausfall: {', '.join(sorted(unknown))}")
        self._now = now
        self._run_foreign(now)
        if self._watchdog.tick(now):
            for evcs in self._evcs.values():
                if evcs.instance is not None:
                    evcs.write("SetCurrent", evcs.cfg.safe_a, now)
        for wallbox in self.wallboxes.values():
            wallbox.step(now, STEP_S, self._volts)
        for tesla in self.teslas.values():
            tesla.tick(now)
        self._expire_override(now)
        self._balance()

    def _expire_override(self, now: datetime) -> None:
        """Der Override am Cerbo zur Zeit ``now``: Er verfällt ``OVERRIDE_TIMEOUT_S`` nach dem
        letzten Schreiben (bei 300 s ist er weg) und mit Dynamic ESS sofort. Danach stehen
        ``override_w`` und ``override_age_s`` für diesen Schritt fest."""
        if self._override is not None and (
            self.dess or now - self._override[1] >= _OVERRIDE_TIMEOUT
        ):
            self._override = None
        override = self._override
        self.override_w = None if override is None else override[0]
        self.override_age_s = None if override is None else (now - override[1]).total_seconds()

    def _run_foreign(self, now: datetime) -> None:
        """Der fremde Regler: alle ``FOREIGN_EVERY`` neue Werte direkt an den Geräten."""
        if not self.foreign:
            self._foreign_next = None
            return
        if self._foreign_next is not None and now < self._foreign_next:
            return
        self._foreign_next = now + FOREIGN_EVERY
        for evcs in self._evcs.values():
            evcs.set_current_a = self._rng.randint(*FOREIGN_EVCS_A)
            evcs.start_stop = 1
        for tesla in self.teslas.values():
            tesla.wake(now)
            tesla.charge_amps = self._rng.randint(*FOREIGN_TESLA_A)

    def _balance(self) -> None:
        """Lasten, Akku und Netz je Phase, dann der SOC des Akkus."""
        volts = self._volts
        loads = dict(zip(PHASES, self.house_w, strict=True))
        for wallbox in self.wallboxes.values():
            for phase, amps in wallbox.phase_a().items():
                loads[phase] += amps * volts
        if GRID in self.outages:
            self.pv_now_w = 0.0
            battery = {phase: -load for phase, load in loads.items()}
            ac_in = dict.fromkeys(PHASES, 0.0)
            grid = dict.fromkeys(PHASES, 0.0)
        else:
            self.pv_now_w = self.pv_w
            battery = self._ess(loads)
            ac_in = {phase: loads[phase] + battery[phase] for phase in PHASES}
            grid = {phase: ac_in[phase] - self.pv_w / 3 for phase in PHASES}
        self._ac_out_w, self._ac_in_w, self._grid_w = loads, ac_in, grid
        self.grid_a = {phase: grid[phase] / volts for phase in PHASES}
        self.grid_w = sum(grid.values())
        self.battery_feed_a = {phase: max(0.0, -battery[phase]) / volts for phase in PHASES}
        self.battery_w = sum(battery.values())
        # Wirkungsgrad je Richtung: Laden speichert ``η`` mal, Entladen zieht ``1/η`` mal die
        # Energie am Netz aus dem Akku
        if self.battery_w > 0:
            stored_w = self.battery_w * self._eta
        else:
            stored_w = self.battery_w / self._eta
        kwh = stored_w * STEP_S / 3_600_000
        soc = self.soc_pct + kwh / self._cfg.battery.usable_kwh * 100
        if self.battery_w < 0 and GRID not in self.outages:
            # Am Netz endet die Entladung an der Reserve (``_above_reserve_w``); das hier fängt
            # nur die Rundung von ``η`` ab.
            soc = max(soc, min(self.soc_pct, self._cfg.battery.reserve_soc_pct))
        self.soc_pct = min(100.0, max(0.0, soc))

    def _ess(self, loads: dict[Phase, float]) -> dict[Phase, float]:
        """Die Akkuleistung je Phase am Netz, positiv beim Laden: ESS, dann Peak Shaving.

        Der ESS regelt die Summe der Phasen auf den Netz-Sollwert (``_setpoint_w``). Entladen darf
        der Akku im Schritt höchstens bis zur Reserve (``_above_reserve_w``); liefe er darüber
        hinaus, kürzt das jede Phase im selben Verhältnis.
        """
        battery = self._cfg.battery
        total = self.pv_w - sum(loads.values()) + self._setpoint_w()
        total = min(max(total, -battery.max_discharge_w), battery.max_charge_w)
        may_discharge = not self.charger_only and self.soc_pct > battery.reserve_soc_pct
        if not may_discharge:
            total = max(total, 0.0)
        if self.soc_pct >= 100.0:
            total = min(total, 0.0)
        share = dict.fromkeys(PHASES, total / 3)
        if not may_discharge:
            return share
        limit_w = self._cfg.grid.import_limit_a * self._volts
        floor_w = -battery.max_discharge_w / 3
        for phase in PHASES:
            over_w = loads[phase] + share[phase] - self.pv_w / 3 - limit_w
            if over_w > 0:
                share[phase] = max(share[phase] - over_w, floor_w)
        drawn_w, above_w = -sum(share.values()), self._above_reserve_w()
        if drawn_w > above_w:
            share = {phase: power * above_w / drawn_w for phase, power in share.items()}
        return share

    def _setpoint_w(self) -> float:
        """Der Netz-Sollwert des ESS in W, positiv beim Bezug: der Override, solange er gilt, sonst
        ``AcPowerSetPoint``; was keine Zahl ist, zählt als 0."""
        if self._override is not None:
            return self._override[0]
        return finite_number(self._settings[_AC_SETPOINT]) or 0.0

    def _above_reserve_w(self) -> float:
        """Die Entladeleistung am Netz, mit der der Akku in einem Schritt genau die Reserve
        erreicht; von der Energie über der Reserve kommt ``η`` mal so viel am Netz an."""
        battery = self._cfg.battery
        above_kwh = (self.soc_pct - battery.reserve_soc_pct) / 100 * battery.usable_kwh
        return above_kwh * self._eta * 3_600_000 / STEP_S

    # --- Signale ----------------------------------------------------------------------------------

    def publish(self, store: StateStore, now: datetime) -> None:
        """Schreibt die Signale wie die Adapter, mit Quelle, Art und Zeitstempel ``now``.

        ``now`` muss die aktuelle Zeit der Uhr des Speichers sein: Nach einer Wiederverbindung
        gelten ``victron``-Werte, die älter sind als sie, als veraltet
        (``invalidate_on_reconnect``).

        Eine ausgefallene Quelle liefert nichts; ``victron``, EVCS und Wall Connector sind dann
        getrennt, Tessie bleibt verbunden wie beim Adapter. Tessie liefert nur zu den Abfragezeiten
        seines Adapters, nach gescheiterten Abfragen mit dessen Backoff, und ist wie dieser erst
        nach der ersten gelungenen Abfrage verbunden. Home Assistant ist immer verbunden und meldet
        je Fahrzeug mit ``home_entity`` ``home``, in ``away`` ``not_home``.
        """
        cerbo_up = CERBO not in self.outages
        store.set_connected(VICTRON_SOURCE, cerbo_up)
        if cerbo_up:
            for topic, value in self._cerbo().items():
                for signal, kind in self._routes(topic):
                    store.update(signal, value, source=VICTRON_SOURCE, kind=kind, ts=now)
        for key, evcs in self._evcs.items():
            if EVCS not in self.outages:
                for name, value in decode_block(BLOCK_START, evcs.registers()).items():
                    kind = SignalKind.MEASUREMENT if name == "current_a" else SignalKind.STATE
                    store.update(f"wallbox.{key}.{name}", value, source=key, kind=kind, ts=now)
            store.set_connected(key, EVCS not in self.outages)
        for key, twc in self._twcs.items():
            if TWC not in self.outages:
                vitals = parse_vitals(twc.vitals(self._volts), twc.phase_map)
                for name, (value, kind) in vitals.items():
                    store.update(f"wallbox.{key}.{name}", value, source=key, kind=kind, ts=now)
            store.set_connected(key, TWC not in self.outages)
        for key, tesla in self.teslas.items():
            values = tesla.poll(now, self._volts, reachable=TESSIE not in self.outages)
            if values is None:
                continue
            source = f"tessie:{key}"
            for name, value in values.items():
                store.update(
                    f"vehicle.{key}.{name}",
                    value,
                    source=source,
                    kind=SignalKind.MEASUREMENT,
                    ts=now,
                )
            store.set_connected(source, True)
        if self._home_keys:
            store.set_connected(HA_SOURCE, True)
            for key in self._home_keys:
                state = HA_AWAY if key in self.away else HA_HOME
                store.update(
                    home_signal(key),
                    convert_state(state),
                    source=HA_SOURCE,
                    kind=SignalKind.STATE,
                    ts=now,
                )

    def _cerbo(self) -> dict[str, Value]:
        """Die Topics des Cerbo (``<Dienst>/<Instanz>/<Pfad>``) mit ihren Werten."""
        island = GRID in self.outages
        grid, vebus = f"grid/{self._grid_instance}", f"vebus/{self._vebus_instance}"
        topics: dict[str, Value] = {
            f"{grid}/Ac/Power": self.grid_w,
            f"{vebus}/Ac/NumberOfPhases": self._cfg.grid.phases,
            f"{vebus}/Mode": MULTI_CHARGER_ONLY if self.charger_only else MULTI_ON,
            "system/0/Dc/Battery/Soc": self.soc_pct,
            "system/0/Dc/Battery/Power": self.battery_w,
            "system/0/Ac/ActiveIn/Source": ISLAND_SOURCE if island else GRID_SOURCE,
        }
        for n, phase in enumerate(PHASES, 1):
            topics[f"{grid}/Ac/L{n}/Power"] = self._grid_w[phase]
            topics[f"{grid}/Ac/L{n}/Current"] = self.grid_a[phase]
            topics[f"{grid}/Ac/L{n}/Voltage"] = 0.0 if island else self._volts
            topics[f"{vebus}/Ac/ActiveIn/L{n}/P"] = self._ac_in_w[phase]
            topics[f"{vebus}/Ac/Out/L{n}/P"] = self._ac_out_w[phase]
        pv_w = self.pv_now_w / len(self._pv_instances)  # gleich auf die Wechselrichter
        for instance in self._pv_instances:
            topics[f"pvinverter/{instance}/Ac/Power"] = pv_w
            for n in range(1, 4):
                topics[f"pvinverter/{instance}/Ac/L{n}/Power"] = pv_w / 3
        for evcs in self._evcs.values():
            if evcs.instance is not None:
                for path, value in evcs.gx(self._volts).items():
                    topics[f"evcharger/{evcs.instance}/{path}"] = value
        topics[SETPOINT_OVERRIDE_PATH] = None if self._override is None else self._override[0]
        topics[_DESS_MODE] = int(self.dess)
        return topics | self._settings | self._watchdog.topics()
