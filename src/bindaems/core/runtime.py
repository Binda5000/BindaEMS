"""Laufzeit des core in Phase 1a (nur beobachten): Adapter, 1-s-Zyklus, Telemetrie, Prüfungen.

Je Zyklus: Snapshot → abgeleitete Größen → Plausibilität → Flusszuordnung → Viertelstunden-
Summen → Telemetrie → (alle 10 s) Selbstprüfung und mitregelnde Systeme → Stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Generator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import httpx
import structlog
import uvicorn

from bindaems import __version__
from bindaems.core.accounting.flows import SlotAccumulator, allocate
from bindaems.core.adapters.base import Adapter
from bindaems.core.adapters.evcs import EvcsAdapter, PymodbusReader
from bindaems.core.adapters.homeassistant import HaAdapter
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import AiomqttTransport, VictronMqttAdapter
from bindaems.core.api.app import HealthReport, create_api, snapshot_to_json
from bindaems.core.checks.competitors import detect_competitors
from bindaems.core.checks.selfcheck import CheckResult, run_selfcheck
from bindaems.core.state.derived import Derived, derive
from bindaems.core.state.plausibility import PlausibilityMonitor, check_grid_counters
from bindaems.core.state.store import StateStore
from bindaems.core.telemetry.sampler import TelemetrySampler
from bindaems.shared.broadcast import Broadcast
from bindaems.shared.config import Config, EvcsConfig, Secrets
from bindaems.shared.domain import Alarm, Severity, SlotFlows
from bindaems.shared.influx.lineprotocol import PointSink
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
from bindaems.shared.timeutil import Clock, SystemClock

log = structlog.get_logger(__name__)

CHECK_INTERVAL_S = 10.0
CHECK_TOLERANCE_S = 0.05
CYCLE_LIMIT_MS = 200.0
OVERRUN_CYCLES = 3
CYCLE_HISTORY = 300


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
    ) -> None:
        self._cfg = cfg
        self._secrets = secrets
        self._clock = clock or SystemClock()
        self.cycle_s = cycle_s
        self._serve_api = serve_api
        self._timer = timer
        self.store = StateStore(self._clock)
        self.broadcast = Broadcast()
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

    # --- CoreView ------------------------------------------------------------------------

    def health(self) -> HealthReport:
        return HealthReport(
            mode="OBSERVE",
            version=__version__,
            adapters=[asdict(adapter.health()) for adapter in self.adapters],
            selfcheck=[asdict(result) for result in self._selfcheck],
            alarms=[_alarm_json(alarm) for alarm in self._alarms],
            cycle_ms_p95=self._p95(),
        )

    def state(self) -> dict[str, Any]:
        if self._last_state is None:
            snap = self.store.snapshot()
            return snapshot_to_json(snap, derive(snap, self._cfg))
        return self._last_state

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        return self.broadcast.subscribe()

    # --- Zyklus --------------------------------------------------------------------------

    def cycle_once(self) -> None:
        started = self._timer()
        now = self._clock.now()
        snap = self.store.snapshot()
        derived = derive(snap, self._cfg)
        plausibility = self._plausibility.evaluate(snap, derived, now)
        closed = self._accumulator.add(now, self._flows(derived), snap)
        if closed is not None:
            self._sampler.write_slot_flows(closed)
            self.broadcast.publish({"type": "slot_flows", "data": _slot_json(closed)})
            self._check_counters(closed, now)
        self._sampler.on_cycle(snap, derived)
        if self._check_due(now):
            self._competitor_alarms = detect_competitors(snap, self._cfg, now)
            self._selfcheck = run_selfcheck(snap, self._cfg, self.store.ok_since, now)
            self.selfcheck_runs += 1
        state = snapshot_to_json(snap, derived)
        self._last_state = state
        self.broadcast.publish({"type": "state", "data": state})
        self._record_cycle((self._timer() - started) * 1000.0, now)
        counter = [self._counter_alarm] if self._counter_alarm is not None else []
        self._update_alarms(
            [*plausibility, *self._competitor_alarms, *counter, *self._overrun_alarm()]
        )

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
        def key(items: list[Alarm]) -> list[tuple[str, str, str]]:
            return [(a.id, a.severity.value, a.message) for a in items]

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
        """Schreibt den laufenden Slot und die Warteschlange, dann werden die Tasks beendet."""
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
        if self._http is not None:
            await self._http.aclose()
