"""Planungsdienst (Plan 3b, Task 9): ein Lauf von den Eingangsdaten bis zum gespeicherten Plan.

Ein Lauf sammelt Preise, Prognosen, Monatsspitze und Akkuzustand, löst den Optimierer, leitet
daraus die Absichten ab, rechnet sie nach und prüft den Plan. Scheitert der Solver oder ist der
Plan unplausibel, gilt der Ersatzplan mit dem Grund aus „Texte“. Fehlen Eingangsdaten, entsteht
kein Plan, nur ein Status; der letzte Plan bleibt.

Die Schleife ``run`` (Task 10) plant beim Start, zur Minute 1 jeder Viertelstunde und auf
Auslöser (``triggers``): Fingerabdrücke von Preisen und Prognosen, SOC und Monatsspitze gegen den
Plan, geänderte Einstellungen und ``request``. Anfragen während eines Laufs fallen zu einem
weiteren Lauf zusammen.

Mit einer Klimaanlage der Art ``coe_offset`` und ihren Wärmemodellen (``thermal_model``, Plan 5c)
folgt auf jeden Plan der zweite Schritt, Vorheizen und Vorkühlen (``thermal_stage``), bevor er
gespeichert wird; ohne sie bleibt ``Plan.thermal`` ``None``.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Final

import structlog
from pydantic import ValidationError

from bindaems.app.audit import Source
from bindaems.app.control.intent import trusted
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.service import ForecastService
from bindaems.app.learning.driving_profile import VehicleProfile
from bindaems.app.learning.thermal_service import thermal_device
from bindaems.app.ledger.service import LedgerService
from bindaems.app.planning.battery_model import params_of, sim_slots, simulate
from bindaems.app.planning.builder import build_plan
from bindaems.app.planning.fallback import fallback_intents, fallback_vehicles, per_slot
from bindaems.app.planning.inputs import (
    PRICE_WINDOW,
    BatteryState,
    PlanInputs,
    PlanningUnavailable,
    PriceInput,
    SlotInput,
    assemble_inputs,
    car_floor_kw,
)
from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import OptimizerResult, ev_power_w
from bindaems.app.planning.plausibility import check_plan
from bindaems.app.planning.process import OUTER_TIMEOUT_S, Solver, solve_in_subprocess
from bindaems.app.planning.store import PlanStore
from bindaems.app.planning.thermal_stage import ThermalModels, apply_thermal, failed
from bindaems.app.planning.triggers import (
    THERMAL_REPLAN_MIN_S,
    VEHICLE_REPLAN_MIN_S,
    WATCH_S,
    ExpectedCars,
    Fingerprint,
    ThermalClass,
    changed,
    expected_cars,
    health_thermal,
    next_tick,
    peak_rose,
    settings_relevant,
    soc_deviates,
    thermal_class,
    vehicles_fingerprint,
    vehicles_soft,
)
from bindaems.app.planning.vehicles import PlanVehicleInput, expected_vehicles, planned_vehicles
from bindaems.app.prices.service import PriceService
from bindaems.app.settings.service import (
    SettingsService,
    SettingsVersion,
    VersionConflictError,
)
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import CoeOffsetConfig, Config
from bindaems.shared.control import ChargeMode, Intent
from bindaems.shared.plan import Plan, PlanTrigger
from bindaems.shared.settings import ThermalPlanSettings
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ensure_utc, slot_start

log = structlog.get_logger(__name__)

COMPONENT: Final = "planning"
FINGERPRINT_WINDOW: Final = timedelta(hours=48)


@dataclass(frozen=True)
class PlanningStatus:
    """Zustand des Dienstes: Lauf aktiv, Grund ohne Plan, Fehler des letzten Laufs."""

    running: bool
    unavailable: str | None
    last_error: str | None


def _failure_reason(inputs: PlanInputs, result: OptimizerResult) -> str | None:
    """Grund des Ersatzplans aus dem Ergebnis des Solvers; ``None`` bei einer Lösung."""
    if result.status == "timeout":
        return f"Solver nach {OUTER_TIMEOUT_S:.0f} s abgebrochen"
    if result.status == "infeasible":
        return f"Solver ohne Lösung ({result.status})"
    if result.status == "error":
        return f"Solver fehlgeschlagen: {result.message or 'unbekannter Fehler'}"
    if result.status == "time_limit" and not result.flows:
        return "Zeitlimit ohne Lösung"
    if len(result.flows) != len(inputs.slots):
        return (
            f"Solver fehlgeschlagen: Ergebnis mit {len(result.flows)} statt "
            f"{len(inputs.slots)} Slots"
        )
    return None


def plan_once(inputs: PlanInputs, result: OptimizerResult) -> Plan:
    """Plan aus einem Ergebnis des Optimierers (rein; nutzen auch die Szenarien).

    Mit einer Lösung: Absichten, Nachrechnung und Plausibilität; ist der Plan plausibel, gilt
    er. Sonst der Ersatzplan mit dem Grund aus „Texte“, bei einem unplausiblen Plan dem ersten
    Befund und allen Befunden als Auffälligkeiten. Die geplanten Autos laden im Plan des
    Optimierers mit seiner Ladeleistung und Fehlmenge, im Ersatzplan nach ``fallback_vehicles``.
    Der Ersatzplan bleibt ohne erwartete Autos (Plan 5b, Entscheidung 11): Er rechnet mit den
    Eingangsdaten ohne sie.
    """
    reason = _failure_reason(inputs, result)
    findings: list[str] = []
    if reason is None:
        ev_w = ev_power_w(result)
        intents = derive_intents(inputs, result)
        params = params_of(inputs)
        soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
        steps = simulate(soc0_kwh, sim_slots(inputs, ev_w), intents, params)
        findings = check_plan(inputs, result, intents, steps)
        if not findings:
            return build_plan(
                inputs,
                intents,
                source="optimizer",
                solver_status=result.status,
                optimizer_peaks_kw=result.peaks_kw,
                ev_w=ev_w,
                shortfall_kwh=result.shortfall_kwh,
            )
        reason = f"Plan unplausibel: {findings[0]}"
    plugged = [car for car in inputs.vehicles if not car.expected]
    inputs = inputs.model_copy(update={"vehicles": plugged})
    ev_w = per_slot(fallback_vehicles(inputs), len(inputs.slots))
    return build_plan(
        inputs,
        fallback_intents(inputs, ev_w),
        source="fallback",
        solver_status=result.status,
        fallback_reason=reason,
        issues=findings,
        ev_w=ev_w,
    )


def _number(value: object) -> float | None:
    """Endliche Zahl aus einem Signalwert; sonst ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(LOCAL_TZ).strftime("%H:%M")


