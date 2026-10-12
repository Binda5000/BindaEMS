"""Laufzeit des core: Adapter, 1-s-Zyklus, Telemetrie, Prüfungen, Betriebszustand, Aktoren und
Heartbeat.

Je Zyklus: Snapshot → abgeleitete Größen → Plausibilität → Flusszuordnung → Viertelstunden-
Summen → Telemetrie → (alle 10 s) Selbstprüfung und mitregelnde Systeme → Eingangsprüfung →
(nach jeder Prüfung) Sperren → Watchdog, wirksame Betriebsarten und aktive Sperren →
Betriebszustand → Rücklesen und sicherer Zustand → (beim Start und stündlich) Aufräumen des
Entscheidungslogs → Stream → Alarme → Heartbeat. Am Ende gehen die Entscheidungen des Zyklus in
einem Zug in den Spool, auch wenn der Zyklus scheitert.

Auf Geräte schreibt der core den Heartbeat an den eigenen Watchdog am Cerbo, den nur aus einem
Zyklus, der ohne Ausnahme bis zum Ende läuft, und die Sicherwerte des sicheren Zustands (Plan 2b,
Präzisierung 4). Jeder Befehl geht durch die Safety-Schicht; im Dry-Run wird er nur protokolliert.
Fristen, Limits, Budget und Versuche laufen auf der Uhr der Safety-Schicht (``AnchoredClock``),
Messwerte, Viertelstunden und die Zeitpunkte der Entscheidungen auf der Wanduhr.
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
from bindaems.core.actuators.victron import VictronActuator
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
from bindaems.core.control.actuator import Actuator, Capabilities
from bindaems.core.control.context import decision_inputs, safety_context
from bindaems.core.control.executor import CommandExecutor
from bindaems.core.control.intent import INTENT_FILE_PROBLEM, IntentReply, IntentStore
from bindaems.core.control.operating import OperatingInputs, OperatingStateMachine, Transition
from bindaems.core.control.safe_state import SafeStateKeeper, SafeTarget
from bindaems.core.persistence.decision_spool import DecisionSpool
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.inputs import InputCheck
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.limits import ActuatorSpec, HardLimits
from bindaems.core.safety.locks import LockInfo, LockManager, lock_causes
from bindaems.core.safety.modes import ModeInfo, effective_modes, watchdog_required
from bindaems.core.safety.types import InputStatus
from bindaems.core.state.derived import Derived, derive
from bindaems.core.state.plausibility import PlausibilityMonitor, check_grid_counters
from bindaems.core.state.store import StateStore
from bindaems.core.telemetry.sampler import TelemetrySampler
from bindaems.core.watchdog_client.heartbeat import HeartbeatClient
from bindaems.core.watchdog_client.status import WatchdogStatus, read_watchdog, watchdog_alarms
from bindaems.shared.broadcast import Broadcast
from bindaems.shared.config import Config, EvcsConfig, Secrets
from bindaems.shared.control import VICTRON, ActuatorMode, CommandKind, Intent, OperatingState
from bindaems.shared.decisions import Decision, decision_to_json
from bindaems.shared.domain import Alarm, Quality, Severity, SlotFlows, Snapshot
from bindaems.shared.influx.lineprotocol import PointSink
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
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
# Fähigkeiten eines Aktors vor dem ersten Zyklus: noch nicht geprüft, also nicht steuerbar
_NOT_CHECKED: Final = Capabilities(False)


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
    pending: frozenset[tuple[str, CommandKind]],
) -> dict[str, Any]:
    """Ein Aktor für ``health``; ``pending`` nennt die Arten mit offenem Schreibvorgang."""
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
    }


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
    ) -> None:
        """Die Laufzeit mit Adaptern, Aktoren, Safety-Schicht und Entscheidungslog.

        - ``clock`` ist die Wanduhr: Messwerte, Viertelstunden, Zeitpunkte der Entscheidungen.
        - ``safety_clock`` ist die Uhr der Safety-Schicht, standardmäßig ``AnchoredClock``:
          Fristen, Rate-Limits, Schreibbudget, sicherer Zustand und Tesla-Fehler.
        - ``mqtt_write`` reiht einen Schreibauftrag (Pfad, Wert) an den Cerbo ein. Ohne ihn
          schreibt der Victron-Adapter (``VictronMqttAdapter.write``); ohne Victron-Adapter wird
          kein Auftrag angenommen.
        - ``watchdog_send`` stellt den Heartbeat zu; ohne ihn geht er über ``mqtt_write``.
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
        self.intents = IntentStore(cfg.core.data_dir / "intent.json", self.limits)
        self.intents.load()
        self.locks = LockManager(cfg.safety.lock_release_s)
        self.operating = OperatingStateMachine()
        send = watchdog_send if watchdog_send is not None else self._send_heartbeat
        self.heartbeat = HeartbeatClient(send, cfg.victron.watchdog.heartbeat_s)
        self._inputs = InputCheck()
        # Ergebnis des letzten Zyklus für health(); vor dem ersten Zyklus aus der geladenen Absicht
        self._input_status = InputStatus(True)
        self._watchdog = WatchdogStatus.unknown()
        self._watchdog_required = watchdog_required(self.intents.current, self.limits)
        self._modes = effective_modes(self.intents.current, self.limits, self._watchdog.armed)
        self._active_locks: dict[str, LockInfo] = {}
        self._http: httpx.AsyncClient | None = None
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
        self._needs_watchdog = frozenset(
            actuator for actuator, spec in self.limits.actuators.items() if spec.needs_watchdog
        )
        self.executor = CommandExecutor(self.layer, self.actuators, self._decide, self._labels)
        self.keeper = SafeStateKeeper(self.limits)
        self._cycle_decisions: list[Decision] = []  # Entscheidungen des laufenden Zyklus
        self._last_prune: datetime | None = None
        # Stand des letzten Zyklus für health()
        self._capabilities: dict[str, Capabilities] = {}
        self._pending: frozenset[tuple[str, CommandKind]] = frozenset()
        self.selfcheck_runs = 0
        self._selfcheck: list[CheckResult] = []
        self._competitor_alarms: list[Alarm] = []
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
        capabilities, pending = self._capabilities, self._pending
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
                )
                for actuator, spec in self.limits.actuators.items()
            ],
            watchdog=watchdog_to_json(
                self._watchdog, self.heartbeat.status, required=self._watchdog_required
            ),
            inputs={"ok": self._input_status.ok, "problems": list(self._input_status.problems)},
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

        Eine Ausnahme, auch aus einem Aktor, bricht den Zyklus ab und lässt den Heartbeat ausfallen.
        """
        try:
            self._cycle()
        finally:
            self._flush_decisions()

    def _cycle(self) -> None:
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
        checked = self._check_due(now)
        if checked:
            self._competitor_alarms = detect_competitors(snap, self._cfg, now)
            self._selfcheck = run_selfcheck(snap, self._cfg, self.store.ok_since, now)
            self.selfcheck_runs += 1
        self._update_operation(snap, plausibility, now, checked=checked)
        self._control(snap, derived, now, snow)
        self._prune_decisions(now, snow)
        state = snapshot_to_json(snap, derived)
        self._last_state = state
        self.broadcast.publish({"type": "state", "data": state})
        self._record_cycle((self._timer() - started) * 1000.0, now)
        counter = [self._counter_alarm] if self._counter_alarm is not None else []
        self._update_alarms(
            [
                *plausibility,
                *self._competitor_alarms,
                *counter,
                *self._overrun_alarm(),
                *watchdog_alarms(self._watchdog, self._watchdog_required, now),
                *self._operation_alarms(now),
                *self._control_alarms(snow),
            ]
        )
        # als Letztes: Eine Ausnahme in einem früheren Schritt lässt den Heartbeat ausfallen
        self.heartbeat.on_cycle(now, required=self._watchdog_required)

    def _control(self, snap: Snapshot, derived: Derived, now: datetime, snow: datetime) -> None:
        """Rücklesen und sicherer Zustand (Plan 2b, Präzisierung 4).

        ``snow`` ist die Zeit der Safety-Schicht, ``now`` die Wanduhr.

        1. Der Ausführer fragt die offenen Schreibvorgänge ab, in jedem Zustand.
        2. In CONTROL, DEGRADED und SAFE gehen die Befehle des ``SafeStateKeeper`` an den Ausführer
           und damit durch die Safety-Schicht. Ziele gibt es nur in SAFE (``_safe_targets``).
        3. In START und OBSERVE bekommt der ``SafeStateKeeper`` keine Ziele; das beendet die
           Vorfälle, auch eine gescheiterte Art.

        Die Entscheidungen tragen die Wanduhr ``now``. Fähigkeiten und offene Schreibvorgänge
        bleiben für ``health`` stehen.
        """
        present = {
            (actuator_id, kind): value
            for actuator_id, actuator in self.actuators.items()
            for kind, value in actuator.present(snap).items()
        }
        self.executor.poll(snap, snow, now)
        state = self.operating.state
        if state in _WRITING_STATES:
            live = frozenset(
                actuator
                for actuator, mode in self._modes.items()
                if mode.effective == ActuatorMode.LIVE
            )
            targets = self._safe_targets(snap, state)
            commands = self.keeper.commands(snow, targets, present, self.executor.pending(), live)
            if commands:
                ctx = safety_context(
                    now=snow,
                    operating=state,
                    modes={actuator: mode.effective for actuator, mode in self._modes.items()},
                    locks={actuator: lock.reasons for actuator, lock in self._active_locks.items()},
                    inputs=self._input_status,
                    snap=snap,
                    phase_import_a=derived.phase_import_a,
                    wallbox_phase_a=derived.wallbox_phase_a,
                    present=present,
                )
                inputs = decision_inputs(snap, derived.phase_import_a)
                for command in commands:
                    self.executor.execute(command, ctx, snap, now, inputs)
        else:
            self.keeper.commands(snow, [], present, self.executor.pending(), frozenset())
        self._capabilities = {
            actuator_id: actuator.capabilities(snap, snow)
            for actuator_id, actuator in self.actuators.items()
        }
        self._pending = self.executor.pending()

    def _safe_targets(self, snap: Snapshot, state: OperatingState) -> list[SafeTarget]:
        """Die Ziele des sicheren Zustands (Spec 8.4) in der Reihenfolge des Katalogs.

        In SAFE ist das jeder nicht gesperrte Aktor; bei Netzausfall (Inselbetrieb) bekommen EVCS
        und Tesla zusätzlich „Laden aus“. In den anderen Zuständen gibt es keine Ziele.
        """
        if state != OperatingState.SAFE:
            return []
        island = number(snap, "system.active_in_source") == ISLAND_SOURCE
        return [
            SafeTarget(actuator, "safe_state", stop_charging=island)
            for actuator in self.limits.actuators
            if actuator not in self._active_locks
        ]

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

        Die Stunde zählt auf der Uhr der Safety-Schicht (``snow``): Ein Sprung der Wanduhr häuft
        das Aufräumen nicht und setzt es nicht aus. Das Alter der Tagesdateien misst der Spool an
        der Wanduhr (``now``), denn nach ihr sind die Entscheidungen datiert. Ein Fehler kommt ins
        Log und unterbricht den Zyklus nicht; der nächste Versuch folgt eine Stunde später.
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
        self, snap: Snapshot, plausibility: Sequence[Alarm], now: datetime, *, checked: bool
    ) -> None:
        """Eingangsprüfung, Sperren, Watchdog, wirksame Betriebsarten und Betriebszustand.

        Die Eingangsprüfung läuft in jedem Zyklus mit den Plausibilitätsalarmen dieses Zyklus
        (ohne den Abrechnungsalarm ``plaus.grid_counter``). Die Sperren bekommen ihre Ursachen nur
        in Zyklen, in denen Selbstprüfung und Erkennung mitregelnder Systeme liefen.
        """
        self._input_status = self._inputs.evaluate(snap, plausibility, now)
        if checked:
            self.locks.update(
                now, lock_causes(self._competitor_alarms, self._selfcheck, self.limits)
            )
        intent = self.intents.current
        self._watchdog = read_watchdog(snap)
        self._watchdog_required = watchdog_required(intent, self.limits)
        self._modes = effective_modes(intent, self.limits, self._watchdog.armed)
        self._active_locks = self.locks.active(now)
        inputs = OperatingInputs(
            control_requested=intent is not None and intent.control,
            data_fresh=self._data_fresh(now),
            phases_ok=number(snap, "vebus.phases") == self._cfg.grid.phases,
            watchdog_required=self._watchdog_required,
            watchdog_armed=self._watchdog.armed,
            safe_reasons=_safe_reasons(snap),
            degraded_reasons=self._degraded_reasons(self._active_locks),
        )
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
