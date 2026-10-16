"""Planungsdienst (Plan 3b, Task 9): ein Lauf von den Eingangsdaten bis zum gespeicherten Plan.

Ein Lauf sammelt Preise, Prognosen, Monatsspitze und Akkuzustand, löst den Optimierer, leitet
daraus die Absichten ab, rechnet sie nach und prüft den Plan. Scheitert der Solver oder ist der
Plan unplausibel, gilt der Ersatzplan mit dem Grund aus „Texte“. Fehlen Eingangsdaten, entsteht
kein Plan, nur ein Status; der letzte Plan bleibt.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import structlog

from bindaems.app.core_link import LiveState
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.service import ForecastService
from bindaems.app.ledger.service import LedgerService
from bindaems.app.planning.battery_model import params_of, sim_slots, simulate
from bindaems.app.planning.builder import build_plan
from bindaems.app.planning.fallback import fallback_intents
from bindaems.app.planning.inputs import (
    PRICE_WINDOW,
    BatteryState,
    PlanInputs,
    PlanningUnavailable,
    PriceInput,
    assemble_inputs,
)
from bindaems.app.planning.intents import derive_intents
from bindaems.app.planning.optimizer import OptimizerResult
from bindaems.app.planning.plausibility import check_plan
from bindaems.app.planning.process import OUTER_TIMEOUT_S, Solver, solve_in_subprocess
from bindaems.app.planning.store import PlanStore
from bindaems.app.prices.service import PriceService
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config
from bindaems.shared.plan import Plan, PlanTrigger
from bindaems.shared.timeutil import LOCAL_TZ, Clock, slot_start

log = structlog.get_logger(__name__)

COMPONENT: Final = "planning"


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
    Befund und allen Befunden als Auffälligkeiten.
    """
    reason = _failure_reason(inputs, result)
    findings: list[str] = []
    if reason is None:
        intents = derive_intents(inputs, result)
        params = params_of(inputs)
        soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
        steps = simulate(soc0_kwh, sim_slots(inputs), intents, params)
        findings = check_plan(inputs, result, intents, steps)
        if not findings:
            return build_plan(
                inputs,
                intents,
                source="optimizer",
                solver_status=result.status,
                optimizer_peaks_kw=result.peaks_kw,
            )
        reason = f"Plan unplausibel: {findings[0]}"
    return build_plan(
        inputs,
        fallback_intents(inputs),
        source="fallback",
        solver_status=result.status,
        fallback_reason=reason,
        issues=findings,
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
        self._lock = asyncio.Lock()  # ein Lauf nach dem anderen
        self._running = False
        self._unavailable: str | None = None
        self._last_error: str | None = None
        self._plan = store.latest()

    def current(self) -> Plan | None:
        """Der zuletzt erzeugte Plan, auch wenn er abgelaufen ist."""
        return self._plan

    def status(self) -> PlanningStatus:
        return PlanningStatus(self._running, self._unavailable, self._last_error)

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

    async def collect(self, trigger: PlanTrigger) -> PlanInputs:
        """Eingangsdaten eines Laufs; wirft ``PlanningUnavailable``."""
        now = self._clock.now()
        prices, month_peak_kw = await asyncio.to_thread(self._read, now)
        pv = self._forecast.latest()
        load = self._load_forecast.latest()
        current = self._settings.current()
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
        )

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
        result = await self._solve(inputs)
        plan = plan_once(inputs, result)
        saved = await asyncio.to_thread(self._store.save, plan, inputs)
        self._plan, self._unavailable = saved, None
        log.info(
            "Plan erstellt",
            trigger=trigger,
            source=saved.source,
            solver_status=saved.solver_status,
            solve_ms=result.solve_ms,
            reason=saved.fallback_reason,
        )
        return saved

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
