"""Laufzeit des core: Adapter, 1-s-Zyklus, Telemetrie, Prüfungen, Betriebszustand, Aktoren und
Heartbeat.

Je Zyklus: Snapshot → abgeleitete Größen → Plausibilität → Flusszuordnung → Viertelstunden-
Summen → Telemetrie → Rücklesen der Aktoren und der offenen Schreibvorgänge → (alle 10 s)
Selbstprüfung, mitregelnde Systeme und fremde Änderungen → Eingangsprüfung → (nach jeder
Prüfung) Sperren → Werte des EMS, Watchdog, wirksame Betriebsarten → Betriebszustand → sicherer
Zustand, Übergabe und Regelung → Stream → Alarme → Heartbeat. Am Ende gehen die Entscheidungen
des Zyklus in einem Zug in den Spool, und (beim Start und stündlich) räumt der core das
Entscheidungslog auf, beides auch dann, wenn der Zyklus scheitert.

Die Regelung erreicht die Laufzeit nur über ``RegulationLink`` (Plan 2c-2a, Design 4.3): Er baut
die Eingaben des Regelungs-Kerns, gibt ihm Urteile und Ergebnisse zurück und legt seine Health und
Alarme ab. Eine Ausnahme in der Regelung setzt nur die Regelung aus (Präzisierung 15): Der Zyklus
läuft mit den Befehlen des Keepers weiter, aber ohne Heartbeat, und der Alarm
``control.exception`` gilt.

Auf Geräte schreibt der core den Heartbeat an den eigenen Watchdog am Cerbo, die Sicherwerte des
sicheren Zustands und die der Übergabe am Ende des Live-Betriebs (Plan 2b, Präzisierungen 3 und 4),
die Befehle der Laderegelung an EVCS und Tesla (Plan 2c-1, Design 4.2) und den Override des
Akku-Sollwerts an Victron (Plan 2c-2a, Design 5). Jeder Befehl geht durch die Safety-Schicht; im
Dry-Run wird er nur protokolliert. Ein Aktor hält Werte des EMS (owned), sobald der core ihm einen
Wert gesendet hat; ist er nicht mehr live gewünscht, bekommt er seine Sicherwerte und ist erst
übergeben, wenn sie zurückgelesen sind. Ändert ein anderer einen bestätigten Strom oder
Netz-Sollwert, sperrt das den Aktor wie ein mitregelndes System (Präzisierung 8). Ein Override
verfällt am Cerbo 300 s nach dem Schreiben (``OVERRIDE_TIMEOUT_S``), seine Erwartung in der
Erkennung ebenso, ab der Bestätigung (Plan 2c-2a, Präzisierung 7).

Den Heartbeat sendet der core nur, solange er regelt (Präzisierung 2): Ein Victron- oder EVCS-Aktor
ist im Regelbetrieb live gewünscht oder wird übergeben. Der Zähler kommt nur aus einem fehlerfreien
Zyklus (Präzisierung 5) ohne Ausnahme in der Regelung, in CONTROL, DEGRADED und SAFE oder in
OBSERVE zum Scharfschalten, wenn das Gate nur noch auf den Watchdog wartet und er das konfigurierte
Timeout meldet. Jeder Lauf beginnt mit einem anderen Zähler (``heartbeat_start``). Die Freigabe 0
folgt erst, wenn kein Victron- oder EVCS-Aktor mehr Werte des EMS hält und für keinen von ihnen ein
Schreibvorgang offen ist, und nur, wenn der core in diesem Lauf für den Watchdog zuständig war.

Fristen, Limits, Budget, Versuche und Heartbeat laufen auf der Uhr der Safety-Schicht
(``AnchoredClock``), Messwerte, Viertelstunden und die Zeitpunkte der Entscheidungen auf der
Wanduhr.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Generator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final

import httpx
import structlog
import uvicorn
from pydantic import SecretStr

from bindaems import __version__
from bindaems.core.accounting.flows import SlotAccumulator, allocate
from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.actuators.victron import OVERRIDE_TIMEOUT_S, VictronActuator
from bindaems.core.adapters.base import Adapter
from bindaems.core.adapters.evcs import EvcsAdapter, PymodbusReader
from bindaems.core.adapters.homeassistant import HaAdapter
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import (
    HEARTBEAT_PATH,
    AiomqttTransport,
    VictronMqttAdapter,
)
from bindaems.core.adapters.victron_mqtt import SOURCE as VICTRON_SOURCE
from bindaems.core.api.app import HealthReport, create_api, snapshot_to_json, watchdog_to_json
from bindaems.core.checks.competitors import detect_competitors
from bindaems.core.checks.selfcheck import CRITICAL_SIGNALS, FRESH_FOR, CheckResult, run_selfcheck
from bindaems.core.checks.values import number
from bindaems.core.control.actuator import Actuator, Capabilities, matches
from bindaems.core.control.context import decision_inputs, safety_context
from bindaems.core.control.executor import CommandExecutor, WriteResult
from bindaems.core.control.foreign import WATCHED_KINDS, ForeignChangeDetector
from bindaems.core.control.intent import INTENT_FILE_PROBLEM, IntentReply, IntentStore
from bindaems.core.control.operating import (
    OperatingInputs,
    OperatingStateMachine,
    Transition,
    gate_ready_except_watchdog,
)
from bindaems.core.control.ownership import Ownership
from bindaems.core.control.safe_state import (
    SafeStateKeeper,
    SafeTarget,
    safe_values_reached,
)
from bindaems.core.persistence.decision_spool import DecisionSpool
from bindaems.core.regulation_link import RegulationLink
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.inputs import InputCheck
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.limits import ActuatorSpec, HardLimits
from bindaems.core.safety.locks import (
    LockInfo,
    LockManager,
    controlled_elsewhere,
    lock_causes,
)
from bindaems.core.safety.modes import (
    ModeInfo,
    effective_modes,
    initially_owned,
    live_wanted,
    watchdog_required,
)
from bindaems.core.safety.types import InputStatus, SafetyContext, Verdict
from bindaems.core.state.derived import Derived, derive
from bindaems.core.state.plausibility import PlausibilityMonitor, check_grid_counters
from bindaems.core.state.store import StateStore
from bindaems.core.telemetry.sampler import TelemetrySampler
from bindaems.core.watchdog_client.heartbeat import HeartbeatClient, heartbeat_start
from bindaems.core.watchdog_client.status import (
    WatchdogState,
    WatchdogStatus,
    armed_for_core,
    read_watchdog,
    timeout_matches,
    watchdog_alarms,
)
from bindaems.shared.broadcast import Broadcast
from bindaems.shared.config import Config, EvcsConfig, Secrets
from bindaems.shared.control import (
    VICTRON,
    ActuatorMode,
    Command,
    CommandKind,
    CommandValue,
    Intent,
    OperatingState,
)
from bindaems.shared.decisions import Decision, DecisionResult, decision_to_json
from bindaems.shared.domain import Alarm, Quality, Severity, SlotFlows, Snapshot
from bindaems.shared.influx.lineprotocol import PointSink
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import AnchoredClock, Clock, SystemClock

log = structlog.get_logger(__name__)

CHECK_INTERVAL_S = 10.0
CHECK_TOLERANCE_S = 0.05
CYCLE_LIMIT_MS = 200.0
OVERRUN_CYCLES = 3
CYCLE_HISTORY = 300
# ``system.active_in_source``: kein Netz am AC-Eingang, die Multis versorgen das Haus (Inselbetrieb)
ISLAND_SOURCE: Final = 240
# So oft räumt der core das Entscheidungslog auf (auf der Uhr der Safety-Schicht)
PRUNE_INTERVAL: Final = timedelta(hours=1)
# Nur in diesen Betriebszuständen schreibt der core auf Aktoren (Regel 8)
_WRITING_STATES: Final = frozenset(
    {OperatingState.CONTROL, OperatingState.DEGRADED, OperatingState.SAFE}
)
# Nur in diesen Betriebszuständen läuft die Regelung, und nur bei Regelbetrieb (Plan 2c-1,
# Design 4.3)
_REGULATION_STATES: Final = frozenset({OperatingState.CONTROL, OperatingState.DEGRADED})
# Fähigkeiten eines Aktors vor dem ersten Zyklus: noch nicht geprüft, also nicht steuerbar
_NOT_CHECKED: Final = Capabilities(False)
# Ein Schreibvorgang mit diesem Ergebnis kann am Gerät noch verspätet wirken
_UNCLEAR_RESULTS: Final = frozenset({DecisionResult.ERROR, DecisionResult.TIMEOUT})
# Bei diesen Familien bestätigt der Befehl selbst (Tessie: ``result: true``), nicht das Rücklesen
_CONFIRMED_BY_COMMAND: Final = frozenset({"tesla"})
# Tessie-Signale des Fahrzeugs: Ist eines ``False``, lädt es nicht (schläft, weg, abgesteckt)
_NOT_CHARGING_SIGNALS: Final = ("online", "at_home", "plugged")

_Key = tuple[str, CommandKind]  # Aktor und Art


class _ApiServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield  # SIGTERM/SIGINT behandelt die Laufzeit (geordnetes Herunterfahren)


def _alarm_json(alarm: Alarm) -> dict[str, str]:
    return {
        "id": alarm.id,
        "severity": alarm.severity.value,
        "message": alarm.message,
        "since": alarm.since.isoformat(),
    }


def _slot_json(flows: SlotFlows) -> dict[str, Any]:
    return {
        "slot_start": flows.slot_start.isoformat(),
        "covered_s": flows.covered_s,
        "flows_wh": dict(flows.flows_wh),
        "counters": {signal: list(bounds) for signal, bounds in flows.counters.items()},
    }


def _actuator_json(
    spec: ActuatorSpec,
    mode: ModeInfo,
    lock: LockInfo | None,
    capabilities: Capabilities,
    pending: frozenset[_Key],
    owned: frozenset[str],
) -> dict[str, Any]:
    """Ein Aktor für ``health``.

    ``pending`` nennt die Arten mit offenem Schreibvorgang, ``owned`` die Aktoren, die Werte des
    EMS halten können (Plan 2b, Präzisierung 3).
    """
    return {
        "id": spec.id,
        "label": spec.label,
        "requested": mode.requested.value,
        "effective": mode.effective.value,
        "live_allowed": spec.live_allowed,
        "reasons": list(mode.reasons),
        "locked": list(lock.reasons) if lock is not None else [],
        "controllable": capabilities.controllable,
        "capability_reasons": list(capabilities.reasons),
        "pending": [kind.value for kind in CommandKind if (spec.id, kind) in pending],
        "owned": spec.id in owned,
    }


def _tesla_not_charging(snap: Snapshot, spec: ActuatorSpec) -> bool:
    """Lädt der Tesla laut Tessie nicht? Dann bekommt er im sicheren Zustand keinen Sicherwert.

    Das gilt für einen Tesla, bei dem ``online``, ``at_home`` oder ``plugged`` mit Qualität ``OK``
    ``False`` ist: Er schläft, ist nicht zu Hause oder nicht angesteckt. Ein fehlender oder
    veralteter Wert genügt nicht, denn dann kann er laden. Die Signale heißen wie der Aktor
    (``vehicle.<k>.online``).
    """
    return spec.family == "tesla" and any(
        snap.ok(f"{spec.id}.{name}") is False for name in _NOT_CHARGING_SIGNALS
    )


def _lapses_at(write: WriteResult, snow: datetime) -> datetime | None:
    """Wann die Erwartung eines bestätigten Werts verfällt (Plan 2c-2a, Präzisierung 7).

    Ein Override mit Zahl verfällt am Cerbo ``OVERRIDE_TIMEOUT_S`` nach dem letzten Schreiben;
    seine Erwartung ebenso, ab der Bestätigung zur Zeit der Safety-Schicht ``snow``. Jeder andere
    Wert, auch „kein Override“, gilt ohne Frist (``None``).
    """
    if write.kind == CommandKind.GRID_SETPOINT and finite_number(write.value) is not None:
        return snow + timedelta(seconds=OVERRIDE_TIMEOUT_S)
    return None


def _safe_reasons(snap: Snapshot) -> tuple[str, ...]:
    """Gründe für den sicheren Zustand: veraltete Cerbo-Daten und Inselbetrieb.

    Veraltet ist jedes Signal aus ``CRITICAL_SIGNALS``, das fehlt oder nicht die Qualität ``OK``
    hat; die Meldung nennt sie in der Reihenfolge der Liste.
    """
    reasons: list[str] = []
    stale = [
        signal
        for signal in CRITICAL_SIGNALS
        if (reading := snap.get(signal)) is None or reading.quality is not Quality.OK
    ]
    if stale:
        reasons.append(f"Cerbo-Daten veraltet: {', '.join(stale)}.")
    if number(snap, "system.active_in_source") == ISLAND_SOURCE:
        reasons.append("Netzausfall (Inselbetrieb).")
    return tuple(reasons)


class CoreRuntime:
    """Erfüllt ``CoreView`` der internen API."""

    def __init__(
        self,
        cfg: Config,
        secrets: Secrets,
        *,
        clock: Clock | None = None,
        adapters: Sequence[Adapter] | None = None,
        sink: PointSink | None = None,
        cycle_s: float = 1.0,
        serve_api: bool = True,
        timer: Callable[[], float] = time.perf_counter,
        watchdog_send: Callable[[int], bool] | None = None,
        safety_clock: Clock | None = None,
        mqtt_write: Callable[[str, float | int | None], bool] | None = None,
        http_client: httpx.AsyncClient | None = None,
        heartbeat_first: int | None = None,
    ) -> None:
        """Die Laufzeit mit Adaptern, Aktoren, Safety-Schicht, Regelung und Entscheidungslog.

        - ``clock`` ist die Wanduhr: Messwerte, Viertelstunden, Zeitpunkte der Entscheidungen,
          ``boost_until`` und der Kalendertag des Weckkontingents.
        - ``safety_clock`` ist die Uhr der Safety-Schicht, standardmäßig ``AnchoredClock``:
          Fristen, Rate-Limits, Schreibbudget, sicherer Zustand, Erkennung fremder Änderungen,
          Heartbeat, Tesla-Fehler und die Zeiten der Regelung.
        - ``mqtt_write`` reiht einen Schreibauftrag (Pfad, Wert) an den Cerbo ein. Ohne ihn
          schreibt der Victron-Adapter (``VictronMqttAdapter.write``); ohne Victron-Adapter wird
          kein Auftrag angenommen.
        - ``watchdog_send`` stellt den Heartbeat zu; ohne ihn geht er über ``mqtt_write``.
        - ``http_client`` ist der gemeinsame HTTP-Client von Wall Connector, Tessie, Tesla-Aktor
          und InfluxDB; ohne ihn legt die Laufzeit beim ersten Bedarf einen an. ``shutdown``
          schließt ihn in beiden Fällen.
        - ``heartbeat_first`` ist der erste Zähler des Heartbeats (1 bis ``HEARTBEAT_MAX``, sonst
          ``ValueError``). Ohne ihn beginnt jeder Lauf mit einem eigenen Wert aus der Startzeit der
          Wanduhr (``heartbeat_start``), anders als frühere Läufe und die Zähler 1, 2, 3, … der
          Probe und des Teil-2-Werkzeugs.
        """
        self._cfg = cfg
        self._secrets = secrets
        self._clock = clock or SystemClock()
        self._safety_clock: Clock = safety_clock if safety_clock is not None else AnchoredClock()
        self._mqtt_write = mqtt_write if mqtt_write is not None else self._victron_write
        self.cycle_s = cycle_s
        self._serve_api = serve_api
        self._timer = timer
        self.store = StateStore(self._clock)
        self.broadcast = Broadcast()
        self.limits = HardLimits.from_config(cfg)
        self._needs_watchdog = frozenset(
            actuator for actuator, spec in self.limits.actuators.items() if spec.needs_watchdog
        )
        self.intents = IntentStore(
            cfg.core.data_dir / "intent.json",
            self.limits,
            vehicles={key: vehicle.name for key, vehicle in cfg.vehicles.items()},
            wallboxes=list(cfg.wallboxes),
        )
        self.intents.load()
        self.locks = LockManager(cfg.safety.lock_release_s)
        self.operating = OperatingStateMachine()
        send = watchdog_send if watchdog_send is not None else self._send_heartbeat
        first = (
            heartbeat_first if heartbeat_first is not None else heartbeat_start(self._clock.now())
        )
        self.heartbeat = HeartbeatClient(send, cfg.victron.watchdog.heartbeat_s, first=first)
        intent = self.intents.current
        # Das Gerät eines Aktors, den die gespeicherte Absicht live anfordert, kann noch Werte vom
        # letzten Lauf halten (Plan 2b, Präzisierung 3).
        self.ownership = Ownership(initially_owned(intent, self.limits))
        self.foreign = ForeignChangeDetector(cfg.safety.lock_release_s)
        # Erwartungen, die erst gelten, wenn das Rücklesen den Wert zeigt (``_note_write``)
        self._awaiting_readback: dict[_Key, CommandValue] = {}
        self._confirmed_by_command = frozenset(
            actuator
            for actuator, spec in self.limits.actuators.items()
            if spec.family in _CONFIRMED_BY_COMMAND
        )
        self._inputs = InputCheck()
        # Ergebnis des letzten Zyklus für health(); vor dem ersten Zyklus aus der geladenen Absicht
        self._input_status = InputStatus(True)
        self._watchdog = WatchdogStatus.unknown()
        self._watchdog_armed = False  # scharf für diesen core (``armed_for_core``)
        self._handing_back = self.ownership.handing_back(live_wanted(intent, self.limits))
        self._watchdog_required = watchdog_required(
            intent, self.limits, handing_back=self._handing_back
        )
        self._modes = effective_modes(intent, self.limits, False, handing_back=self._handing_back)
        self._effective: dict[str, ActuatorMode] = {}  # wirksame Betriebsarten im letzten Zyklus
        self._gate_ready = False  # das Gate wartet nur noch auf den Watchdog
        # Zuständig für den Watchdog ab dem ersten Bedarf oder dem ersten owned Victron- oder
        # EVCS-Aktor, bis zum Ende des Laufs (``_heartbeat``)
        self._responsible = bool(self.ownership.owned & self._needs_watchdog)
        self._owned = self.ownership.owned
        self._active_locks: dict[str, LockInfo] = {}
        # Aktoren, deren Sperre bei der letzten Prüfung ein anderes System belegte
        self._controlled_elsewhere: frozenset[str] = frozenset()
        self._http: httpx.AsyncClient | None = http_client  # der gemeinsame (``_http_client``)
        self._writer: InfluxWriter | None = None
        if sink is None:
            telemetry = cfg.telemetry
            spool = DiskSpool(
                telemetry.spool_dir,
                telemetry.spool_max_mb * 1024 * 1024,
                timedelta(days=telemetry.spool_max_age_days),
                self._clock,
            )
            self._writer = InfluxWriter(
                cfg.influxdb, secrets.influx_password, self._http_client(), spool
            )
            sink = self._writer
        self._sampler = TelemetrySampler(sink, self._clock)
        self.counter_signals = [
            "grid.energy_import_kwh",
            "grid.energy_export_kwh",
            *(f"pv.{name}.energy_kwh" for name in cfg.victron.instances.pv.values()),
            *(f"load.{name}.energy_kwh" for name in cfg.victron.instances.acload.values()),
            *(f"wallbox.{name}.total_kwh" for name in cfg.wallboxes),
        ]
        self._accumulator = SlotAccumulator(self.counter_signals)
        self._plausibility = PlausibilityMonitor()
        self.adapters: list[Adapter] = (
            list(adapters) if adapters is not None else self.build_adapters()
        )
        # Die Aktoren leben so lange wie die Laufzeit: Der Tesla hält seine laufenden Befehle.
        self.actuators: dict[str, Actuator] = self.build_actuators()
        started = self._safety_clock.now()
        data_dir = cfg.core.data_dir
        budget = PersistentWriteBudget(
            self.limits.persistent_per_hour,
            self.limits.persistent_per_day,
            data_dir / "write_budget.json",
            started,
        )
        self.layer = SafetyLayer(
            self.limits, budget, rate_file=data_dir / "rate_limits.json", now=started
        )
        self.decision_spool = DecisionSpool(data_dir / "decisions")
        self._labels = {actuator: spec.label for actuator, spec in self.limits.actuators.items()}
        self.executor = CommandExecutor(self.layer, self.actuators, self._decide, self._labels)
        self.keeper = SafeStateKeeper(self.limits)
        # Die Regelung lebt so lange wie die Laufzeit; ``started`` ist die Wanduhr beim Start der
        # Regelung (Zuordnung). Ihr Zustand lebt nur im Speicher (Plan 2c-1, Design 4.1).
        self.regulation = RegulationLink(cfg, self.limits, started=self._clock.now())
        self._cycle_decisions: list[Decision] = []  # Entscheidungen des laufenden Zyklus
        self._last_prune: datetime | None = None
        # Stand des letzten Zyklus für health()
        self._capabilities: dict[str, Capabilities] = {}
        self._pending: frozenset[tuple[str, CommandKind]] = frozenset()
        self.selfcheck_runs = 0
        self._selfcheck: list[CheckResult] = []
        self._competitor_alarms: list[Alarm] = []
        self._foreign_alarms: list[Alarm] = []
        self._counter_alarm: Alarm | None = None
        self._last_check: datetime | None = None
        self._alarms: list[Alarm] = []
        self._cycle_ms: deque[float] = deque(maxlen=CYCLE_HISTORY)
        self._slow_cycles = 0
        self._overrun_since: datetime | None = None
        self._last_state: dict[str, Any] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._api_server: _ApiServer | None = None

    # --- Aufbau -------------------------------------------------------------------------

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    def build_adapters(self) -> list[Adapter]:
        cfg, secrets = self._cfg, self._secrets
        mqtt = cfg.victron.mqtt
        adapters: list[Adapter] = [
            VictronMqttAdapter(
                cfg.victron,
                secrets.mqtt_password,
                self.store,
                partial(AiomqttTransport, mqtt, secrets.mqtt_password),
            )
        ]
        for name, wallbox in cfg.wallboxes.items():
            if isinstance(wallbox, EvcsConfig):
                reader = partial(PymodbusReader, wallbox.host, wallbox.port, wallbox.unit_id)
                adapters.append(EvcsAdapter(name, wallbox, self.store, reader))
            else:
                adapters.append(TwcAdapter(name, wallbox, self.store, self._http_client()))
        for vehicle_id, vehicle in cfg.vehicles.items():
            if vehicle.tessie is None:
                continue
            if secrets.tessie_token is None:
                log.warning(f"Tessie-Token fehlt – Fahrzeug {vehicle.name} wird nicht gelesen")
                continue
            adapters.append(
                TessieAdapter(
                    vehicle_id,
                    vehicle,
                    cfg.site.location,
                    secrets.tessie_token,
                    self.store,
                    self._http_client(),
                )
            )
        if cfg.homeassistant is not None and secrets.ha_token is not None:
            adapters.append(HaAdapter(cfg.homeassistant, secrets.ha_token, self.store))
        return adapters

    def build_actuators(self) -> dict[str, Actuator]:
        """Die Aktoren in Katalogreihenfolge: Victron, jede EVCS, jedes Fahrzeug mit Tessie.

        Victron und EVCS schreiben über ``mqtt_write`` und sind steuerbar, solange der Cerbo
        verbunden ist. Die EVCS findet ihre Instanz des GX-Dienstes ``evcharger`` über ihren
        Schlüssel in ``victron.instances.evcharger``; ohne Eintrag ist sie nicht steuerbar. Der
        Tesla nutzt den gemeinsamen HTTP-Client und die Uhr der Safety-Schicht; ohne Tessie-Token
        ist Tessie für ihn nie erreichbar.
        """
        cfg = self._cfg
        actuators: dict[str, Actuator] = {
            VICTRON: VictronActuator(self._mqtt_write, self._cerbo_connected)
        }
        evcharger = cfg.victron.instances.evcharger
        for key, wallbox in cfg.wallboxes.items():
            if isinstance(wallbox, EvcsConfig):
                instance = next((gx for gx, name in evcharger.items() if name == key), None)
                evcs = EvcsActuator(key, wallbox, instance, self._mqtt_write, self._cerbo_connected)
                actuators[evcs.id] = evcs
        token = self._secrets.tessie_token
        for key, vehicle in cfg.vehicles.items():
            if vehicle.tessie is None:
                continue
            car = TeslaActuator(
                key,
                vehicle,
                token if token is not None else SecretStr(""),  # ohne Token nie erreichbar
                self._http_client(),
                partial(self._tessie_connected, key),
                clock=self._safety_clock.now,
            )
            actuators[car.id] = car
        return actuators

    def _cerbo_connected(self) -> bool:
        """Steht die Verbindung zum Cerbo? Der Victron-Adapter hält sie im Zustandsspeicher."""
        return self.store.connected(VICTRON_SOURCE) is True

    def _tessie_connected(self, key: str) -> bool:
        """Ist Tessie für das Fahrzeug ``key`` erreichbar? Ohne Tessie-Token nie."""
        if self._secrets.tessie_token is None:
            return False
        return self.store.connected(f"tessie:{key}") is True

    def _victron_write(self, path: str, value: float | int | None) -> bool:
        """Reiht den Auftrag beim Victron-Adapter ein; ohne ihn ist das Ergebnis ``False``.

        Der Zyklus läuft im Event-Loop, ``write`` darf deshalb direkt aufgerufen werden.
        """
        for adapter in self.adapters:
            if isinstance(adapter, VictronMqttAdapter):
                return adapter.write(path, value)
        return False

    def _send_heartbeat(self, value: int) -> bool:
        """Schreibt den Heartbeat über ``mqtt_write``."""
        return self._mqtt_write(HEARTBEAT_PATH, value)

    # --- CoreView ------------------------------------------------------------------------

    def health(self) -> HealthReport:
        """Gesundheit mit dem Stand des letzten Zyklus; ``intent`` ist die gültige Absicht.

        Die API ruft das aus einem anderen Thread auf. Deshalb liest es nur, was der Zyklus
        abgelegt hat, und ruft etwa ``locks.active`` nicht selbst auf.
        """
        intent, since = self.intents.current, self.operating.since
        modes, locks = self._modes, self._active_locks
        capabilities, pending, owned = self._capabilities, self._pending, self._owned
        regulation = self.regulation.health()
        return HealthReport(
            mode=self.operating.state.value,
            version=__version__,
            adapters=[asdict(adapter.health()) for adapter in self.adapters],
            selfcheck=[asdict(result) for result in self._selfcheck],
            alarms=[_alarm_json(alarm) for alarm in self._alarms],
            cycle_ms_p95=self._p95(),
            mode_since=since.isoformat() if since is not None else None,
            mode_reasons=list(self.operating.reasons),
            intent=intent.model_dump(mode="json") if intent is not None else None,
            actuators=[
                _actuator_json(
                    spec,
                    modes[actuator],
                    locks.get(actuator),
                    capabilities.get(actuator, _NOT_CHECKED),
                    pending,
                    owned,
                )
                for actuator, spec in self.limits.actuators.items()
            ],
            watchdog=watchdog_to_json(
                self._watchdog, self.heartbeat.status, required=self._watchdog_required
            ),
            inputs={"ok": self._input_status.ok, "problems": list(self._input_status.problems)},
            vehicles=regulation["vehicles"],
            battery=regulation["battery"],
        )

    def state(self) -> dict[str, Any]:
        if self._last_state is None:
            snap = self.store.snapshot()
            return snapshot_to_json(snap, derive(snap, self._cfg))
        return self._last_state

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        return self.broadcast.subscribe()

    def intent(self) -> Intent | None:
        return self.intents.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        return self.intents.submit(intent, self._clock.now())

    def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        """Die Entscheidungen mit ``ts`` nach ``since`` aus dem Spool, aufsteigend.

        Wie ``DecisionSpool.read_since``: höchstens ``limit``, außer eine Gruppe gleicher Zeit
        (ein Zyklus) reicht darüber hinaus; das Zweite sagt, ob weitere folgen. Die Entscheidungen
        des laufenden Zyklus erscheinen erst nach seinem Ende. Der Aufruf liest Dateien und darf in
        einem anderen Thread laufen.
        """
        return self.decision_spool.read_since(since, limit)

    # --- Zyklus --------------------------------------------------------------------------

    def cycle_once(self) -> None:
        """Ein Regelzyklus; am Ende gehen seine Entscheidungen in den Spool, auch wenn er scheitert.

        Danach räumt der core das Entscheidungslog auf (``_prune_decisions``), ebenfalls auch dann:
        Ein Zyklus, der dauernd scheitert, ließe es sonst wachsen.

        Eine Ausnahme, auch aus einem Aktor, bricht den Zyklus ab und lässt den Heartbeat ausfallen.
        Nur eine Ausnahme in der Regelung fängt ``RegulationLink``: Sie setzt die Regelung aus, und
        der Zyklus läuft ohne Heartbeat weiter (Plan 2c-2a, Präzisierung 15). Ob ein Zyklus
        fehlerfrei war, gilt für ihn allein (Plan 2b, Präzisierung 5): Die gescheiterten
        Schreibvorgänge eines abgebrochenen Zyklus zählen nicht für den nächsten.
        """
        try:
            self._cycle()
        finally:
            self._flush_decisions()
            self._prune_decisions(self._clock.now(), self._safety_clock.now())
            self.executor.take_failures()  # Rest eines abgebrochenen Zyklus, sonst schon leer

    def _cycle(self) -> None:
        self.regulation.start_cycle()
        started = self._timer()
        now = self._clock.now()
        snow = self._safety_clock.now()
        snap = self.store.snapshot()
        derived = derive(snap, self._cfg)
        plausibility = self._plausibility.evaluate(snap, derived, now)
        closed = self._accumulator.add(now, self._flows(derived), snap)
        if closed is not None:
            self._sampler.write_slot_flows(closed)
            self.broadcast.publish({"type": "slot_flows", "data": _slot_json(closed)})
            self._check_counters(closed, now)
        self._sampler.on_cycle(snap, derived)
        present = self._readback(snap, now, snow)
        checked = self._check_due(now)
        if checked:
            self._competitor_alarms = detect_competitors(snap, self._cfg, now)
            self._selfcheck = run_selfcheck(snap, self._cfg, self.store.ok_since, now)
            self.selfcheck_runs += 1
            self.foreign.check(present, self.executor.pending(), snow)
        self._foreign_alarms = self.foreign.alarms(snow, self._labels)
        self._update_operation(snap, plausibility, now, present, checked=checked)
        self._control(snap, derived, now, snow, present)
        state = snapshot_to_json(snap, derived)
        self._last_state = state
        self.broadcast.publish({"type": "state", "data": state})
        self._record_cycle((self._timer() - started) * 1000.0, now)
        counter = [self._counter_alarm] if self._counter_alarm is not None else []
        watchdog = watchdog_alarms(
            self._watchdog,
            self._watchdog_required,
            now,
            expected_timeout_s=self._cfg.victron.watchdog.timeout_s,
        )
        self._update_alarms(
            [
                *plausibility,
                *self._competitor_alarms,
                *self._foreign_alarms,
                *counter,
                *self._overrun_alarm(),
                *watchdog,
                *self._operation_alarms(now),
                *self._control_alarms(snow),
                *self.regulation.alarms(),
            ]
        )
        # als Letztes: Eine Ausnahme in einem früheren Schritt lässt den Heartbeat ausfallen
        self._heartbeat(snow)

    def _readback(self, snap: Snapshot, now: datetime, snow: datetime) -> dict[_Key, CommandValue]:
        """Die zurückgelesenen Werte aller Aktoren je Art; dazu das Rücklesen des Ausführers.

        Der Ausführer fragt in jedem Zustand seine offenen Schreibvorgänge ab. Jeder
        abgeschlossene geht mit der Zeit der Safety-Schicht ``snow`` an ``_note_write`` und an die
        Regelung (``note_result``), auch einer des Keepers: Ein Ergebnis, das nicht zu ihrem
        letzten eigenen Wert passt, lässt sie unberührt. Eine Erwartung, die auf das Rücklesen
        wartet, gilt ab dem ersten Zyklus, in dem das Gerät den Wert zeigt (``matches``).
        """
        present = {
            (actuator_id, kind): value
            for actuator_id, actuator in self.actuators.items()
            for kind, value in actuator.present(snap).items()
        }
        for write in self.executor.poll(snap, snow, now):
            self._note_write(write, snow)
            self.regulation.note_result(write)
        for key, value in list(self._awaiting_readback.items()):
            if key in present and matches(key[1], value, present[key]):
                del self._awaiting_readback[key]
                self.foreign.expect(key[0], key[1], value)
        return present

    def _note_write(self, write: WriteResult, snow: datetime) -> None:
        """Was ein abgeschlossener Schreibvorgang für die Erkennung fremder Änderungen bedeutet.

        Es zählen nur die beobachteten Arten Strom und Netz-Sollwert (``WATCHED_KINDS``; Plan 2b,
        Präzisierung 8). ``snow`` ist die Zeit der Safety-Schicht, auf der die Erkennung läuft.

        - **Bestätigt** bei einem owned Aktor: Der Wert ist ab jetzt die Erwartung. Ein Override
          mit Zahl verfällt am Cerbo ``OVERRIDE_TIMEOUT_S`` nach dem Schreiben, wenn niemand ihn
          auffrischt; seine Erwartung verfällt ebenso, ``OVERRIDE_TIMEOUT_S`` nach der Bestätigung
          (Plan 2c-2a, Präzisierung 7). Jedes bestätigte Auffrischen erneuert sie. So ist der
          gewollte Rückfall keine fremde Änderung, wenn die Regelung ruht, gesperrt ist, das Ziel
          nicht kennt oder eine Ausnahme wirft. „Kein Override“ gilt ohne Frist. Beim Tesla
          bestätigt Tessie den Befehl (``result: true``), nicht das Rücklesen, und sein Cache hinkt
          30–60 s hinterher. Seine Erwartung gilt deshalb erst, wenn der Cache den Wert zeigt
          (``_readback``); bis dahin erwartet die Erkennung beim Tesla nichts. Sonst meldete sie
          eine fremde Änderung, wo nur der Cache zurückliegt.
        - **Fehler oder Zeitüberschreitung:** Die Beobachtung des Aktors endet bis zum nächsten
          bestätigten Wert. Ein eigener Wert, den das Gerät verspätet übernimmt, sähe sonst gegen
          die alte Erwartung fremd aus.
        - **Nicht gesendet** kommt hier nicht vor (``CommandExecutor.execute``): Am Gerät hat sich
          nichts geändert, die Erwartung bleibt.
        """
        if write.kind not in WATCHED_KINDS:
            return
        actuator = write.actuator
        if write.result in _UNCLEAR_RESULTS:
            self._forget(actuator)
        elif write.result == DecisionResult.OK and actuator in self.ownership.owned:
            if actuator in self._confirmed_by_command:
                self.foreign.forget(actuator)
                self._awaiting_readback[(actuator, write.kind)] = write.value
            else:
                lapses_at = _lapses_at(write, snow)
                self.foreign.expect(actuator, write.kind, write.value, lapses_at=lapses_at)

    def _forget(self, actuator: str) -> None:
        """Beendet die Beobachtung des Aktors, auch die Erwartungen, die auf das Rücklesen warten.

        Der Alarm einer erkannten fremden Änderung bleibt (``ForeignChangeDetector.forget``).
        """
        self.foreign.forget(actuator)
        self._awaiting_readback = {
            key: value for key, value in self._awaiting_readback.items() if key[0] != actuator
        }

    def _control(
        self,
        snap: Snapshot,
        derived: Derived,
        now: datetime,
        snow: datetime,
        present: Mapping[_Key, CommandValue],
    ) -> None:
        """Sicherer Zustand und Übergabe (Plan 2b, Präzisierungen 3 und 4), dann die Regelung.

        ``snow`` ist die Zeit der Safety-Schicht, ``now`` die Wanduhr, ``present`` das Rücklesen
        dieses Zyklus. Ausgeführt wird in der Reihenfolge von Design 4.2, berechnet in dieser:

        1. Die Fähigkeiten aller Aktoren. Für jeden Aktor, dessen wirksame Betriebsart wechselte
           (``_switched_modes``), vergisst die Regelung ihre letzten Werte.
        2. Die Befehle der Regelung (``RegulationLink.step``). Sie regelt nur in CONTROL und
           DEGRADED bei eingeschaltetem Regelbetrieb (Plan 2c-1, Design 4.3). In jedem anderen
           Zyklus ruht sie (``idle``): in START, OBSERVE und SAFE, ohne Absicht und bei
           ausgeschaltetem Regelbetrieb, auch während der Übergabe danach. Blockiert sind für sie
           die Arten mit offenem Schreibvorgang und jede Art eines Aktors, der übergeben wird: Die
           Übergabe geht der Regelung vor. Die Annahmezeit liest die Laufzeit nach der Absicht
           (``IntentStore``); so ist sie nie älter als deren Annahme. Die Sperren bekommt sie wie
           der ``SafetyContext``: Ist Victron gesperrt, sendet der Akku-Sollwert nichts
           (Plan 2c-2a, Präzisierung 8).
        3. Erst danach die des ``SafeStateKeeper`` (``_keeper_commands``): Er zählt einen Versuch
           schon beim Berechnen. Bricht der Zyklus davor ab, hat er so keinen verbraucht; eine
           Ausnahme der Regelung selbst fängt ``RegulationLink``.
        4. Ein ``SafetyContext`` für alle Befehle, mit der Wallbox je Fahrzeug aus der Zuordnung
           der Laderegelung (``vehicle_wallbox``).
        5. Erst die Befehle des Keepers, dann die der Regelung gehen an den Ausführer und damit
           durch die Safety-Schicht (``_execute``). Übergibt der Ausführer einen Wert des Keepers,
           vergisst die Regelung ihre Werte für diesen Aktor: Das Gerät ändert sich ohne sie. Die
           Befehle der Regelung schreiben zu den Eingaben jeder Entscheidung ihre eigenen ins Log:
           die der Ladepunkte Überschuss, Grenze des Wächters und Ist-Werte, der Override PV,
           Hauslast, Autos, Fehlbetrag, erlaubten Anteil, Ziel und Ist-Override.
        6. Jedes Urteil über einen Befehl der Regelung geht an sie zurück (``note_verdict``), auch
           ``None``. ``not_sent`` heißt: Das Urteil ließ senden, aber danach ist kein
           Schreibvorgang offen.
        7. Am Ende legt ``RegulationLink.finish`` Health und Alarme der Regelung ab.

        Warf die Regelung in diesem Zyklus (``RegulationLink.failed``, Plan 2c-2a,
        Präzisierung 15), führt die Laufzeit keinen ihrer Befehle mehr aus; die des Keepers laufen
        trotzdem. Übergeben ist ein Aktor schon vorher, in ``_update_operation``. Die
        Entscheidungen tragen die Wanduhr ``now``. Fähigkeiten, offene Schreibvorgänge und die
        owned Aktoren bleiben für ``health`` stehen.
        """
        capabilities = {
            actuator_id: actuator.capabilities(snap, snow)
            for actuator_id, actuator in self.actuators.items()
        }
        self.regulation.forget(self._switched_modes())
        locks = {actuator: lock.reasons for actuator, lock in self._active_locks.items()}
        intent = self.intents.current
        if self.operating.state in _REGULATION_STATES and intent is not None and intent.control:
            handback = {(actuator, kind) for actuator in self._handing_back for kind in CommandKind}
            regulated = self.regulation.step(
                snap=snap,
                derived=derived,
                now=now,
                snow=snow,
                present=present,
                capabilities=capabilities,
                actuators=self.actuators,
                intent=intent,
                accepted_at=self.intents.accepted_at,
                blocked=self.executor.pending() | handback,
                connected=self.store.connected,
                locks=locks,
            )
        else:
            self.regulation.idle(snow)
            regulated = []
        keeper = self._keeper_commands(snap, snow, present)
        if keeper or regulated:
            ctx = safety_context(
                now=snow,
                operating=self.operating.state,
                modes={actuator: mode.effective for actuator, mode in self._modes.items()},
                locks=locks,
                inputs=self._input_status,
                snap=snap,
                phase_import_a=derived.phase_import_a,
                wallbox_phase_a=derived.wallbox_phase_a,
                present=present,
                vehicle_wallbox=self.regulation.vehicle_wallbox(),
            )
            inputs = decision_inputs(snap, derived.phase_import_a)
            for command in keeper:
                if self._execute(command, ctx, snap, now, inputs)[1]:
                    self.regulation.forget({command.actuator})
            for found in regulated:
                if self.regulation.failed:
                    break
                command = found.command
                verdict, sent = self._execute(command, ctx, snap, now, {**inputs, **found.inputs})
                not_sent = verdict is not None and verdict.send and not sent
                self.regulation.note_verdict(command, verdict, not_sent=not_sent)
        self._capabilities = capabilities
        self._pending = self.executor.pending()
        self._owned = self.ownership.owned
        self.regulation.finish(now)

    def _switched_modes(self) -> set[str]:
        """Die Aktoren, deren wirksame Betriebsart seit dem letzten Zyklus gewechselt hat.

        Gemerkt wird in jedem Zyklus, auch ohne Regelung. Ein Wechsel (Dry-Run ↔ live) heißt für
        die Regelung: Ein Dry-Run-Urteil gilt nicht als gesendet, sonst ginge nach dem
        Scharfschalten etwa „Laden an“ erst mit der nächsten Änderung des Wunsches hinaus.
        """
        effective = {actuator: mode.effective for actuator, mode in self._modes.items()}
        switched = {a for a, mode in effective.items() if self._effective.get(a, mode) != mode}
        self._effective = effective
        return switched

    def _keeper_commands(
        self, snap: Snapshot, snow: datetime, present: Mapping[_Key, CommandValue]
    ) -> list[Command]:
        """Die Befehle des ``SafeStateKeeper`` in diesem Zyklus.

        In CONTROL, DEGRADED und SAFE für die Ziele aus ``_targets``, live nur an die wirksam live
        geschalteten Aktoren. In START und OBSERVE bekommt er keine Ziele; das beendet die
        Vorfälle, auch eine gescheiterte Art, und es gibt keine Befehle.
        """
        state, pending = self.operating.state, self.executor.pending()
        if state not in _WRITING_STATES:
            self.keeper.commands(snow, [], present, pending, frozenset())
            return []
        live = frozenset(
            actuator
            for actuator, mode in self._modes.items()
            if mode.effective == ActuatorMode.LIVE
        )
        return self.keeper.commands(snow, self._targets(snap, state), present, pending, live)

    def _execute(
        self,
        command: Command,
        ctx: SafetyContext,
        snap: Snapshot,
        now: datetime,
        inputs: Mapping[str, float | None],
    ) -> tuple[Verdict | None, bool]:
        """Gibt einen Befehl an den Ausführer: sein Urteil und ob der Wert übergeben wurde.

        Übergeben ist er, wenn das Urteil senden ließ und der Schreibvorgang danach offen ist.
        Dann hält der Aktor Werte des EMS (``Ownership.note_sent``); ein Befehl, der nicht
        gesendet wurde, macht ihn nicht dazu.
        """
        verdict = self.executor.execute(command, ctx, snap, now, inputs)
        sent = (
            verdict is not None
            and verdict.send
            and (command.actuator, command.kind) in self.executor.pending()
        )
        if sent:
            self.ownership.note_sent(command.actuator)
        return verdict, sent

    def _targets(self, snap: Snapshot, state: OperatingState) -> list[SafeTarget]:
        """Die Ziele des Zyklus: zuerst die des sicheren Zustands, dann die Übergaben.

        Beide in der Reihenfolge des Katalogs. Ein Aktor, der in beiden vorkommt, bekommt je Art
        nur einen Befehl, mit dem Grund des sicheren Zustands (``SafeStateKeeper``).
        """
        handback = [
            SafeTarget(actuator, "handback")
            for actuator in self.limits.actuators
            if actuator in self._handing_back
        ]
        return [*self._safe_targets(snap, state), *handback]

    def _safe_targets(self, snap: Snapshot, state: OperatingState) -> list[SafeTarget]:
        """Die Ziele des sicheren Zustands (Spec 8.4) in der Reihenfolge des Katalogs.

        In SAFE ist das jeder nicht gesperrte Aktor, außer einem Tesla, der laut Tessie nicht lädt
        (``_tesla_not_charging``): Für ihn gäbe es nur Versuche, die nicht gesendet werden, dazu den
        Alarm ``actuator.failed`` und Einträge im Rate-Limiter. Bei Netzausfall (Inselbetrieb)
        bekommen EVCS und Tesla zusätzlich „Laden aus“. In den anderen Zuständen gibt es keine
        Ziele.
        """
        if state != OperatingState.SAFE:
            return []
        island = number(snap, "system.active_in_source") == ISLAND_SOURCE
        return [
            SafeTarget(actuator, "safe_state", stop_charging=island)
            for actuator, spec in self.limits.actuators.items()
            if actuator not in self._active_locks and not _tesla_not_charging(snap, spec)
        ]

    def _heartbeat(self, snow: datetime) -> None:
        """Heartbeat oder Freigabe am Ende des Zyklus, auf der Uhr der Safety-Schicht.

        Die Regel ist Präzisierung 2 des Plans 2b:

        - **Fehlerfrei** ist der Zyklus (Präzisierung 5), wenn für keinen Victron- oder EVCS-Aktor
          ein Schreibvorgang scheiterte (Fehler, Zeitüberschreitung, nicht gesendet) und keine
          Sicherwert-Art dieser Aktoren gescheitert ist; Fehler beim Tesla zählen nicht. Eine
          Ausnahme bricht den Zyklus schon vorher ab; eine in der Regelung fängt
          ``RegulationLink``, und der Zyklus ist dann nicht fehlerfrei (``failed``, Plan 2c-2a,
          Präzisierung 15).
        - **Der Zähler** darf aus einem fehlerfreien Zyklus in CONTROL, DEGRADED und SAFE hinaus und
          in OBSERVE zum Scharfschalten: wenn das Gate nur noch auf den Watchdog wartet, er nicht
          schon für diesen core scharf ist, seinen Zustand meldet und das konfigurierte Timeout
          (``timeout_matches``). Mit unbekanntem oder anderem Timeout würde er nie scharf für diesen
          core, und der core hielte ihn aus OBSERVE heraus endlos am Leben. So bleibt der Zähler
          aus, ein scharfer Watchdog löst aus, und der Alarm ``watchdog.timeout`` nennt die Abhilfe.
        - **Die Freigabe 0** nur, wenn kein Victron- oder EVCS-Aktor mehr Werte des EMS hält
          (Präzisierung 3), für keinen von ihnen ein Schreibvorgang offen ist, gleich ob ihn der
          Keeper oder die Regelung sendete (Design 4.5: Er könnte sonst nach der Freigabe am
          Gerät ankommen), und der core in diesem Lauf für den Watchdog zuständig ist: ab dem
          ersten Bedarf oder dem ersten owned Victron- oder EVCS-Aktor, auch beim Start. Eine Probe
          oder das Teil-2-Werkzeug machen den Watchdog mit eigenen Heartbeats scharf und lösen ihn
          aus, während der core im Dry-Run läuft; ein core, der nie zuständig war, gibt ihn nicht
          frei, auch nicht zum Aufräumen nach der Auslösung.
        """
        failures = self.executor.take_failures()
        owned = self.ownership.owned & self._needs_watchdog
        writing = any(actuator in self._needs_watchdog for actuator, _ in self.executor.pending())
        if self._watchdog_required or owned:
            self._responsible = True
        failed_write = any(write.actuator in self._needs_watchdog for write in failures)
        failed_kind = any(actuator in self._needs_watchdog for actuator, _ in self.keeper.failed)
        cycle_ok = not failed_write and not failed_kind and not self.regulation.failed
        state = self.operating.state
        arming = (
            state == OperatingState.OBSERVE
            and self._gate_ready
            and not self._watchdog_armed
            and self._watchdog.state is not WatchdogState.UNKNOWN
            and timeout_matches(self._watchdog, self._cfg.victron.watchdog.timeout_s)
        )
        self.heartbeat.on_cycle(
            snow,
            required=self._watchdog_required,
            allowed=cycle_ok and (state in _WRITING_STATES or arming),
            release_allowed=self._responsible and not owned and not writing,
            watchdog=self._watchdog.state,
            echo=self._watchdog.heartbeat,
        )

    def _decide(self, decision: Decision) -> None:
        """Senke des Ausführers: sofort in den Stream, am Ende des Zyklus in den Spool."""
        self._cycle_decisions.append(decision)
        self.broadcast.publish({"type": "decision", "data": decision_to_json(decision)})

    def _flush_decisions(self) -> None:
        """Schreibt die Entscheidungen des Zyklus in einem Zug in den Spool.

        Alle tragen dieselbe Zeit; ein Leser sieht sie ganz oder gar nicht. Ein Schreibfehler
        kommt ins Log (``DecisionSpool``) und unterbricht nichts.
        """
        if self._cycle_decisions:
            decisions, self._cycle_decisions = self._cycle_decisions, []
            self.decision_spool.append_many(decisions)

    def _prune_decisions(self, now: datetime, snow: datetime) -> None:
        """Räumt das Entscheidungslog im ersten Zyklus und danach stündlich auf.

        ``cycle_once`` ruft es nach dem Flush der Entscheidungen auf, auch nach einem gescheiterten
        Zyklus. Die Stunde zählt auf der Uhr der Safety-Schicht (``snow``): Ein Sprung der Wanduhr
        häuft das Aufräumen nicht und setzt es nicht aus. Das Alter der Tagesdateien misst der
        Spool an der Wanduhr (``now``), denn nach ihr sind die Entscheidungen datiert. Ein Fehler
        kommt ins Log und unterbricht den Zyklus nicht; der nächste Versuch folgt eine Stunde
        später.
        """
        last = self._last_prune
        if last is not None and snow - last < PRUNE_INTERVAL:
            return
        self._last_prune = snow
        try:
            self.decision_spool.prune(now)
        except Exception:  # Aufräumen ist Nebensache: Der Zyklus geht vor
            log.exception("Entscheidungslog nicht aufgeräumt")

    def _control_alarms(self, snow: datetime) -> list[Alarm]:
        """Alarme der Safety-Schicht (Schreibbudget), des sicheren Zustands und der Aktoren."""
        return [
            *self.layer.alarms(snow),
            *self.keeper.alarms(snow, self._labels, self._needs_watchdog),
            *(alarm for actuator in self.actuators.values() for alarm in actuator.alarms(snow)),
        ]

    def _update_operation(
        self,
        snap: Snapshot,
        plausibility: Sequence[Alarm],
        now: datetime,
        present: Mapping[_Key, CommandValue],
        *,
        checked: bool,
    ) -> None:
        """Eingangsprüfung, Sperren, Werte des EMS, Watchdog, Betriebsarten und Betriebszustand.

        Die Eingangsprüfung läuft in jedem Zyklus mit den Plausibilitätsalarmen dieses Zyklus
        (ohne den Abrechnungsalarm ``plaus.grid_counter``). Die Sperren bekommen ihre Ursachen nur
        in Zyklen, in denen Selbstprüfung und Erkennung mitregelnder Systeme liefen; eine fremde
        Änderung (``competitor.foreign.<aktor>``) zählt wie ein mitregelndes System.

        Werte des EMS (Plan 2b, Präzisierung 3), mit ``present``, dem Rücklesen dieses Zyklus:

        - Ein gesperrter Aktor, den nachweislich ein anderes System regelt
          (``controlled_elsewhere``: ein Alarm ``competitor.*`` oder ein Prüfpunkt mit ``fail``),
          hält ab sofort keine Werte des EMS mehr und wird nicht übergeben; seine Beobachtung
          endet, und die Regelung vergisst ihre letzten Werte für ihn (``RegulationLink.forget``):
          Sein Gerät ändert sich ohne sie. Eine Sperre nur wegen eines nicht lesbaren Prüfpunkts
          (``unknown``) lässt ihn owned, etwa im ersten Zyklus ohne Daten: Sein Gerät kann noch
          Werte des EMS halten. Seine Übergabe lehnt dann die Sperre ab, und der Watchdog
          übernimmt.
        - Ein owned Aktor, der nicht mehr live gewünscht ist, wird übergeben. Zeigt er seine
          Sicherwerte, ist er übergeben, auch wenn der Watchdog sie gesetzt hat, und seine
          Beobachtung endet. Das gilt in jedem Zustand und vor dem Bedarf: Sonst ginge in diesem
          Zyklus noch ein Zähler hinaus, etwa an einen ausgelösten Watchdog, dessen Sicherwerte
          gerade zurückgelesen wurden.
        - Bis dahin läuft die Übergabe, auch eine gescheiterte. Victron und EVCS übernimmt dann der
          Watchdog; beim Tesla bleibt der Alarm, bis sein Strom stimmt oder er wieder live
          gewünscht ist.

        Meldet der Watchdog „ausgelöst“, vergisst die Laufzeit in jedem Zyklus die Erwartungen der
        Erkennung fremder Änderungen für Victron und EVCS (``_forget``): Was er setzt, ist keine
        fremde Änderung (Design 10). Neue Erwartungen entstehen erst wieder aus bestätigten Werten.

        Scharf zählt der Watchdog nur für diesen core (``armed_for_core``): mit dem Echo eines
        eigenen Zählers und dem erwarteten Timeout. Den Watchdog braucht der Betrieb, solange ein
        Victron- oder EVCS-Aktor im Regelbetrieb live gewünscht ist oder übergeben wird
        (Präzisierung 2).
        """
        self._input_status = self._inputs.evaluate(snap, plausibility, now)
        if checked:
            competitors = [*self._competitor_alarms, *self._foreign_alarms]
            self.locks.update(now, lock_causes(competitors, self._selfcheck, self.limits))
            self._controlled_elsewhere = controlled_elsewhere(
                competitors, self._selfcheck, self.limits
            )
        self._active_locks = self.locks.active(now)
        taken = self._controlled_elsewhere.intersection(self._active_locks)
        for actuator in self.ownership.drop(taken):
            self._forget(actuator)
            self.regulation.forget({actuator})
        intent = self.intents.current
        self._watchdog = read_watchdog(snap)
        if self._watchdog.state is WatchdogState.TRIPPED:
            for actuator in self._needs_watchdog:
                self._forget(actuator)
        self._watchdog_armed = armed_for_core(
            self._watchdog, self.heartbeat.recent, self._cfg.victron.watchdog.timeout_s
        )
        wanted = live_wanted(intent, self.limits)
        for actuator in self.ownership.handing_back(wanted):
            if safe_values_reached(self.limits, SafeTarget(actuator, "handback"), present):
                self.ownership.release(actuator)
                self._forget(actuator)
        self._handing_back = self.ownership.handing_back(wanted)
        self._watchdog_required = watchdog_required(
            intent, self.limits, handing_back=self._handing_back
        )
        self._modes = effective_modes(
            intent, self.limits, self._watchdog_armed, handing_back=self._handing_back
        )
        inputs = OperatingInputs(
            control_requested=intent is not None and intent.control,
            data_fresh=self._data_fresh(now),
            phases_ok=number(snap, "vebus.phases") == self._cfg.grid.phases,
            watchdog_required=self._watchdog_required,
            watchdog_armed=self._watchdog_armed,
            safe_reasons=_safe_reasons(snap),
            degraded_reasons=self._degraded_reasons(self._active_locks),
            handback_pending=bool(self._handing_back),
        )
        self._gate_ready = gate_ready_except_watchdog(inputs)
        transition = self.operating.step(now, inputs)
        if transition is not None:
            self._announce(transition)

    def _data_fresh(self, now: datetime) -> bool:
        """Alle ``CRITICAL_SIGNALS`` sind seit mindestens 30 s ununterbrochen ``OK``."""
        return all(
            (since := self.store.ok_since(signal)) is not None and since <= now - FRESH_FOR
            for signal in CRITICAL_SIGNALS
        )

    def _degraded_reasons(self, locks: Mapping[str, LockInfo]) -> tuple[str, ...]:
        """Gründe für den eingeschränkten Betrieb, in dieser Reihenfolge:

        1. jede Wallbox, deren Quelle registriert, aber nicht verbunden ist
        2. jedes Fahrzeug mit Tessie, dessen Tessie-Quelle registriert, aber nicht verbunden ist
        3. jede aktive Sperre, in der Reihenfolge des Aktorkatalogs
        """
        reasons: list[str] = []
        for key, wallbox in self._cfg.wallboxes.items():
            if self.store.connected(key) is False:
                if isinstance(wallbox, EvcsConfig):
                    reasons.append(f"EVCS „{key}“ nicht erreichbar.")
                else:
                    reasons.append(f"Wall Connector „{key}“ nicht erreichbar.")
        for key, vehicle in self._cfg.vehicles.items():
            if vehicle.tessie is not None and self.store.connected(f"tessie:{key}") is False:
                reasons.append(f"Tessie für „{vehicle.name}“ nicht erreichbar.")
        for actuator, spec in self.limits.actuators.items():
            lock = locks.get(actuator)
            if lock is not None:
                reasons.append(f"{spec.label} gesperrt: {'; '.join(lock.reasons)}")
        return tuple(reasons)

    def _announce(self, transition: Transition) -> None:
        """Meldet einen Wechsel des Betriebszustands im Stream und im Log."""
        mode, previous = transition.state.value, transition.previous.value
        reasons, ts = list(transition.reasons), transition.ts.isoformat()
        data = {"mode": mode, "previous": previous, "reasons": reasons, "ts": ts}
        self.broadcast.publish({"type": "mode_change", "data": data})
        log.info("Betriebszustand gewechselt", mode=mode, previous=previous, reasons=reasons)

    def _operation_alarms(self, now: datetime) -> list[Alarm]:
        """``core.safe``, solange SAFE gilt; ``intent.file``, solange die Absicht unlesbar war."""
        alarms: list[Alarm] = []
        if self.operating.state == OperatingState.SAFE:
            message = f"Sicherer Zustand: {'; '.join(self.operating.reasons)}"
            since = self.operating.since or now  # in SAFE immer gesetzt
            alarms.append(Alarm("core.safe", Severity.ERROR, message, since))
        if self.intents.load_problem is not None:
            alarms.append(Alarm("intent.file", Severity.WARNING, INTENT_FILE_PROBLEM, now))
        return alarms

    def _flows(self, derived: Derived) -> dict[str, float] | None:
        pv, grid = derived.pv_total_w, derived.grid_w
        battery, house = derived.battery_w, derived.house_load_w
        if pv is None or grid is None or battery is None or house is None:
            return None  # Bilanz unbekannt: zählt nicht als abgedeckt
        wallboxes = {name: derived.wallbox_w.get(name) or 0.0 for name in self._cfg.wallbox_order}
        return allocate(pv, grid, battery, house, wallboxes)

    def _check_counters(self, closed: SlotFlows, now: datetime) -> None:
        alarm = check_grid_counters(closed, now)
        if alarm is not None and self._counter_alarm is not None:
            alarm = replace(alarm, since=self._counter_alarm.since)  # besteht seit dem ersten Slot
        self._counter_alarm = alarm

    def _check_due(self, now: datetime) -> bool:
        last = self._last_check
        if last is not None and (now - last).total_seconds() < CHECK_INTERVAL_S - CHECK_TOLERANCE_S:
            return False
        self._last_check = now
        return True

    def _record_cycle(self, duration_ms: float, now: datetime) -> None:
        self._cycle_ms.append(duration_ms)
        if duration_ms > CYCLE_LIMIT_MS:
            self._slow_cycles += 1
            if self._slow_cycles >= OVERRUN_CYCLES and self._overrun_since is None:
                self._overrun_since = now
        else:
            self._slow_cycles = 0
            self._overrun_since = None

    def _overrun_alarm(self) -> list[Alarm]:
        if self._overrun_since is None:
            return []
        message = "Regelzyklus überschreitet 200 ms."
        return [Alarm("core.cycle_overrun", Severity.WARNING, message, self._overrun_since)]

    def _p95(self) -> float | None:
        if not self._cycle_ms:
            return None
        ordered = sorted(self._cycle_ms)
        return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]

    def _update_alarms(self, alarms: list[Alarm]) -> None:
        """Übernimmt die Alarme; eine ID, die schon in der vorigen Liste stand, behält ``since``."""

        def key(items: list[Alarm]) -> list[tuple[str, str, str]]:
            return [(a.id, a.severity.value, a.message) for a in items]

        since = {alarm.id: alarm.since for alarm in self._alarms}
        alarms = [
            replace(alarm, since=since[alarm.id]) if alarm.id in since else alarm
            for alarm in alarms
        ]
        changed = key(alarms) != key(self._alarms)
        self._alarms = alarms
        if changed:
            self.broadcast.publish(
                {"type": "alarm", "data": [_alarm_json(alarm) for alarm in alarms]}
            )

    # --- Betrieb -------------------------------------------------------------------------

    async def run(self) -> None:
        self._run_task = asyncio.current_task()
        async with asyncio.TaskGroup() as tasks:
            for adapter in self.adapters:
                tasks.create_task(adapter.run(), name=f"adapter:{adapter.name}")
            if self._writer is not None:
                tasks.create_task(self._writer.run(), name="influx-writer")
            tasks.create_task(self._cycle_loop(), name="cycle")
            if self._serve_api:
                tasks.create_task(self._serve(), name="api")

    async def _cycle_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            try:
                self.cycle_once()
            except Exception:  # ein fehlerhafter Zyklus darf die Erfassung nicht beenden
                log.exception("Zyklus fehlgeschlagen")
            next_tick += self.cycle_s
            delay = next_tick - loop.time()
            if delay < 0:
                next_tick, delay = loop.time(), 0.0  # Überlauf: Takt neu ausrichten
            await asyncio.sleep(delay)

    async def _serve(self) -> None:
        api = create_api(self, self._secrets.internal_token)
        config = uvicorn.Config(
            api,
            host=self._cfg.core_api.host,
            port=self._cfg.core_api.port,
            log_config=None,
            access_log=False,
            lifespan="off",
        )
        self._api_server = _ApiServer(config)
        await self._api_server.serve()

    async def shutdown(self) -> None:
        """Schreibt den laufenden Slot und die Warteschlange, dann werden die Tasks beendet.

        Laufende Tessie-Befehle enden, bevor der gemeinsame HTTP-Client schließt.
        """
        closed = self._accumulator.flush()
        if closed is not None:
            self._sampler.write_slot_flows(closed)
        if self._writer is not None:
            await self._writer.flush_now()
        if self._api_server is not None:
            self._api_server.should_exit = True
        task = self._run_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.wait({task})
        for actuator in self.actuators.values():
            if isinstance(actuator, TeslaActuator):
                await actuator.aclose()
        if self._http is not None:
            await self._http.aclose()
