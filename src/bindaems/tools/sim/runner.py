"""Lauf eines Szenarios: die echte Laufzeit des core gegen das Anlagenmodell (Design 13.1).

``run_scenario`` baut die Laufzeit wie im Betrieb, nur ohne Adapter und API: Safety-Schicht,
Ausführer, Regelung (Laderegelung und Akku-Sollwert) und die echten Aktoren. Die ``ManualClock``
ist Wanduhr und Uhr der Safety-Schicht. Gefälscht sind nur die Geräte dahinter (Plan 2c-1,
Präzisierung 16): Der core schreibt über ``PlantModel.mqtt_write`` und erreicht Tessie über
``httpx.MockTransport`` mit ``PlantModel.tessie``; die Telemetrie verwirft ein ``_Discard``.

Die Konfiguration des Laufs macht ``sim_config``: Ablage des core, EVCS und Fahrzeuge mit Tessie
für den Live-Betrieb freigegeben, mit ``Scenario.victron_live`` auch Victron, die Werte von
``Scenario.control`` über ``ControlConfig`` und die von ``Scenario.vehicles`` über
``VehicleConfig``. Die Absicht schaltet dieselben Aktoren live
(``_intent``).

Mit ``Scenario.thermal`` hat der Lauf dazu den echten CoE-Adapter der Klimaanlage (Plan 6a,
Ruling R1): Er läuft als Task mit einem Port im Prozess (``SimCoePort``), der seine Pakete der
UVR im Modell gibt (``AirConditioner.receive``) und deren Pakete an seinen Empfang. Seine Uhr und
seine Pausen laufen in simulierter Zeit (``SimTimer``). Die Klimaanlage ist dann live freigegeben
und in der Absicht live.

Je simulierter Sekunde:

1. die Ereignisse der Sekunde
2. ``model.step``, mit Klimaanlage dann die fälligen Pausen des CoE-Adapters (``SimTimer.wake``,
   danach ``SETTLE_ROUNDS`` Runden des Event-Loops), dann ``model.publish``
3. ``rt.cycle_once()``
4. zehnmal ``await asyncio.sleep(0)``: Die Befehle an Tessie laufen bis zu ihrer Antwort
5. ``model.resolve``: Tessie beantwortet, was fällig ist
6. eine Probe (``Sample``, mit den Fahrzeugen der Konfiguration und dem Gast), alle
   ``HEALTH_EVERY_S`` dazu die Gesundheit
7. die Uhr eine Sekunde weiter

Am Ende liest der Lauf alle Entscheidungen und fährt die Laufzeit herunter. Die Kennzahlen kommen
aus Proben und Entscheidungen; für die Befehle an Tessie gilt dabei der Zeitpunkt, zu dem sie
Tessie erreichten (``sent_times``), nicht der ihres späteren Ergebnisses. Dazu sammelt der Lauf die
eigenen Werte der Regelung (``OwnValue``) und was sie davon vergisst (``Forgotten``): Er hüllt
``note_verdict``, ``forget`` und ``idle`` von ``rt.regulation`` ein (``RegulationLink``), denn
``UNCHANGED`` setzt den letzten eigenen Wert, ohne dass eine Entscheidung entsteht. So sieht die
Kennzahl auch die eigenen Werte des Akku-Sollwerts und die Pause nach einer Ausnahme in der
Regelung, denn der Link ruft dafür sein eigenes ``idle`` (Plan 2c-2a, Präzisierung 17).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import random
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import httpx
from pydantic import SecretStr

from bindaems.core.adapters.coe import CoeAdapter, DatagramPort
from bindaems.core.api.app import HealthReport
from bindaems.core.runtime import CoreRuntime
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import (
    CoeOffsetConfig,
    Config,
    ControlConfig,
    EvcsConfig,
    Secrets,
    VehicleConfig,
)
from bindaems.shared.control import (
    VICTRON,
    ActuatorMode,
    Command,
    CommandKind,
    CommandValue,
    GuestIntent,
    Intent,
    VehicleIntent,
    thermal_actuator,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.influx.lineprotocol import Point
from bindaems.shared.timeutil import ManualClock
from bindaems.tools.sim.metrics import (
    CarSample,
    Forgotten,
    Metrics,
    OwnValue,
    Sample,
    ThermalSample,
    measure,
)
from bindaems.tools.sim.model import GUEST_CAR, GUEST_MIN_A, AirConditioner, PlantModel
from bindaems.tools.sim.scenarios import Action, Scenario

HEALTH_EVERY_S: Final = 10  # so oft hält der Lauf die Gesundheit des core fest
SETTLE_ROUNDS: Final = 10  # Runden des Event-Loops nach jedem Zyklus
READ_LIMIT: Final = 1000  # Entscheidungen je Lesen
# Ergebnisse eines Befehls, der Tessie erreicht hat
_SENT_RESULTS: Final = frozenset({DecisionResult.OK, DecisionResult.ERROR, DecisionResult.TIMEOUT})
# Der Simulator erreicht kein Netz: Die Tokens gehen nur an das Modell.
_INTERNAL_TOKEN: Final = "simulator-ohne-netz-" + "0" * 16
_TESSIE_TOKEN: Final = "simulator"  # noqa: S105 - kein Geheimnis: erreicht nur das Modell


@dataclass(frozen=True)
class SimResult:
    """Das Ergebnis eines Laufs.

    - ``scenario``: das Szenario, ``metrics``: seine Kennzahlen
    - ``decisions``: alle Entscheidungen des core, aufsteigend
    - ``health``: alle ``HEALTH_EVERY_S`` die Gesundheit, mit der Sekunde des Laufs
    - ``samples``: je Sekunde eine Probe
    - ``failures``: die Verstöße gegen Kennzahlen und eigene Prüfungen, je ein Satz
    - ``sent``: je Kennung einer Entscheidung an ein Fahrzeug mit Tessie der Zeitpunkt, zu dem ihr
      Befehl Tessie erreichte (``sent_times``); fehlt er, gilt der der Entscheidung
    - ``usable_kwh``: die nutzbare Kapazität des Akkus der Konfiguration des Laufs; Prüfungen, die
      den SOC in Energie umrechnen, nehmen sie von hier statt einer festen Zahl
    """

    scenario: Scenario
    metrics: Metrics
    decisions: list[Decision]
    health: list[tuple[int, HealthReport]]
    samples: list[Sample]
    failures: list[str]
    sent: Mapping[str, datetime] = MappingProxyType({})
    usable_kwh: float = field(kw_only=True)


class SimTimer:
    """Uhr und Pausen des CoE-Adapters in simulierter Zeit.

    ``monotonic`` sind die Sekunden seit ``start`` nach der Uhr des Laufs. ``sleep`` wartet, bis
    ``wake`` nach dem Vorstellen der Uhr seine Zeit erreicht sieht; eine abgebrochene Pause
    vergisst ``wake``.
    """

    def __init__(self, clock: ManualClock, start: datetime) -> None:
        self._clock = clock
        self._start = start
        self._waiting: list[tuple[float, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    def monotonic(self) -> float:
        return (self._clock.now() - self._start).total_seconds()

    async def sleep(self, seconds: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiting, (self.monotonic() + seconds, next(self._order), future))
        await future

    def wake(self) -> None:
        """Beendet jede Pause, deren Zeit erreicht ist."""
        now = self.monotonic()
        while self._waiting and self._waiting[0][0] <= now:
            _, _, future = heapq.heappop(self._waiting)
            if not future.done():
                future.set_result(None)


class SimCoePort:
    """Der UDP-Port des CoE-Adapters im Prozess (Ruling R1): Gesendetes geht an die UVR im Modell
    (``AirConditioner.receive``); ``close`` trennt das C.M.I. vom Empfang des Adapters."""

    def __init__(self, unit: AirConditioner) -> None:
        self._unit = unit

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self._unit.receive(data, addr)

    def close(self) -> None:
        self._unit.disconnect()


async def _open_coe(
    unit: AirConditioner, on_datagram: Callable[[bytes, tuple[str, int]], None], port: int
) -> DatagramPort:
    """Die ``PortFactory`` des Laufs: verbindet das C.M.I. des Modells mit dem Empfang."""
    unit.connect(on_datagram)
    return SimCoePort(unit)


class _Discard:
    """Die Telemetrie des Laufs: Der Simulator schreibt keine Messpunkte."""

    def write(self, point: Point) -> None:
        del point


def sim_config(
    cfg: Config,
    data_dir: Path,
    *,
    victron: bool = False,
    control: Mapping[str, Any] = MappingProxyType({}),
    vehicles: Mapping[str, Mapping[str, Any]] = MappingProxyType({}),
    thermal: bool = False,
) -> Config:
    """Die Konfiguration des Laufs: ``data_dir`` als Ablage des core, jede EVCS und jedes
    Fahrzeug mit Tessie für den Live-Betrieb freigegeben, mit ``victron`` auch Victron, mit
    ``thermal`` auch jede Klimaanlage der Art ``coe_offset``.

    ``control`` überschreibt Werte von ``ControlConfig``, ``vehicles`` je Fahrzeug Werte von
    ``VehicleConfig`` (etwa ``charge_limit_sync``); ein ungültiger Wert oder ein unbekannter Name
    ist ein ``ValidationError``, ein unbekanntes Fahrzeug ein ``KeyError``.
    """
    changed = {
        key: VehicleConfig.model_validate(cfg.vehicles[key].model_dump() | dict(values))
        for key, values in vehicles.items()
    }
    wallboxes = {
        key: wallbox.model_copy(update={"live_allowed": True})
        if isinstance(wallbox, EvcsConfig)
        else wallbox
        for key, wallbox in cfg.wallboxes.items()
    }
    fleet = {
        key: vehicle.model_copy(update={"live_allowed": True})
        if vehicle.tessie is not None
        else vehicle
        for key, vehicle in (cfg.vehicles | changed).items()
    }
    core = cfg.core.model_copy(update={"data_dir": data_dir})
    changes: dict[str, Any] = {
        "wallboxes": wallboxes,
        "vehicles": fleet,
        "core": core,
        "control": ControlConfig.model_validate(cfg.control.model_dump() | dict(control)),
    }
    if victron:
        changes["victron"] = cfg.victron.model_copy(update={"live_allowed": True})
    if thermal:
        devices = {
            name: device.model_copy(update={"live_allowed": True})
            if isinstance(device, CoeOffsetConfig)
            else device
            for name, device in cfg.thermal.devices.items()
        }
        changes["thermal"] = cfg.thermal.model_copy(update={"devices": devices})
    return cfg.model_copy(update=changes)


def _intent(scenario: Scenario, cfg: Config) -> Intent:
    """Die Absicht des Szenarios; mit ``live`` sind EVCS und Fahrzeuge mit Tessie live, mit
    ``victron_live`` dazu Victron, mit ``thermal`` jede Klimaanlage der Art ``coe_offset``."""
    live: dict[str, ActuatorMode] = {}
    if scenario.live:
        live = {
            wallbox_actuator(key): ActuatorMode.LIVE
            for key, wallbox in cfg.wallboxes.items()
            if isinstance(wallbox, EvcsConfig)
        } | {
            vehicle_actuator(key): ActuatorMode.LIVE
            for key, vehicle in cfg.vehicles.items()
            if vehicle.tessie is not None
        }
    if scenario.victron_live:
        live[VICTRON] = ActuatorMode.LIVE
    if scenario.thermal:
        for name, device in cfg.thermal.devices.items():
            if isinstance(device, CoeOffsetConfig):
                live[thermal_actuator(name)] = ActuatorMode.LIVE
    if not live:
        return scenario.intent
    return scenario.intent.model_copy(update={"actuators": scenario.intent.actuators | live})


async def run_scenario(
    scenario: Scenario, cfg: Config, data_dir: Path, *, duration_s: int | None = None
) -> SimResult:
    """Lässt das Szenario laufen, ``duration_s`` Sekunden statt seiner Dauer, wenn angegeben.

    ``data_dir`` nimmt die Ablage des core auf (Absicht, Limits, Entscheidungslog). Eine Ausnahme
    im Zyklus bricht den Lauf ab; die Laufzeit fährt trotzdem herunter.
    """
    cfg = sim_config(
        cfg,
        data_dir,
        victron=scenario.victron_live,
        control=scenario.control,
        vehicles=scenario.vehicles,
        thermal=scenario.thermal,
    )
    seconds = scenario.duration_s if duration_s is None else duration_s
    clock = ManualClock(scenario.start)
    rng = random.Random(scenario.seed)  # noqa: S311 - Saat der Simulation
    model = PlantModel(cfg, rng, roundtrip_pct=scenario.roundtrip_pct)
    scenario.setup(model)
    requests: list[tuple[datetime, str]] = []

    async def tessie(request: httpx.Request) -> httpx.Response:
        requests.append((clock.now(), request.url.path))
        return await model.tessie(request)

    secrets = Secrets(
        internal_token=SecretStr(_INTERNAL_TOKEN), tessie_token=SecretStr(_TESSIE_TOKEN)
    )
    rt = CoreRuntime(
        cfg,
        secrets,
        clock=clock,
        safety_clock=clock,
        adapters=[],
        sink=_Discard(),
        serve_api=False,
        mqtt_write=model.mqtt_write,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(tessie)),
    )
    own = _own_values(rt)
    timer = SimTimer(clock, scenario.start) if scenario.thermal else None
    coe_tasks: list[asyncio.Task[None]] = []
    try:
        model.attach(rt.store)
        if timer is not None:
            coe_tasks.append(_start_coe(model, cfg, rt, timer))
        reply = rt.submit_intent(_intent(scenario, cfg))
        if reply.result == "rejected":
            raise ValueError(f"Absicht des Szenarios abgelehnt: {' '.join(reply.details)}")
        events = _by_second(scenario.events)
        samples: list[Sample] = []
        health: list[tuple[int, HealthReport]] = []
        for t in range(seconds):
            for action in events.get(t, ()):
                action(model, rt)
            now = clock.now()
            model.step(now)
            if timer is not None:
                timer.wake()
                for _ in range(SETTLE_ROUNDS):
                    await asyncio.sleep(0)
            model.publish(rt.store, now)
            rt.cycle_once()
            for _ in range(SETTLE_ROUNDS):
                await asyncio.sleep(0)
            model.resolve(now)
            sample = _sample(t, model, cfg, rt.intent(), now)
            if timer is not None:
                thermal = _thermal_sample(model.thermal, now, rt.regulation.health())
                sample = replace(sample, thermal=thermal)
            samples.append(sample)
            if t % HEALTH_EVERY_S == 0:
                health.append((t, rt.health()))
            clock.advance(1)
        decisions = _decisions(rt, scenario.start)
    finally:
        for task in coe_tasks:
            task.cancel()
        await asyncio.gather(*coe_tasks, return_exceptions=True)
        await rt.shutdown()
    sent = sent_times(decisions, requests, cfg)
    metrics = measure(samples, decisions, cfg, sent=sent, own=own)
    result = SimResult(
        scenario,
        metrics,
        decisions,
        health,
        samples,
        [],
        MappingProxyType(sent),
        usable_kwh=cfg.battery.usable_kwh,
    )
    return replace(result, failures=[*metrics.failures(cfg), *scenario.checks(result)])


def _start_coe(
    model: PlantModel, cfg: Config, rt: CoreRuntime, timer: SimTimer
) -> asyncio.Task[None]:
    """Startet den CoE-Adapter der Klimaanlage des Modells als Task, mit dem Port im Prozess.

    Er schreibt in den Zustandsspeicher des core und gehört zu dessen Adaptern, wie im Betrieb
    (``CoreRuntime.build_adapters``). Ohne Klimaanlage im Modell ist das ein ``ValueError``.
    """
    unit = model.thermal
    if unit is None:
        raise ValueError("Das Szenario braucht eine Klimaanlage der Art coe_offset.")
    device = cfg.thermal.devices[unit.name]
    if not isinstance(device, CoeOffsetConfig):
        raise ValueError(f"Die Klimaanlage „{unit.name}“ ist nicht der Art coe_offset.")
    adapter = CoeAdapter(
        unit.name,
        device,
        rt.store,
        partial(_open_coe, unit),
        clock=timer.monotonic,
        sleep=timer.sleep,
    )
    rt.adapters.append(adapter)
    return asyncio.create_task(adapter.run(), name=f"adapter:{adapter.name}")


def _own_values(rt: CoreRuntime) -> list[OwnValue | Forgotten]:
    """Hüllt ``note_verdict``, ``forget`` und ``idle`` von ``rt.regulation`` ein; die Liste füllt
    sich mit den eigenen Werten der Regelung und dem, was sie davon vergisst, in ihrer Reihenfolge.

    Eigen ist jedes Urteil über einen Befehl der Regelung, das ihren letzten Wert setzt: mit Wert
    oder ``UNCHANGED`` (Präzisierung 10). „Nicht gesendet“ (``not_sent``) setzt keinen, es löscht
    den Wert dieser Art; ``forget`` vergisst die Werte der genannten Aktoren, ``idle`` alle. Das
    Auffrischen eines Overrides (``Command.refresh`` beim Netz-Sollwert) setzt den Wert erneut,
    ergibt im Dry-Run aber keine Entscheidung: Es zählt nicht als Dry-Run (``OwnValue.dry_run``).
    Die Regelung bekommt jeden Aufruf unverändert.
    """
    own: list[OwnValue | Forgotten] = []
    regulation = rt.regulation
    note, forget, idle = regulation.note_verdict, regulation.forget, regulation.idle

    def noted(command: Command, verdict: Verdict | None, *, not_sent: bool = False) -> None:
        if not_sent:
            own.append(Forgotten(command.actuator, command.kind))
        elif verdict is not None and (verdict.sendable or verdict.outcome == Outcome.UNCHANGED):
            dry_run = verdict.sendable and verdict.dry_run and not command.refreshes
            own.append(OwnValue(command.actuator, command.kind, command.value, dry_run=dry_run))
        note(command, verdict, not_sent=not_sent)

    def forgot(actuators: Collection[str]) -> None:
        own.extend(Forgotten(actuator) for actuator in sorted(actuators))
        forget(actuators)

    def idled(snow: datetime) -> None:
        own.append(Forgotten())
        idle(snow)

    # Der Simulator beobachtet die Regelung von außen; die Methoden bleiben dieselben.
    regulation.note_verdict = noted  # type: ignore[method-assign]
    regulation.forget = forgot  # type: ignore[method-assign]
    regulation.idle = idled  # type: ignore[method-assign]
    return own


def _by_second(events: Sequence[tuple[int, Action]]) -> dict[int, list[Action]]:
    found: dict[int, list[Action]] = defaultdict(list)
    for second, action in events:
        found[second].append(action)
    return found


def _sample(t: int, model: PlantModel, cfg: Config, intent: Intent | None, now: datetime) -> Sample:
    """Die Probe nach Sekunde ``t``; der Modus je Auto laut Absicht, mit Boost bis ``now``, ob die
    Regelung seinen Ladepunkt live steuert (``_steered``) und sein Ladestand im Modell. Dazu Netz,
    PV, Haus und Override des Modells im Schritt.

    Hinter den Fahrzeugen der Konfiguration steht der Gast (``GUEST_CAR``), auch abgesteckt, dann
    ohne Strom. Sein Modus ist ``intent.guest.mode``, ohne Absicht PV; Boost kennt er nicht. Sein
    Mindeststrom ist der der EVCS, an der er steckt, sonst ``GUEST_MIN_A``.
    """
    amps, power = model.car_a, model.car_w
    cars: dict[str, CarSample] = {}
    for key, vehicle in cfg.vehicles.items():
        entry = (intent.vehicles.get(key) if intent is not None else None) or VehicleIntent()
        boost = entry.boost_until is not None and now < entry.boost_until
        plugged = model.cars[key].wallbox
        cars[key] = CarSample(
            charging=amps[key] > 0,
            current_a=amps[key],
            power_w=power[key],
            mode="boost" if boost else entry.mode.value,
            min_a=max(vehicle.min_a, _evcs_min_a(plugged, cfg)),
            complete=model.cars[key].complete,
            steered=_steered(key, plugged, cfg, intent),
            soc_pct=model.cars[key].soc_pct,
        )
    guest_mode = (intent.guest if intent is not None else GuestIntent()).mode
    plugged = model.cars[GUEST_CAR].wallbox
    cars[GUEST_CAR] = CarSample(
        charging=amps[GUEST_CAR] > 0,
        current_a=amps[GUEST_CAR],
        power_w=power[GUEST_CAR],
        mode=guest_mode.value,
        min_a=max(GUEST_MIN_A, _evcs_min_a(plugged, cfg)),
        complete=model.cars[GUEST_CAR].complete,
        steered=_steered(GUEST_CAR, plugged, cfg, intent),
        soc_pct=model.cars[GUEST_CAR].soc_pct,
    )
    return Sample(
        t=t,
        grid_a=dict(model.grid_a),
        battery_feed_a=dict(model.battery_feed_a),
        battery_w=model.battery_w,
        soc_pct=model.soc_pct,
        cars=cars,
        grid_w=model.grid_w,
        pv_w=model.pv_now_w,
        house_w=sum(model.house_w) + model.thermal_w,
        override_w=model.override_w,
        override_age_s=model.override_age_s,
    )


def _thermal_sample(
    unit: AirConditioner | None, now: datetime, regulation: Mapping[str, Any]
) -> ThermalSample | None:
    """Die Klimaanlage nach der Sekunde ``now``, mit dem Stand der Regelung (``regulation``,
    ``RegulationLink.health``) dieser Sekunde; ohne Klimaanlage ``None``."""
    if unit is None:
        return None
    received: list[float] = []
    for at, value in reversed(unit.received):
        if at != now:
            break
        received.append(value)
    received.reverse()
    entry = regulation.get("thermal", {}).get(unit.name, {})
    peak = regulation.get("peak", {})
    reduction = peak.get("reduction_kw")
    pressure = peak.get("state") == "exceeded" or (reduction is not None and reduction > 0)
    return ThermalSample(
        mode=unit.mode,
        power_w=unit.power_w,
        active_k=unit.active_k,
        received_k=tuple(received),
        silent=unit.silent,
        wish=entry.get("wish"),
        reason=entry.get("reason"),
        peak_pressure=pressure,
    )


def _evcs_min_a(wallbox: str | None, cfg: Config) -> int:
    """Der eigene Mindeststrom der EVCS ``wallbox``; 0 an einem Wall Connector und abgesteckt."""
    config = cfg.wallboxes[wallbox] if wallbox is not None else None
    return config.min_a if isinstance(config, EvcsConfig) else 0


def _steered(key: str, wallbox: str | None, cfg: Config, intent: Intent | None) -> bool:
    """Steuert die Regelung den Ladepunkt des Fahrzeugs ``key`` an ``wallbox`` live?

    Der Ladepunkt ist an einer EVCS deren Aktor, an einem Wall Connector das Fahrzeug selbst; live
    ist er mit der Betriebsart ``live`` in der Absicht. Ein Fahrzeug ohne Tessie hat keinen Aktor,
    die Absicht kann es also nicht live schalten; ein abgestecktes hat keinen Ladepunkt.
    """
    if wallbox is None or intent is None:
        return False
    at_evcs = isinstance(cfg.wallboxes[wallbox], EvcsConfig)
    actuator = wallbox_actuator(wallbox) if at_evcs else vehicle_actuator(key)
    return intent.actuators.get(actuator) is ActuatorMode.LIVE


def _decisions(rt: CoreRuntime, start: datetime) -> list[Decision]:
    """Alle Entscheidungen seit ``start``, aufsteigend."""
    found: list[Decision] = []
    since = start - timedelta(microseconds=1)
    while True:
        batch, more = rt.decisions(since, READ_LIMIT)
        found.extend(batch)
        if not more or not batch:
            return found
        since = batch[-1].ts


def sent_times(
    decisions: Sequence[Decision], requests: Sequence[tuple[datetime, str]], cfg: Config
) -> dict[str, datetime]:
    """Je gesendeter Entscheidung an ein Fahrzeug mit Tessie der Zeitpunkt, zu dem ihr Befehl
    Tessie erreichte.

    ``requests`` sind Zeit und Pfad jeder Anfrage an Tessie. Gesendet ist eine Entscheidung mit
    Ergebnis ``ok``, ``error`` oder ``timeout``. Ihr Befehl ist die letzte Anfrage mit seinem Pfad
    vor ihrem Zeitpunkt: Je Aktor und Art ist höchstens ein Befehl offen, und sein Ergebnis kommt
    frühestens eine Sekunde nach dem Senden.
    """
    prefixes = {
        vehicle_actuator(key): f"/{vehicle.tessie.vin}/command/"
        for key, vehicle in cfg.vehicles.items()
        if vehicle.tessie is not None
    }
    sent: dict[str, datetime] = {}
    for decision in decisions:
        prefix = prefixes.get(decision.actuator)
        if prefix is None or decision.result not in _SENT_RESULTS:
            continue
        path = prefix + _command(decision.kind, decision.applied)
        earlier = [at for at, found in requests if found == path and at < decision.ts]
        if earlier:
            sent[decision.id] = earlier[-1]
    return sent


def _command(kind: CommandKind, value: CommandValue) -> str:
    """Der Name des Tessie-Befehls wie im Tesla-Aktor."""
    if kind is CommandKind.CURRENT:
        return "set_charging_amps"
    if kind is CommandKind.CHARGE_LIMIT:
        return "set_charge_limit"
    return "start_charging" if value is True else "stop_charging"