class PlanningService:
    def __init__(
        self,
        cfg: Config,
        settings: SettingsService,
        prices: PriceService,
        forecast: ForecastService,
        load_forecast: LoadForecastService,
        ledger: LedgerService,
        live: LiveState,
        store: PlanStore,
        clock: Clock,
        *,
        solver: Solver = solve_in_subprocess,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        support_pct: Callable[[], float] | None = None,
        intent: Callable[[], Intent | None] | None = None,
        driving_profiles: Callable[[], Mapping[str, VehicleProfile]] | None = None,
        thermal_model: ThermalModels | None = None,
    ) -> None:
        self._cfg = cfg
        self._settings = settings
        self._prices = prices
        self._forecast = forecast
        self._load_forecast = load_forecast
        self._ledger = ledger
        self._live = live
        self._store = store
        self._clock = clock
        self._solver = solver
        self._sleep = sleep
        # Unterstützungsschwelle der Absicht (Task 10); ohne Quelle gilt die Konfiguration
        self._support_pct = support_pct or (lambda: cfg.control.support_soc_pct)
        # zuletzt gesehene Absicht (``IntentService.cached``) für die geplanten Autos (Plan 4b)
        self._intent = intent or (lambda: None)
        # Profile des Fahrverhaltens (Plan 5b) für die erwarteten Autos; ohne Quelle gibt es keine
        self._driving_profiles = driving_profiles
        self._lock = asyncio.Lock()  # ein Lauf nach dem anderen
        self._running = False
        self._unavailable: str | None = None
        self._last_error: str | None = None
        # wartender Auslöser; ``request`` kommt auch aus Worker-Threads (Einstellungen, API)
        self._pending: PlanTrigger | None = None
        self._pending_lock = threading.Lock()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._seen_settings = settings.current().settings
        settings.on_change(self._on_settings)
        self._plan = store.latest()
        self._plan_listeners: list[Callable[[Plan], None]] = []
        # Der letzte Lauf sah ein Auto in „Geplant“ nicht, weil Health die Absicht noch nicht
        # bestätigt hatte (Ruling R23); ``_watch`` fordert dann „vehicles“ an
        self._cars_unseen = False
        # Eingangsdaten des letzten Plans und die Menge erwarteter Autos, für die ``_watch``
        # zuletzt „vehicles“ angefordert hat (Plan 5b, Präzisierung 10)
        self._inputs: PlanInputs | None = None
        self._expected_requested: ExpectedCars | None = None
        # Ladelimit und Quelle des Ladestands (``vehicles_soft``), mit denen zuletzt „vehicles“
        # angefordert wurde, und wann; ``None`` bis zur ersten Prüfung bzw. Anforderung
        self._limits_seen: tuple[Any, ...] | None = None
        self._limits_at: datetime | None = None
        # Vorheizen und Vorkühlen (Plan 5c): nur mit Wärmemodellen und einer Klimaanlage der Art
        # ``coe_offset``. Gemerkt werden die Betriebsart (zusammengefasst), mit der der letzte
        # Plan rechnete, ein neues Wärmemodell seitdem und die letzte Anforderung „thermal“.
        device = thermal_device(cfg) if thermal_model is not None else None
        self._thermal_device = device
        self._thermal_models = thermal_model if device is not None else None
        self._thermal_seen: ThermalClass | None = None
        self._thermal_model_changed = False
        self._thermal_at: datetime | None = None
        # Einstellungen der Verschiebung aus dem Schnappschuss des Laufs (``collect``), damit sie
        # zu ``settings_version`` des Plans passen; harte Grenze des Versatzes der Klimaanlage
        self._thermal_settings: ThermalPlanSettings | None = None
        unit = cfg.thermal.devices.get(device) if device is not None else None
        self._thermal_max_k = unit.max_offset_k if isinstance(unit, CoeOffsetConfig) else 0.0

    def current(self) -> Plan | None:
        """Der zuletzt erzeugte Plan, auch wenn er abgelaufen ist."""
        return self._plan

    def on_plan(self, listener: Callable[[Plan], None]) -> None:
        """Ruft ``listener`` mit jedem neuen Plan, sobald er gespeichert und aktuell ist (Task 10,
        Übergabe an den core); Fehler des Zuhörers kommen nur ins Log."""
        self._plan_listeners.append(listener)

    def status(self) -> PlanningStatus:
        return PlanningStatus(self._running, self._unavailable, self._last_error)

    def thermal_model_changed(self) -> None:
        """Rückruf nach einem neuen Wärmemodell (``ThermalModelService.on_change``): Die nächste
        Prüfung plant mit ``thermal`` neu, höchstens alle 5 min. Nur aus der Ereignisschleife."""
        self._thermal_model_changed = True

    def pending(self) -> PlanTrigger | None:
        """Der Auslöser, der auf den nächsten Lauf wartet."""
        with self._pending_lock:
            return self._pending

    def request(self, trigger: PlanTrigger) -> None:
        """Fordert einen Lauf an (threadsicher). Ein wartender Auslöser bleibt, nur ``manual``
        ersetzt ihn; Anfragen während eines Laufs ergeben so genau einen weiteren Lauf."""
        with self._pending_lock:
            if self._pending is None or trigger == "manual":
                self._pending = trigger
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # ``run`` sieht den Auslöser beim Start
        if threading.get_ident() == self._loop_thread:
            self._wake.set()
        else:
            with contextlib.suppress(RuntimeError):  # Schleife inzwischen geschlossen
                loop.call_soon_threadsafe(self._wake.set)

    def _take(self) -> PlanTrigger | None:
        with self._pending_lock:
            trigger, self._pending = self._pending, None
        return trigger

    def _on_settings(self, version: SettingsVersion) -> None:
        with self._pending_lock:
            old, self._seen_settings = self._seen_settings, version.settings
        if settings_relevant(old, version.settings):
            self.request("settings")

    def set_grid_charge(self, on: bool, *, actor: str, source: Source) -> SettingsVersion:
        """Schalter „Netzladen erlaubt“ als neue Einstellungsversion (blockiert: Datenbank).

        Kommentar „Netzladen ein“ bzw. „Netzladen aus“. Hat der Schalter schon den Wert, entsteht
        keine neue Version. Bei einem Versionskonflikt (UI und HA schreiben gleichzeitig) wird
        einmal auf der neuen Version wiederholt; scheitert auch das, geht der Fehler weiter.
        """
        for attempt in (1, 2):
            current = self._settings.current()
            if current.settings.planning.grid_charge_allowed == on:
                return current
            planning = current.settings.planning.model_copy(update={"grid_charge_allowed": on})
            settings = current.settings.model_copy(update={"planning": planning})
            try:
                return self._settings.update(
                    settings,
                    base_version=current.version,
                    actor=actor,
                    source=source,
                    comment="Netzladen ein" if on else "Netzladen aus",
                )
            except VersionConflictError:
                if attempt == 2:
                    raise
        raise AssertionError("unerreichbar")  # pragma: no cover - die Schleife kehrt zurück

    def set_thermal_plan(self, on: bool, *, actor: str, source: Source) -> SettingsVersion:
        """Schalter „Vorheizen und Vorkühlen“ (``learning.thermal.enabled``) als neue
        Einstellungsversion (blockiert: Datenbank).

        Kommentar „Vorheizen und Vorkühlen ein“ bzw. „… aus“; die Einstellungen sind relevant für
        die Planung und lösen die Neuplanung aus. Wiederholung bei Versionskonflikt wie bei
        ``set_grid_charge``.
        """
        for attempt in (1, 2):
            current = self._settings.current()
            thermal = current.settings.learning.thermal
            if thermal.enabled == on:
                return current
            learning = current.settings.learning.model_copy(
                update={"thermal": thermal.model_copy(update={"enabled": on})}
            )
            try:
                return self._settings.update(
                    current.settings.model_copy(update={"learning": learning}),
                    base_version=current.version,
                    actor=actor,
                    source=source,
                    comment="Vorheizen und Vorkühlen ein" if on else "Vorheizen und Vorkühlen aus",
                )
            except VersionConflictError:
                if attempt == 2:
                    raise
        raise AssertionError("unerreichbar")  # pragma: no cover - die Schleife kehrt zurück

    async def run(self) -> None:
        """Plant beim Start, zur Viertelstunde und auf Auslöser; prüft alle ``WATCH_S`` s.

        Ein Fehler in einem Durchlauf wird protokolliert, die Schleife läuft weiter; ein Abbruch
        (``CancelledError``) beendet sie.
        """
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.get_ident()
        self.request("start")
        fingerprint: Fingerprint | None = None
        tick = next_tick(self._clock.now())
        while True:
            now = self._clock.now()
            if now >= tick:
                self.request("slot")
                tick = next_tick(now)
            try:
                fingerprint = await self._watch(fingerprint, now)
            except Exception:
                log.exception("Auslöser der Planung nicht prüfbar")
            # Erst hier zurücksetzen: Die Anfragen von Viertelstunde und ``_watch`` nimmt
            # ``_take`` mit; eine Anfrage während des Laufs weckt die Schleife danach sofort.
            self._wake.clear()
            trigger = self._take()
            if trigger is not None:
                try:
                    await self.replan(trigger)
                except Exception:
                    log.exception("Planungslauf fehlgeschlagen", trigger=trigger)
            await self._wait(tick)

    async def _watch(self, old: Fingerprint | None, now: datetime) -> Fingerprint:
        """Prüft Fingerabdrücke (auch der geplanten Autos), SOC und Monatsspitze und fordert die
        Auslöser an."""
        last_price, primary, peak_kw = await asyncio.to_thread(self._observe, now)
        pv = self._forecast.latest()
        load = self._load_forecast.latest()
        health = self._live.health
        intent = self._intent_for(health)
        vehicles = vehicles_fingerprint(health, intent)
        if old is not None and (intent is None or not trusted(intent.version, health)):
            # Health fehlt oder regelt noch nicht mit der Absicht (Neustart des core): das ist
            # keine Änderung der Autos; der Planer sähe sie dann nur nicht
            vehicles = old.vehicles
        new = Fingerprint(
            last_price=last_price,
            primary=primary,
            pv_issued=pv.issued_at if pv is not None else None,
            load_issued=load.issued_at if load is not None else None,
            vehicles=vehicles,
            support_pct=self._support_pct(),
        )
        triggers = changed(old, new)
        for trigger in triggers:
            self.request(trigger)
        vehicles_requested = "vehicles" in triggers
        if self._cars_unseen and intent is not None and trusted(intent.version, health):
            # Der letzte Plan entstand ohne die Autos in „Geplant“; Health bestätigt die Absicht
            # jetzt, auch wenn sich der Stand der Autos nicht geändert hat (Ruling R23)
            self._cars_unseen = False
            self.request("vehicles")
            vehicles_requested = True
        if self._expected_changed(health, intent):
            self.request("vehicles")
            vehicles_requested = True
        if self._limits_due(vehicles, now, planned=vehicles_requested):
            self.request("vehicles")
        if self._thermal_due(now):
            self.request("thermal")
        plan = self._plan
        if plan is not None:
            # Ein Plan wegen des SOC löst im selben Slot nicht erneut aus: Die lineare Erwartung
            # ab Slotbeginn passt nicht zu einem Plan, der spät im Slot entstand.
            replanned = plan.trigger == "soc" and slot_start(plan.created_at) == slot_start(now)
            soc_pct = _number(self._live.value("battery.soc_pct"))
            if not replanned and soc_deviates(plan, now, soc_pct):
                self.request("soc")
            if plan.valid_at(now) and peak_rose(plan, peak_kw):
                self.request("month_peak")
        return new

    def _limits_due(self, vehicles: tuple[Any, ...], now: datetime, *, planned: bool) -> bool:
        """Ist ein Lauf wegen Ladelimit oder Quelle des Ladestands fällig? Höchstens alle
        ``VEHICLE_REPLAN_MIN_S`` s. Verglichen wird mit dem Stand der letzten Anforderung
        „vehicles“, nicht mit der Prüfung davor: Eine gedrosselte Änderung geht so nicht
        verloren, sondern löst aus, sobald die Zeit um ist und der Stand noch abweicht. Fordert
        die Prüfung ohnehin „vehicles“ an (``planned``), rechnet der Lauf mit dem Stand von
        jetzt."""
        soft = vehicles_soft(vehicles)
        if planned:
            self._limits_seen, self._limits_at = soft, now
            return False
        if self._limits_seen is None:
            self._limits_seen = soft  # erste Prüfung: der Lauf „start“ kennt den Stand
            return False
        if soft == self._limits_seen:
            return False
        if (
            self._limits_at is not None
            and (now - self._limits_at).total_seconds() < VEHICLE_REPLAN_MIN_S
        ):
            return False
        self._limits_seen, self._limits_at = soft, now
        return True

    def _thermal_health(self) -> Mapping[str, Any] | None:
        """Health für die Klimaanlage: nur mit Verbindung zum core (sonst unbekannt)."""
        return self._live.health if self._live.connected else None

    def _thermal_due(self, now: datetime) -> bool:
        """Ist ein Lauf „thermal“ fällig (Plan 5c, Präzisierung 8)? Ja, wenn die Betriebsart aus
        Health (``thermal_class``) eine andere ist als die, mit der der letzte Plan rechnete,
        oder seitdem ein neues Wärmemodell kam; höchstens alle ``THERMAL_REPLAN_MIN_S`` s. Eine
        gedrosselte Änderung bleibt fällig, bis die Zeit um ist. Ohne Health gibt es keinen
        Vergleich, vor dem ersten Plan auch nicht."""
        device = self._thermal_device
        if device is None or self._thermal_models is None:
            return False
        current = thermal_class(self._thermal_health(), device)
        mode_changed = (
            current is not None and self._thermal_seen is not None and current != self._thermal_seen
        )
        if not (mode_changed or self._thermal_model_changed):
            return False
        if (
            self._thermal_at is not None
            and (now - self._thermal_at).total_seconds() < THERMAL_REPLAN_MIN_S
        ):
            return False
        self._thermal_at = now
        return True

    def _expected_changed(self, health: Mapping[str, Any] | None, intent: Intent | None) -> bool:
        """Hat sich die Menge der erwarteten Autos gegenüber den Eingangsdaten des letzten Plans
        geändert (Plan 5b, Präzisierung 10)? Gerechnet wird wie im letzten Lauf, mit seinen Slots
        und seiner Zeit, aber mit Health, Absicht und Profilen von jetzt: So ändert der Lauf der
        Zeit nichts (dafür plant die Viertelstunde neu), nur eine neue Ankunft, Abfahrt oder ein
        neues Ziel und ein Auto, das ansteckt. Dieselbe Menge meldet es nur einmal, auch wenn der
        Lauf danach keinen Plan ergibt. Solange Health der Absicht nicht traut (Neustart des core,
        neue Absicht), prüft es nicht: Ohne Health sähe es kein erwartetes Auto, und der Auslöser
        flatterte wie beim Fingerabdruck der Autos."""
        inputs = self._inputs
        if inputs is None or self._driving_profiles is None:
            return False
        if intent is None or not trusted(intent.version, health):
            return False
        cars = self._vehicles(health, intent, inputs.created_at, inputs.slots)
        current = expected_cars(cars)
        if current == expected_cars(inputs.vehicles):
            self._expected_requested = None
            return False
        if current == self._expected_requested:
            return False
        self._expected_requested = current
        return True

    def _observe(self, now: datetime) -> tuple[datetime | None, int, float | None]:
        """Letzter Preis-Slot und ``primary``-Slots in 48 h, Monatsspitze (blockiert)."""
        start = slot_start(now)
        views = self._prices.slots(start, start + FINGERPRINT_WINDOW)
        last_price = max((view.start for view in views), default=None)
        primary = sum(1 for view in views if view.origin == "primary")
        peak = self._ledger.month_peak(now.astimezone(LOCAL_TZ).date())
        return last_price, primary, peak.kw if peak is not None else None

    async def _wait(self, tick: datetime) -> None:
        """Wartet auf eine Anfrage, höchstens ``WATCH_S`` s und nicht über die Viertelstunde."""
        if self._wake.is_set():
            return
        timeout = max(0.0, min(WATCH_S, (tick - self._clock.now()).total_seconds()))
        waiter = asyncio.ensure_future(self._wake.wait())
        sleeper = asyncio.ensure_future(self._sleep(timeout))
        try:
            await asyncio.wait({waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, sleeper):
                task.cancel()
            await asyncio.gather(waiter, sleeper, return_exceptions=True)

    def _battery(self) -> BatteryState | None:
        soc_pct = _number(self._live.value("battery.soc_pct"))
        if soc_pct is None:
            return None
        voltage_v = _number(self._live.value("battery.voltage_v"))

        def bms_w(signal: str) -> float | None:
            current_a = _number(self._live.value(signal))
            if current_a is None or voltage_v is None:
                return None
            return max(0.0, current_a * voltage_v)

        battery = self._cfg.battery
        grid = self._cfg.grid
        if grid.max_import_kw is not None:
            grid_max_import_w = grid.max_import_kw * 1000
        else:
            grid_max_import_w = (grid.fuse_a - grid.fuse_margin_a) * grid.voltage_nominal_v * 3
        return BatteryState(
            soc_pct=soc_pct,
            usable_kwh=battery.usable_kwh,
            reserve_pct=battery.reserve_soc_pct,
            soc_max_pct=battery.soc_max_pct,
            max_charge_w=battery.max_charge_w,
            max_discharge_w=battery.max_discharge_w,
            limit_now_charge_w=bms_w("battery.ccl_a"),
            limit_now_discharge_w=bms_w("battery.dcl_a"),
            max_grid_charge_setpoint_w=self._cfg.victron.max_grid_charge_setpoint_w,
            grid_max_import_w=grid_max_import_w,
        )

    def _read(self, now: datetime) -> tuple[list[PriceInput], float | None]:
        """Preise des Planungsfensters und Monatsspitze (blockiert: Datenbank)."""
        start = slot_start(now)
        prices = [
            PriceInput(
                start=item.slot_start,
                import_ct=item.import_gross_ct,
                feed_in_ct=item.feed_in_ct,
                origin=item.origin,
            )
            for item in self._prices.planning(start, start + PRICE_WINDOW, now)
        ]
        peak = self._ledger.month_peak(now.astimezone(LOCAL_TZ).date())
        return prices, peak.kw if peak is not None else None

    def _intent_for(self, health: Mapping[str, Any] | None) -> Intent | None:
        """Die Absicht, gegen die Health geprüft wird: von der zuletzt gesehenen und der, die
        Health selbst als gültige nennt (wie ``health_trusted``), die mit der höheren Version;
        ``None`` ohne beide."""
        seen = self._intent()
        raw = health.get("intent") if health is not None else None
        shown: Intent | None = None
        if isinstance(raw, dict):
            with contextlib.suppress(ValidationError):
                shown = Intent.model_validate(raw)
        if seen is None or (shown is not None and shown.version > seen.version):
            return shown
        return seen

    async def collect(self, trigger: PlanTrigger) -> PlanInputs:
        """Eingangsdaten eines Laufs; wirft ``PlanningUnavailable``.

        Die geplanten Autos kommen aus dem Health des core und der Absicht (``planned_vehicles``),
        danach die erwarteten aus der Anwesenheitsprognose (``expected_vehicles``, nur mit
        ``driving_profiles``), die PV-Prognose P25 für Regel R2 aus der PV-Prognose. Hat Health
        die Absicht noch nicht bestätigt und steht darin ein Auto in „Geplant“, plant der Lauf
        ohne die Autos; der Dienst merkt sich das und plant neu, sobald Health sie bestätigt
        (``_watch``, Ruling R23).
        """
        now = self._clock.now()
        prices, month_peak_kw = await asyncio.to_thread(self._read, now)
        pv = self._forecast.latest()
        load = self._load_forecast.latest()
        current = self._settings.current()
        self._thermal_settings = current.settings.learning.thermal
        health = self._live.health
        intent = self._intent_for(health)
        self._cars_unseen = (
            intent is not None
            and any(entry.mode is ChargeMode.PLANNED for entry in intent.vehicles.values())
            and not trusted(intent.version, health)
        )
        return assemble_inputs(
            now=now,
            trigger=trigger,
            prices=prices,
            pv_w=pv.p50_w if pv is not None else None,
            load_w=load.p50_w if load is not None else None,
            battery=self._battery(),
            planning=current.settings.planning,
            tariff=current.settings.tariff,
            month_peak_kw=month_peak_kw,
            previous=self._plan,
            settings_version=current.version,
            support_soc_pct=self._support_pct(),
            peak_target_kw=self._cfg.control.peak_target_kw,
            car_floor_kw=car_floor_kw(self._cfg),
            pv_p25_w=pv.p25_w if pv is not None else None,
            vehicles=lambda slots: self._vehicles(health, intent, now, slots),
        )

    def _vehicles(
        self,
        health: Mapping[str, Any] | None,
        intent: Intent | None,
        now: datetime,
        slots: Sequence[SlotInput],
    ) -> list[PlanVehicleInput]:
        """Die angesteckten geplanten Autos, danach die erwarteten (Plan 5b, Entscheidung 11);
        ohne Profile des Fahrverhaltens gibt es keine erwarteten."""
        cars = planned_vehicles(self._cfg, health, intent, now=now, slots=slots)
        if self._driving_profiles is None:
            return cars
        expected = expected_vehicles(
            self._cfg, intent, health, self._driving_profiles(), slots, now, plugged=cars
        )
        return [*cars, *expected]

    async def replan(self, trigger: PlanTrigger) -> Plan | None:
        """Ein Planungslauf; ``None``, wenn Eingangsdaten fehlen (der letzte Plan bleibt).

        Eine Ausnahme des Solvers ergibt den Ersatzplan; andere Fehler (etwa beim Speichern)
        stehen in ``status().last_error`` und gehen an den Aufrufer.
        """
        async with self._lock:
            self._running = True
            try:
                plan = await self._run(trigger)
            except Exception as exc:
                self._last_error = str(exc) or type(exc).__name__
                raise  # der Aufrufer (Job der app) protokolliert
            finally:
                self._running = False
            self._last_error = None
            return plan

    async def _run(self, trigger: PlanTrigger) -> Plan | None:
        try:
            inputs = await self.collect(trigger)
        except PlanningUnavailable as exc:
            if self._unavailable != exc.reason:
                log.info("Kein Plan", trigger=trigger, reason=exc.reason)
            self._unavailable = exc.reason
            return None
        self._unavailable = None  # Eingangsdaten vollständig, auch wenn danach etwas scheitert
        result = await self._solve(inputs)
        plan = await self._with_thermal(plan_once(inputs, result), inputs)
        saved = await asyncio.to_thread(self._store.save, plan, inputs)
        self._plan = saved
        self._inputs = inputs
        log.info(
            "Plan erstellt",
            trigger=trigger,
            source=saved.source,
            solver_status=saved.solver_status,
            solve_ms=result.solve_ms,
            reason=saved.fallback_reason,
        )
        for listener in list(self._plan_listeners):
            try:
                listener(saved)
            except Exception:  # ein Zuhörer darf den Lauf nicht scheitern lassen
                log.exception("Reaktion auf den neuen Plan fehlgeschlagen")
        return saved

    async def _with_thermal(self, plan: Plan, inputs: PlanInputs) -> Plan:
        """Zweiter Planungsschritt: Vorheizen und Vorkühlen (``thermal_stage``), im Thread. Ohne
        Wärmemodelle oder Klimaanlage der Plan unverändert (``thermal`` ``None``); ein
        unerwarteter Fehler gibt den Plan ohne Verschiebung (Grund ``solver``)."""
        device, models = self._thermal_device, self._thermal_models
        if device is None or models is None:
            return plan
        health = self._thermal_health()
        entry = health_thermal(health, device)
        # Dieser Plan kennt Betriebsart und Wärmemodell von jetzt
        self._thermal_seen = thermal_class(health, device) or "rest"
        self._thermal_model_changed = False
        settings = self._thermal_settings  # die des Laufs (``collect``)
        try:
            if settings is None:
                settings = self._settings.current().settings.learning.thermal
            temperatures = {
                ensure_utc(slot): t for slot, t in self._forecast.temperatures().items()
            }
            return await asyncio.to_thread(
                partial(
                    apply_thermal,
                    plan,
                    device=device,
                    max_offset_k=self._thermal_max_k,
                    entry=entry,
                    models=models,
                    temperatures=temperatures,
                    settings=settings,
                    end_value_ct=inputs.end_value_ct,
                )
            )
        except Exception:  # die Verschiebung darf den Plan nie verhindern
            log.exception("Vorheizen und Vorkühlen nicht berechnet")
            enabled = settings is not None and settings.enabled
            return failed(plan, device=device, entry=entry, enabled=enabled)

    async def _solve(self, inputs: PlanInputs) -> OptimizerResult:
        try:
            return await self._solver(inputs)
        except Exception as exc:  # der Ersatzplan fängt jeden Fehler des Solvers auf
            log.warning("Solver fehlgeschlagen", error=str(exc))
            return OptimizerResult(
                status="error",
                flows=(),
                peaks_kw={},
                objective_ct=None,
                solve_ms=0,
                message=str(exc) or type(exc).__name__,
            )

    def component_status(self) -> ComponentStatus:
        if self._unavailable is not None:
            return ComponentStatus(COMPONENT, False, f"Kein Plan: {self._unavailable}")
        plan = self._plan
        if plan is None:
            return ComponentStatus(COMPONENT, False, "Noch kein Plan")
        if not plan.valid_at(self._clock.now()):
            return ComponentStatus(
                COMPONENT, False, f"Plan abgelaufen seit {_hhmm(plan.valid_until)} Uhr"
            )
        if plan.source == "fallback":
            return ComponentStatus(
                COMPONENT,
                False,
                f"Ersatzplan von {_hhmm(plan.created_at)} Uhr: {plan.fallback_reason}",
            )
        return ComponentStatus(COMPONENT, True, f"Plan von {_hhmm(plan.created_at)} Uhr")
