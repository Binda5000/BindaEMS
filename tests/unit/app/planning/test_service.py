"""Tests des Planungslaufs: Eingangsdaten, Lösen, Plausibilität, Ersatzplan, Speicherung und
Status (Plan 3b, Task 9); Auslöser und Schleife des Dienstes (Task 10)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from structlog.testing import capture_logs
from tests.app_helpers import ingest, price_row
from tests.plan_helpers import (
    blocking_solver,
    flows_of,
    live_signals,
    month,
    planned_car,
    planned_health,
    resimulate,
    returns,
    scenario,
    set_planning,
    small_battery,
    until,
    vehicle_health,
)

from bindaems.app.learning.driving_profile import VehicleProfile, WeekdayProfile
from bindaems.app.learning.thermal_model import ModeModel, ModeProfile
from bindaems.app.planning import thermal_stage
from bindaems.app.planning.inputs import PlanInputs, PlanningUnavailable
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.app.planning.service import PlanningService, PlanningStatus, plan_once
from bindaems.app.planning.thermal_shift import ShiftedSlot, ShiftInput, ShiftResult
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.plan import BatteryIntent, PlanTrigger, intent_plan
from bindaems.shared.settings import PlanningSettings, ThermalPlanSettings
from bindaems.shared.timeutil import SLOT, ManualClock, slot_start

Solver = Callable[[PlanInputs], Awaitable[OptimizerResult]]


def returns_flows_with_soc_below_reserve() -> Solver:
    """Solver mit stimmigen Flüssen (Eigenverbrauch), dessen SOC nach dem ersten Slot leer ist."""

    async def solver(inputs: PlanInputs) -> OptimizerResult:
        result = flows_of(resimulate(inputs, [BatteryIntent()] * len(inputs.slots)), inputs)
        first = replace(result.flows[0], soc_end_kwh=0.0)
        return replace(result, flows=(first, *result.flows[1:]))

    return solver


def raises(exc: Exception) -> Solver:
    async def solver(inputs: PlanInputs) -> OptimizerResult:
        raise exc

    return solver


def failed(status: str, message: str | None = None) -> OptimizerResult:
    return OptimizerResult(status, (), {}, None, 10, message)  # type: ignore[arg-type]


def evening_inputs() -> PlanInputs:
    """Vier Slots, der letzte teuer; der kleine Akku reicht nicht für alles."""
    return scenario(
        prices=[10.0, 10.0, 10.0, 40.0],
        pv=[0.0] * 4,
        load=[3000.0] * 4,
        soc_pct=40.0,
        battery=small_battery(),
    )


# --- Planungslauf -----------------------------------------------------------------------------


async def test_replan_stores_optimizer_plan(service, store):
    plan = await service.replan("manual")
    assert (
        plan.source == "optimizer"
        and plan.trigger == "manual"
        and store.latest() == plan == service.current()
    )
    assert store.inputs_of(plan.id).settings_version == plan.settings_version
    assert plan.solver_status == "optimal" and plan.fallback_reason is None and plan.issues == []
    assert len(plan.slots) == 96


async def test_listeners_get_the_saved_plan(service):
    heard = []
    service.on_plan(lambda plan: heard.append((plan, service.current())))
    plan = await service.replan("manual")
    assert heard == [(plan, plan)]  # nach ``self._plan = saved``
    assert plan.id is not None


async def test_listener_errors_are_only_logged(service):
    def broken(plan):
        raise RuntimeError("Zuhörer kaputt")

    heard = []
    service.on_plan(broken)
    service.on_plan(heard.append)
    with capture_logs() as logs:
        plan = await service.replan("manual")
    assert heard == [plan] and service.current() == plan
    assert service.status().last_error is None
    assert any(entry["log_level"] == "error" for entry in logs)


async def test_failed_solver_gives_fallback(service_with):
    service = service_with(solver=returns(OptimizerResult("timeout", (), {}, None, 30000)))
    plan = await service.replan("slot")
    assert (plan.source, plan.fallback_reason) == ("fallback", "Solver nach 30 s abgebrochen")
    assert (
        service.component_status().message
        == "Ersatzplan von 12:01 Uhr: Solver nach 30 s abgebrochen"
    )
    assert plan.solver_status == "timeout" and not service.component_status().ok


async def test_implausible_result_gives_fallback(service_with):
    service = service_with(solver=returns_flows_with_soc_below_reserve())
    plan = await service.replan("slot")
    assert plan.source == "fallback" and plan.fallback_reason.startswith(
        "Plan unplausibel: SOC außerhalb der Grenzen um "
    )
    assert plan.fallback_reason == "Plan unplausibel: SOC außerhalb der Grenzen um 12:00"
    assert plan.issues[0] == "SOC außerhalb der Grenzen um 12:00"
    assert plan.solver_status == "optimal"


async def test_solver_exception_gives_fallback(service_with):
    service = service_with(solver=raises(RuntimeError("HiGHS weg")))
    plan = await service.replan("slot")
    assert (plan.source, plan.fallback_reason) == ("fallback", "Solver fehlgeschlagen: HiGHS weg")
    assert plan.solver_status == "error"
    assert service.status() == PlanningStatus(running=False, unavailable=None, last_error=None)


async def test_unavailable_keeps_previous_plan_and_reports(service, live):
    first = await service.replan("start")
    live.connected = False
    assert await service.replan("slot") is None
    assert service.current() == first
    assert service.component_status().message == "Kein Plan: kein SOC vom core"
    assert service.status().unavailable == "kein SOC vom core"


async def test_unavailable_clears_after_next_plan(service, live):
    live.connected = False
    assert await service.replan("slot") is None
    live.connected = True
    assert await service.replan("slot") is not None
    assert service.status().unavailable is None
    assert service.component_status().message == "Plan von 12:01 Uhr"


async def test_missing_pv_forecast_reports(service, forecast):
    forecast.forecast = None
    assert await service.replan("slot") is None
    assert service.component_status().message == "Kein Plan: keine PV-Prognose"


async def test_missing_load_forecast_reports(service, load_forecast):
    load_forecast.forecast = None
    assert await service.replan("slot") is None
    assert service.component_status().message == "Kein Plan: keine Lastprognose"


async def test_running_while_solving(service_with):
    entered, gate = asyncio.Event(), asyncio.Event()
    seen: list[PlanningStatus] = []

    async def solver(inputs: PlanInputs) -> OptimizerResult:
        seen.append(service.status())
        entered.set()
        await gate.wait()
        return await asyncio.to_thread(solve, inputs)

    service = service_with(solver=solver)
    task = asyncio.create_task(service.replan("manual"))
    await entered.wait()
    assert service.status().running and seen[0].running
    gate.set()
    await task
    assert not service.status().running


async def test_store_error_is_reported_and_raised(service, store, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Datenbank voll")

    monkeypatch.setattr(store, "save", broken)
    with pytest.raises(RuntimeError, match="Datenbank voll"):
        await service.replan("manual")
    assert service.status() == PlanningStatus(
        running=False, unavailable=None, last_error="Datenbank voll"
    )
    assert service.current() is None


async def test_unavailable_clears_once_inputs_are_complete(service, store, live, monkeypatch):
    live.connected = False
    assert await service.replan("slot") is None
    live.connected = True

    def broken(*args, **kwargs):
        raise RuntimeError("Datenbank voll")

    monkeypatch.setattr(store, "save", broken)
    with pytest.raises(RuntimeError):
        await service.replan("slot")
    assert service.status().unavailable is None  # die Eingangsdaten waren vollständig
    assert service.component_status().message == "Noch kein Plan"


# --- Eingangsdaten ----------------------------------------------------------------------------


async def test_collect_uses_bms_limits_and_month_peak(service, live, ledger):
    live_signals(live, {"battery.soc_pct": 55.0, "battery.ccl_a": 50.0, "battery.voltage_v": 52.0})
    ingest(ledger, "2026-10-16T08:00:00+00:00", {"grid>house": 1000})  # 4 kW
    inputs = await service.collect("manual")
    assert inputs.battery.limit_now_charge_w == 2600.0 and inputs.month_peak_kw == 4.0


async def test_collect_battery_from_config(service, cfg):
    inputs = await service.collect("start")
    battery = inputs.battery
    assert battery.soc_pct == 50.0
    assert battery.usable_kwh == cfg.battery.usable_kwh
    assert battery.reserve_pct == cfg.battery.reserve_soc_pct
    assert battery.soc_max_pct == cfg.battery.soc_max_pct
    assert (battery.max_charge_w, battery.max_discharge_w) == (
        cfg.battery.max_charge_w,
        cfg.battery.max_discharge_w,
    )
    assert battery.max_grid_charge_setpoint_w == cfg.victron.max_grid_charge_setpoint_w
    assert battery.grid_max_import_w == (35 - 2) * 230.0 * 3
    assert battery.limit_now_charge_w is None and battery.limit_now_discharge_w is None
    assert inputs.month_peak_kw is None and inputs.trigger == "start"


async def test_collect_support_threshold_defaults_to_the_config(service, cfg):
    inputs = await service.collect("start")
    assert inputs.support_soc_pct == cfg.control.support_soc_pct == 70.0


async def test_collect_support_threshold_follows_the_given_source(service_with):
    inputs = await service_with(support_pct=lambda: 82.0).collect("start")
    assert inputs.support_soc_pct == 82.0


async def test_collect_budget_floors_from_the_config(service_with, cfg):
    # Gesamtreview I2: Ziel des Wächters und der kleinste Ladestrom des größten Autos
    # (Tesla 5 A · 3 Phasen · 230 V = 3,45 kW; e-Golf 6 A · 2 · 230 V = 2,76 kW)
    inputs = await service_with().collect("start")
    assert (inputs.peak_target_kw, inputs.car_floor_kw) == (None, pytest.approx(3.45))
    control = cfg.control.model_copy(update={"peak_target_kw": 8.0})
    inputs = await service_with(cfg=cfg.model_copy(update={"control": control})).collect("start")
    assert inputs.peak_target_kw == 8.0


async def test_collect_takes_p25_and_the_planned_cars(service_with, live, forecast):
    forecast.forecast = replace(
        forecast.forecast, p25_w={k: v / 2 for k, v in forecast.forecast.p25_w.items()}
    )
    live.health = planned_health(vehicle_health("tesla"), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    inputs = await service_with(intent=lambda: intent).collect("start")
    assert [(s.pv_w, s.pv_p25_w) for s in inputs.slots[:2]] == [(2000.0, 1000.0)] * 2
    assert [car.key for car in inputs.vehicles] == ["tesla"]
    # die gesehene Absicht ist neuer als die, mit der Health regelte: kein Auto
    inputs = await service_with(intent=lambda: Intent(version=4)).collect("start")
    assert inputs.vehicles == []
    # ohne gesehene Absicht gilt die, die Health nennt
    inputs = await service_with().collect("start")
    assert [car.key for car in inputs.vehicles] == ["tesla"]
    live.health = None
    assert (await service_with().collect("start")).vehicles == []


CLOCK_NOW = datetime.fromisoformat("2026-10-16T10:01:00+00:00")  # Uhr der Fixtures


def _driving_profile(soc: float = 40.0, arrival: int = 56) -> VehicleProfile:
    """Anwesenheit 0,8 ab der Viertelstunde ``arrival`` (56: 14:00 Ortszeit) an jedem Tag,
    Ladestand bei Ankunft ``soc``."""
    weekdays = [
        WeekdayProfile(
            weekday=day,
            basis="weekday",
            days=8,
            trips=8,
            trip_share=1.0,
            departure_min=None,
            departure_p50_min=None,
            departure_p80_min=None,
            departure_confidence=None,
            arrival_p50_min=None,
            arrival_soc_p50=soc,
            consumption_p50_pp=None,
            consumption_p80_pp=None,
            target_soc_pct=None,
            target_clamped=False,
            target_confidence=None,
        )
        for day in range(7)
    ]
    return VehicleProfile(
        key="tesla",
        source="tessie",
        data_from=CLOCK_NOW - timedelta(days=60),
        data_to=CLOCK_NOW,
        days=60,
        weekdays=weekdays,
        presence=[[0.0] * arrival + [0.8] * (96 - arrival) for _ in range(7)],
    )


async def test_collect_appends_expected_cars_after_the_plugged_ones(service_with, live):
    live.health = planned_health(
        vehicle_health("egolf"), vehicle_health("tesla", plugged=False), version=3
    )
    planned = VehicleIntent(mode=ChargeMode.PLANNED)
    intent = Intent(version=3, vehicles={"tesla": planned, "egolf": planned})
    profiles = {"tesla": _driving_profile(), "egolf": _driving_profile()}
    inputs = await service_with(intent=lambda: intent, driving_profiles=lambda: profiles).collect(
        "start"
    )
    assert [(car.key, car.expected) for car in inputs.vehicles] == [
        ("egolf", False),
        ("tesla", True),
    ]
    tesla = inputs.vehicles[1]
    assert tesla.arrival_at == datetime.fromisoformat("2026-10-16T12:00:00+00:00")  # 14:00 Ortszeit
    assert (tesla.soc_pct, tesla.soc_assumed) == (40.0, False)


async def test_collect_expects_no_cars_without_profiles(service_with, live):
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    inputs = await service_with(intent=lambda: intent).collect("start")
    assert inputs.vehicles == []
    inputs = await service_with(intent=lambda: intent, driving_profiles=lambda: {}).collect("start")
    assert inputs.vehicles == []


async def test_collect_checks_health_against_the_newer_intent(service_with, live):
    # Der core hat schon Version 4, regelte aber noch mit 3; gesehen ist erst Version 3
    health = planned_health(vehicle_health("tesla"), version=3)
    live.health = {**health, "intent": {"version": 4}}
    seen = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    assert (await service_with(intent=lambda: seen).collect("start")).vehicles == []
    # umgekehrt: die gesehene Absicht ist neuer als die in Health
    live.health = health
    newer = seen.model_copy(update={"version": 4})
    assert (await service_with(intent=lambda: newer).collect("start")).vehicles == []
    # beide gleich alt: das Auto steht im Plan
    inputs = await service_with(intent=lambda: seen).collect("start")
    assert [car.key for car in inputs.vehicles] == ["tesla"]


async def test_collect_grid_limit_from_max_import(service_with, cfg):
    limited = cfg.model_copy(update={"grid": cfg.grid.model_copy(update={"max_import_kw": 11.0})})
    inputs = await service_with(cfg=limited).collect("start")
    assert inputs.battery.grid_max_import_w == 11000.0


async def test_collect_discharge_limit_needs_voltage(service, live):
    live_signals(live, {"battery.dcl_a": 100.0})
    assert (await service.collect("start")).battery.limit_now_discharge_w is None
    live_signals(live, {"battery.voltage_v": 50.0})
    assert (await service.collect("start")).battery.limit_now_discharge_w == 5000.0


async def test_collect_ignores_non_numeric_soc(service, live):
    live_signals(live, {"battery.soc_pct": "voll"})  # type: ignore[dict-item]
    assert await service.replan("slot") is None
    assert service.component_status().message == "Kein Plan: kein SOC vom core"


async def test_collect_prices_settings_and_horizon(service, settings_service):
    inputs = await service.collect("prices")
    assert inputs.settings_version == settings_service.current().version
    assert inputs.planning == settings_service.current().settings.planning
    assert (
        len(inputs.slots) == 96 and inputs.slots[0].start.isoformat() == "2026-10-16T10:00:00+00:00"
    )
    assert all(slot.origin == "primary" for slot in inputs.slots)
    # 16:00Z ist 18:00 Ortszeit: teuer (Bezugspreis brutto über dem billigen Slot)
    assert inputs.slots[24].import_ct > inputs.slots[0].import_ct
    assert inputs.slots[0].pv_w == 2000.0 and inputs.slots[0].load_w == 600.0
    assert inputs.created_at.isoformat() == "2026-10-16T10:01:00+00:00"


async def test_collect_uses_previous_plan_for_inertia(service):
    assert all(slot.prev_battery_w is None for slot in (await service.collect("start")).slots)
    await service.replan("start")
    inputs = await service.collect("slot")
    assert [slot.prev_battery_w is not None for slot in inputs.slots[:5]] == [True] * 4 + [False]


# --- Status -----------------------------------------------------------------------------------


def test_status_before_first_run(service):
    status = service.component_status()
    assert (status.name, status.ok, status.message) == ("planning", False, "Noch kein Plan")
    assert service.current() is None
    assert service.status() == PlanningStatus(running=False, unavailable=None, last_error=None)


async def test_status_ok_and_expired(service, clock):
    plan = await service.replan("manual")
    status = service.component_status()
    assert (status.ok, status.message) == (True, "Plan von 12:01 Uhr")
    clock.advance(timedelta(minutes=45))
    assert plan.valid_until == clock.now()
    status = service.component_status()
    assert (status.ok, status.message) == (False, "Plan abgelaufen seit 12:46 Uhr")


async def test_current_loads_latest_plan_from_store(service, service_with):
    plan = await service.replan("manual")
    assert service_with().current() == plan


# --- plan_once --------------------------------------------------------------------------------


def test_plan_once_optimizer_plan():
    inputs = evening_inputs()
    result = solve(inputs)
    plan = plan_once(inputs, result)
    assert (plan.source, plan.solver_status, plan.fallback_reason, plan.issues) == (
        "optimizer",
        "optimal",
        None,
        [],
    )


def test_plan_once_time_limit_with_flows_is_a_result():
    inputs = evening_inputs()
    result = replace(solve(inputs), status="time_limit")
    plan = plan_once(inputs, result)
    assert (plan.source, plan.solver_status, plan.fallback_reason) == (
        "optimizer",
        "time_limit",
        None,
    )


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (failed("time_limit"), "Zeitlimit ohne Lösung"),
        (failed("infeasible"), "Solver ohne Lösung (infeasible)"),
        (failed("timeout", "Kind hängt"), "Solver nach 30 s abgebrochen"),
        (failed("error", "Modell kaputt"), "Solver fehlgeschlagen: Modell kaputt"),
        (failed("error"), "Solver fehlgeschlagen: unbekannter Fehler"),
    ],
)
def test_plan_once_fallback_reasons(result, reason):
    inputs = evening_inputs()
    plan = plan_once(inputs, result)
    assert (plan.source, plan.fallback_reason, plan.solver_status) == (
        "fallback",
        reason,
        result.status,
    )
    assert plan.issues == [] and len(plan.slots) == 4
    assert plan.slots[0].reasons[0].code == "ERSATZPLAN"


def test_plan_once_wrong_slot_count_is_failure():
    inputs = evening_inputs()
    result = solve(inputs)
    plan = plan_once(inputs, replace(result, flows=result.flows[:3]))
    assert plan.source == "fallback"
    assert plan.fallback_reason == "Solver fehlgeschlagen: Ergebnis mit 3 statt 4 Slots"


def test_plan_once_implausible_keeps_all_findings():
    inputs = evening_inputs()
    result = flows_of(resimulate(inputs, [BatteryIntent()] * 4), inputs)
    broken = tuple(replace(item, soc_end_kwh=0.0) for item in result.flows)
    plan = plan_once(inputs, replace(result, flows=broken))
    assert plan.source == "fallback"
    assert plan.fallback_reason == f"Plan unplausibel: {plan.issues[0]}"
    assert plan.issues[0] == "SOC außerhalb der Grenzen um 02:00"


def test_plan_once_optimizer_budget_uses_peaks():
    inputs = scenario(
        prices=[10.0] * 4, pv=[0.0] * 4, load=[3000.0] * 4, peak_months=[month("2026-10", 2.0)]
    )
    result = solve(inputs)
    plan = plan_once(inputs, replace(result, peaks_kw={"2026-10": 6.24}))
    assert plan.source == "optimizer" and plan.slots[0].budget_kw == 6.3


def test_plan_once_budget_is_not_raised_by_an_expected_car():
    # Ruling F-1: Das Budget des Wächters geht an den core; ein erwartetes Auto (nur Prognose)
    # darf es nicht anheben. Ein angestecktes Auto hebt es weiter an.
    base = {
        "prices": [10.0] * 8,
        "pv": [0.0] * 8,
        "load": [500.0] * 8,
        "peak_months": [month("2026-10", 2.0)],
    }
    alone = scenario(**base)
    arrival = alone.slots[5].start
    car = planned_car(need_kwh=10.0, expected=True, arrival_at=arrival)
    plugged = car.model_copy(update={"expected": False, "arrival_at": None})

    def budgets(inputs: PlanInputs) -> tuple[list[float | None], list[float | None]]:
        plan = plan_once(inputs, solve(inputs)).model_copy(update={"id": 1})
        sent = intent_plan(plan, grid_charge_allowed=False)
        return [s.budget_kw for s in plan.slots], [s.budget_kw for s in sent.slots]

    without = budgets(alone)
    assert without == ([2.0] * 8, [2.0] * 3)
    expected_plan = plan_once(with_car := scenario(**base, vehicles=[car]), solve(with_car))
    assert expected_plan.source == "optimizer"
    assert expected_plan.vehicles[0].planned_kwh == pytest.approx(3 * 11.04 * 0.25)
    assert budgets(with_car) == without
    raised, _ = budgets(scenario(**base, vehicles=[plugged]))
    assert all(value is not None and value > 2.0 for value in raised)


# --- Auslöser und Schleife (Task 10) ---------------------------------------------------------


def recording(calls: list[PlanInputs]) -> Solver:
    """Solver, der die Eingangsdaten an ``calls`` hängt und im selben Prozess löst."""

    async def solver(inputs: PlanInputs) -> OptimizerResult:
        calls.append(inputs)
        return await asyncio.to_thread(solve, inputs)

    return solver


def fast_sleep(clock: ManualClock | None = None) -> Callable[[float], Awaitable[None]]:
    """Ersatz für das Warten der Schleife: kurz, auf Wunsch rückt die Uhr um die Wartezeit vor."""

    async def sleep(seconds: float) -> None:
        if clock is not None:
            clock.advance(timedelta(seconds=seconds))
        await asyncio.sleep(0.005)

    return sleep


@contextlib.asynccontextmanager
async def running(service: PlanningService):
    task = asyncio.create_task(service.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def triggers(calls: list[PlanInputs]) -> list[PlanTrigger]:
    return [inputs.trigger for inputs in calls]


async def test_requests_during_solve_coalesce_into_one_rerun(service_with):
    gate, calls = asyncio.Event(), []
    service = service_with(solver=blocking_solver(gate, calls))
    task = asyncio.create_task(service.run())
    await until(lambda: len(calls) == 1)  # Lauf „start“ hängt
    for trigger in ("prices", "pv_forecast", "manual"):
        service.request(trigger)
    gate.set()
    await until(lambda: len(calls) == 2)
    await asyncio.sleep(0.05)
    assert [i.trigger for i in calls] == ["start", "manual"]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_settings_change_requests_replan(service, settings_service):
    set_planning(settings_service, PlanningSettings(grid_charge_allowed=True))
    assert service.pending() == "settings"


async def test_irrelevant_settings_change_requests_nothing(service, settings_service):
    current = settings_service.current()
    settings_service.update(
        current.settings.model_copy(update={"wallbox_names": {"evcs": "Garage"}}),
        base_version=current.version,
        actor="test",
        source="ui",
    )
    assert service.pending() is None


def test_waiting_trigger_stays_unless_manual(service):
    assert service.pending() is None
    service.request("prices")
    service.request("soc")
    assert service.pending() == "prices"
    service.request("manual")
    service.request("slot")
    assert service.pending() == "manual"


async def test_run_plans_at_start(service_with, store):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls))
    async with running(service):
        await until(lambda: service.current() is not None)
    assert triggers(calls) == ["start"] and store.latest().trigger == "start"
    assert service.pending() is None


async def test_run_takes_a_trigger_requested_before_the_start(service_with):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls))
    service.request("manual")
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
    assert triggers(calls) == ["manual"]


async def test_run_plans_one_minute_into_each_slot(service_with, clock):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep(clock))
    async with running(service):
        await until(lambda: len(calls) == 2)
    assert triggers(calls) == ["start", "slot"]
    assert calls[1].created_at.isoformat() == "2026-10-16T10:16:00+00:00"  # 10:01 + 15 min


async def test_new_pv_forecast_requests_replan(service_with, forecast):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)  # die Schleife kennt den Fingerabdruck
        newer = forecast.forecast.issued_at + timedelta(minutes=5)
        forecast.forecast = replace(forecast.forecast, issued_at=newer)
        await until(lambda: len(calls) == 2)
    assert triggers(calls) == ["start", "pv_forecast"]


async def test_new_prices_request_replan(service_with, price_store, clock):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        last = slot_start(clock.now()) + 96 * SLOT
        price_store.save([price_row(last + i * SLOT, 12.0, "primary") for i in range(4)])
        await until(lambda: len(calls) == 2)
    assert triggers(calls) == ["start", "prices"]


async def test_soc_deviation_requests_replan_once_per_slot(service_with, live):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    async with running(service):
        await until(lambda: len(calls) == 1)
        live_signals(live, {"battery.soc_pct": 80.0})
        await until(lambda: len(calls) == 2)
        live_signals(live, {"battery.soc_pct": 20.0})  # im selben Slot nicht noch einmal
        await asyncio.sleep(0.1)
    assert triggers(calls) == ["start", "soc"]
    assert calls[1].battery.soc_pct == 80.0


async def test_month_peak_rise_requests_replan(service_with, ledger):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    async with running(service):
        await until(lambda: len(calls) == 1)
        ingest(ledger, "2026-10-16T08:00:00+00:00", {"grid>house": 1000})  # 4 kW
        await until(lambda: len(calls) == 2)
        await asyncio.sleep(0.1)  # die neue Spitze steht im Plan: kein weiterer Lauf
    assert triggers(calls) == ["start", "month_peak"] and calls[1].month_peak_kw == 4.0


async def test_request_from_another_thread_wakes_the_loop(service_with):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls))  # wartet sonst 30 s
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.to_thread(service.request, "manual")
        await until(lambda: len(calls) == 2)
    assert triggers(calls) == ["start", "manual"]


async def test_run_logs_errors_and_continues(service_with, store, monkeypatch):
    save = store.save
    failures = [RuntimeError("Datenbank gesperrt")]

    def flaky(*args, **kwargs):
        if failures:
            raise failures.pop()
        return save(*args, **kwargs)

    monkeypatch.setattr(store, "save", flaky)
    service = service_with()
    with capture_logs() as logs:
        async with running(service):
            await until(lambda: service.status().last_error == "Datenbank gesperrt")
            service.request("manual")
            await until(lambda: service.current() is not None)
    assert "Planungslauf fehlgeschlagen" in [entry["event"] for entry in logs]
    assert service.current().trigger == "manual" and service.status().last_error is None


async def test_cancel_stops_the_loop_during_a_run(service_with):
    gate, calls = asyncio.Event(), []
    service = service_with(solver=blocking_solver(gate, calls))
    task = asyncio.create_task(service.run())
    await until(lambda: len(calls) == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.status().running


async def test_failed_watch_is_logged_and_the_run_still_plans(service_with, prices, monkeypatch):
    def broken(start, end):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(prices, "slots", broken)
    service = service_with()
    with capture_logs() as logs:
        async with running(service):
            await until(lambda: service.current() is not None)
    assert "Auslöser der Planung nicht prüfbar" in [entry["event"] for entry in logs]
    assert service.current().trigger == "start"


async def test_persistent_trigger_waits_between_runs(service_with, live, forecast, monkeypatch):
    """Ein anhaltender Auslöser (SOC weicht ab, Eingangsdaten fehlen) plant einmal je Wartezeit,
    nicht in einer engen Schleife."""
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0.02)

    service = service_with(sleep=sleep)
    collects: list[PlanTrigger] = []
    collect = service.collect

    async def counting(trigger: PlanTrigger) -> PlanInputs:
        collects.append(trigger)
        return await collect(trigger)

    monkeypatch.setattr(service, "collect", counting)
    async with running(service):
        await until(lambda: service.current() is not None)
        forecast.forecast = None
        live_signals(live, {"battery.soc_pct": 90.0})
        await until(lambda: len(sleeps) >= 10)
    assert service.status().unavailable == "keine PV-Prognose"
    assert collects.count("soc") >= 5
    assert len(collects) <= len(sleeps) + 2  # höchstens ein Lauf je Wartezeit


async def test_vehicle_change_requests_replan(service_with, live):
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep(), intent=lambda: intent)
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)  # die Schleife kennt den Fingerabdruck
        live.health = planned_health(vehicle_health("tesla"), version=3)  # angesteckt
        await until(lambda: len(calls) == 2)
        live.health = planned_health(vehicle_health("tesla", soc_pct=45.0), version=3)
        await asyncio.sleep(0.1)  # Ladestand allein löst nichts aus
        live.health = planned_health(vehicle_health("tesla", target=90.0), version=3)
        await until(lambda: len(calls) == 3)
    assert triggers(calls) == ["start", "vehicles", "vehicles"]
    assert [[car.key for car in inputs.vehicles] for inputs in calls] == [[], ["tesla"], ["tesla"]]
    assert calls[2].vehicles[0].target_soc_pct == 90.0


async def test_support_threshold_change_requests_a_plan(service_with):
    """Die Unterstützungsschwelle geht in den Plan ein: Ändert sie sich (anderer Wert der
    Einstellungen), plant der Dienst mit dem Auslöser „settings“ neu, genau einmal."""
    support = {"pct": 70.0}
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls), sleep=fast_sleep(), support_pct=lambda: support["pct"]
    )
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.1)  # die Schleife kennt den Fingerabdruck
        support["pct"] = 60.0
        await until(lambda: len(calls) == 2)
        await asyncio.sleep(0.2)  # keine weitere Anfrage
    assert triggers(calls) == ["start", "settings"]
    assert calls[1].support_soc_pct == 60.0


def _planned_tesla(live, **changes) -> Intent:
    """Health mit dem Tesla in „Geplant“ (``changes`` wie bei ``vehicle_health``) und die
    passende Absicht."""
    live.health = planned_health(vehicle_health("tesla", **changes), version=3)
    return Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})


async def test_charge_limit_flapping_replans_at_most_every_five_minutes(service_with, live, clock):
    """Wechselt das Ladelimit alle 30 s über 10 min, plant der Dienst höchstens alle 5 min neu:
    die erste Änderung sofort (30 s, Limit 70), die nächste erst nach 300 s: Bei 330 s steht das
    Limit wieder auf 70 wie beim letzten Lauf, bei 360 s weicht es mit 80 ab; 660 s liegt hinter
    den 10 min. Das sind genau zwei Läufe „vehicles“."""
    intent = _planned_tesla(live, charge_limit_pct=80.0)
    calls: list[PlanInputs] = []
    steps = {"n": 0}

    async def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))
        steps["n"] += 1
        limit = 70.0 if steps["n"] % 2 else 80.0
        live.health = planned_health(vehicle_health("tesla", charge_limit_pct=limit), version=3)
        await asyncio.sleep(0.005)

    service = service_with(solver=recording(calls), sleep=sleep, intent=lambda: intent)
    async with running(service):
        await until(lambda: steps["n"] >= 20)  # 10 min
    assert triggers(calls) == ["start", "vehicles", "vehicles"]


async def test_throttled_charge_limit_change_is_replanned_after_five_minutes(
    service_with, live, clock
):
    """Eine gedrosselte Änderung geht nicht verloren: Weicht der Fingerabdruck nach 5 min noch
    vom zuletzt geplanten ab, plant der Dienst dann neu."""
    intent = _planned_tesla(live, charge_limit_pct=80.0)
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep(), intent=lambda: intent)
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        _planned_tesla(live, charge_limit_pct=70.0)
        await until(lambda: len(calls) == 2)  # die erste Änderung gleich
        clock.advance(timedelta(seconds=60))
        _planned_tesla(live, charge_limit_pct=60.0)
        await asyncio.sleep(0.1)
        assert len(calls) == 2  # gedrosselt
        clock.advance(timedelta(seconds=239))
        await asyncio.sleep(0.1)
        assert len(calls) == 2  # 299 s seit dem letzten Lauf
        clock.advance(timedelta(seconds=2))
        await until(lambda: len(calls) == 3)
        await asyncio.sleep(0.1)  # danach ist der Fingerabdruck geplant: keine weitere Anfrage
    assert triggers(calls) == ["start", "vehicles", "vehicles"]
    assert [inputs.vehicles[0].limit_pct for inputs in calls] == [80.0, 70.0, 60.0]


async def test_target_change_replans_immediately(service_with, live, clock):
    """Ziel und Abfahrt gehören nicht zur Drosselung: Die zweite Zieländerung 40 s nach der
    ersten plant sofort neu."""
    intent = _planned_tesla(live, target=80.0)
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep(), intent=lambda: intent)
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        _planned_tesla(live, target=90.0)
        await until(lambda: len(calls) == 2)
        clock.advance(timedelta(seconds=40))
        _planned_tesla(live, target=85.0)
        await until(lambda: len(calls) == 3)
    assert triggers(calls) == ["start", "vehicles", "vehicles"]
    assert [inputs.vehicles[0].target_soc_pct for inputs in calls] == [80.0, 90.0, 85.0]


async def test_untrusted_health_does_not_flap_the_vehicles_trigger(service_with, live):
    """Fällt Health kurz weg (Neustart des core), ist das keine Änderung der Autos."""
    live.health = planned_health(vehicle_health("tesla"), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep(), intent=lambda: intent)
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        live.health = None
        await asyncio.sleep(0.1)
        live.health = planned_health(vehicle_health("tesla"), version=3)
        await asyncio.sleep(0.1)
    assert triggers(calls) == ["start"]


async def test_a_plan_made_before_health_trusts_the_intent_is_redone_for_the_cars(
    service_with, live
):
    """Ruling R23: Ein Lauf im Fenster nach einer neuen Absicht, bevor Health sie bestätigt,
    plant ohne Autos. Bestätigt Health sie dann mit unverändertem Stand der Autos, fordert der
    Dienst einen Lauf „vehicles“ an."""
    live.health = planned_health(vehicle_health("tesla"), version=4)
    seen = {"intent": Intent(version=4, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})}
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls), sleep=fast_sleep(), intent=lambda: seen["intent"]
    )
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)  # die Schleife kennt den Fingerabdruck
        # neue Absicht (etwa nach dem Senden eines Plans), Health regelt noch mit Version 4
        seen["intent"] = seen["intent"].model_copy(update={"version": 5})
        service.request("manual")
        await until(lambda: len(calls) == 2)
        await asyncio.sleep(0.1)
        live.health = planned_health(vehicle_health("tesla"), version=5)
        await until(lambda: len(calls) == 3)
        await asyncio.sleep(0.2)  # kein vierter Lauf: die Anforderung gilt einmal
    assert triggers(calls) == ["start", "manual", "vehicles"]
    assert [[car.key for car in inputs.vehicles] for inputs in calls] == [
        ["tesla"],
        [],
        ["tesla"],
    ]


# --- erwartete Autos als Auslöser (Plan 5b, Präzisierung 10, Ruling T10-4) --------------------


async def test_plug_in_of_expected_car_triggers_replan(service_with, live):
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    profiles = {"tesla": _driving_profile()}
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls),
        sleep=fast_sleep(),
        intent=lambda: intent,
        driving_profiles=lambda: profiles,
    )
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.1)  # unverändert: kein weiterer Lauf
        live.health = planned_health(vehicle_health("tesla"), version=3)  # angesteckt
        await until(lambda: len(calls) == 2)
        # der Plan wird nach dem Solver in einem Thread gespeichert: auf ihn warten
        await until(lambda: (p := service.current()) is not None and p.trigger == "vehicles")
    assert triggers(calls) == ["start", "vehicles"]
    assert [[(car.key, car.expected) for car in inputs.vehicles] for inputs in calls] == [
        [("tesla", True)],
        [("tesla", False)],
    ]
    assert calls[1].vehicles[0].soc_pct == 40.0  # jetzt der echte Ladestand aus Health
    plan = service.current()
    assert plan is not None and not plan.vehicles[0].expected


async def test_changed_expected_set_triggers_replan(service_with, live):
    # Health bleibt gleich: Eine neue Ankunft (Profil) oder ein neues Ziel (Absicht) ändert
    # die Menge der erwarteten Autos und löst „vehicles“ aus
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    seen = {"intent": Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})}
    profiles = {"tesla": _driving_profile()}
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls),
        sleep=fast_sleep(),
        intent=lambda: seen["intent"],
        driving_profiles=lambda: dict(profiles),
    )
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.1)
        profiles["tesla"] = _driving_profile(arrival=60)  # 15:00 Ortszeit statt 14:00
        await until(lambda: len(calls) == 2)
        await asyncio.sleep(0.1)
        target = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=90)
        seen["intent"] = Intent(version=3, vehicles={"tesla": target})
        await until(lambda: len(calls) == 3)
        await asyncio.sleep(0.1)
    assert triggers(calls) == ["start", "vehicles", "vehicles"]
    arrivals = [inputs.vehicles[0].arrival_at for inputs in calls]
    assert arrivals == [
        datetime.fromisoformat("2026-10-16T12:00:00+00:00"),
        datetime.fromisoformat("2026-10-16T13:00:00+00:00"),
        datetime.fromisoformat("2026-10-16T13:00:00+00:00"),
    ]
    assert [inputs.vehicles[0].target_soc_pct for inputs in calls] == [80.0, 80.0, 90.0]


async def test_changed_expected_set_is_requested_once_while_no_plan_comes(
    service_with, live, monkeypatch
):
    """Ergibt der Lauf „vehicles“ keinen Plan (``PlanningUnavailable``), bleiben die
    Eingangsdaten des letzten Plans; dieselbe neue Menge erwarteter Autos fordert trotzdem nur
    einmal an, nicht bei jeder Prüfung der Schleife."""
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    profiles = {"tesla": _driving_profile()}
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls),
        sleep=fast_sleep(),
        intent=lambda: intent,
        driving_profiles=lambda: dict(profiles),
    )
    attempts: list[PlanTrigger] = []
    collect = service.collect

    async def unavailable_after_start(trigger: PlanTrigger) -> PlanInputs:
        if calls:
            attempts.append(trigger)
            raise PlanningUnavailable("Keine Preise")
        return await collect(trigger)

    monkeypatch.setattr(service, "collect", unavailable_after_start)
    checks = 0
    expected_changed = service._expected_changed

    def counted(*args):
        nonlocal checks
        checks += 1
        return expected_changed(*args)

    monkeypatch.setattr(service, "_expected_changed", counted)
    async with running(service):
        await until(lambda: (p := service.current()) is not None and p.trigger == "start")
        profiles["tesla"] = _driving_profile(arrival=60)  # neue Ankunft: neue Menge
        await until(lambda: attempts == ["vehicles"])
        seen = checks
        await until(lambda: checks >= seen + 10)  # die Schleife prüft weiter
    assert attempts == ["vehicles"]
    assert triggers(calls) == ["start"]


async def test_untrusted_health_does_not_flap_the_expected_cars(service_with, live):
    """Fällt Health kurz weg (Neustart des core), sieht der Dienst kein erwartetes Auto; das ist
    keine Änderung der erwarteten Autos."""
    live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
    intent = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls),
        sleep=fast_sleep(),
        intent=lambda: intent,
        driving_profiles=lambda: {"tesla": _driving_profile()},
    )
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        live.health = None
        await asyncio.sleep(0.1)
        live.health = planned_health(vehicle_health("tesla", plugged=False), version=3)
        await asyncio.sleep(0.1)
    assert triggers(calls) == ["start"]
    assert [car.expected for car in calls[0].vehicles] == [True]


# --- Vorheizen und Vorkühlen (Plan 5c, Task 7) ----------------------------------------------

MODEL_AT = datetime.fromisoformat("2026-10-16T01:50:00+00:00")


def heat_model(mode: str = "heizen", *, usable: bool = True) -> ModeModel:
    """Erfundenes Wärmemodell: 120 W/K, Speicher 3 kWh/K, τ 40 h, höchstens 4 kW."""
    return ModeModel(
        mode=mode,  # type: ignore[arg-type]
        days=20,
        u_w_per_k=120.0,
        pv_w_per_kw=0.0,
        base_w=0.0,
        r2=0.9,
        tau_h=40.0,
        episodes=5,
        c_kwh_per_k=3.0,
        c_model_kwh_per_k=4.8,
        events=0,
        event_error_pct=None,
        max_w=4000.0,
        usable=usable,
        reason="ok" if usable else "few_days",
        gap_w_per_k=0.0,
    )


def full_profile(setpoint_c: float = 21.0, on_share: float = 1.0) -> ModeProfile:
    keys = [(group, quarter) for group in range(3) for quarter in range(96)]
    return ModeProfile(
        setpoint_c=dict.fromkeys(keys, setpoint_c), on_share=dict.fromkeys(keys, on_share)
    )


class FakeModels:
    """Ersatz für ``ThermalModelService``: ``current`` je Betriebsart."""

    def __init__(self, **states: tuple[ModeModel, ModeProfile, datetime]) -> None:
        self.states = states

    def current(self, mode: str) -> tuple[ModeModel, ModeProfile, datetime] | None:
        return self.states.get(mode)


def heating_models() -> FakeModels:
    return FakeModels(
        heizen=(heat_model(), full_profile(), MODEL_AT),
        kuehlen=(heat_model("kuehlen", usable=False), full_profile(24.0), MODEL_AT),
    )


def thermal_health(mode: str = "heizen", actual_c: float = 21.0, base_c: float = 21.0) -> dict:
    return {"thermal": {"klima": {"mode": mode, "actual_c": actual_c, "base_setpoint_c": base_c}}}


def set_thermal(settings_service, **changes) -> None:
    current = settings_service.current()
    learning = current.settings.learning.model_copy(
        update={"thermal": ThermalPlanSettings(**changes)}
    )
    settings_service.update(
        current.settings.model_copy(update={"learning": learning}),
        base_version=current.version,
        actor="test",
        source="ui",
    )


@pytest.fixture
def cold(forecast):
    """Außentemperatur 5 °C für alle Slots der Prognose."""
    forecast.temps = dict.fromkeys(forecast.forecast.p50_w, 5.0)
    return forecast


@pytest.fixture
def small_house(cfg, live):
    """Ein Akku mit 1 kWh an der Reserve: Mittags speist die PV ein, abends kommt teurer Strom
    aus dem Netz. Mit dem großen Akku deckt er den Abend, und Verschieben lohnt nicht."""
    live_signals(live, {"battery.soc_pct": 20.0})
    return cfg.model_copy(update={"battery": cfg.battery.model_copy(update={"usable_kwh": 1.0})})


async def test_plan_without_thermal_is_unchanged(service_with, live, cold):
    live.health = thermal_health()
    # ohne Wärmemodelle (keine Klimaanlage der Art ``coe_offset``): kein zweiter Schritt
    plain = await service_with().replan("manual")
    assert plain.thermal is None
    assert all((s.thermal_w, s.thermal_steps) == (0.0, None) for s in plain.slots)
    # ohne taugliches Modell: der Plan wie ohne, nur mit dem Grund
    service = service_with(thermal_model=FakeModels())
    plan = await service.replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.active) == ("off", False)  # Schalter ab Werk aus
    assert [s.model_dump(exclude={"start"}) for s in plan.slots] == [
        s.model_dump(exclude={"start"}) for s in plain.slots
    ]


async def test_shadow_shift_is_not_sent(service_with, live, cold, small_house):
    live.health = thermal_health()
    service = service_with(cfg=small_house, thermal_model=heating_models())
    plan = await service.replan("manual")
    thermal = plan.thermal
    assert thermal is not None
    assert (thermal.device, thermal.mode, thermal.reason) == ("klima", "heizen", "ok")
    assert thermal.active is False and thermal.saving_ct >= 2.0
    assert thermal.model_computed_at == MODEL_AT
    # Vorschau: die Slots tragen die Verschiebung, die Erklärung nennt sie nicht
    assert any(s.thermal_steps for s in plan.slots)
    assert all(s.thermal_steps is not None for s in plan.slots)
    codes = {r.code for s in plan.slots for r in s.reasons}
    assert not codes & {"KLIMA_MEHR", "KLIMA_WENIGER"}
    sent = intent_plan(plan, grid_charge_allowed=True)
    assert all((s.thermal_steps, s.thermal_grid_w) == (None, 0.0) for s in sent.slots)


async def test_active_shift_goes_into_intent(
    service_with, live, cold, settings_service, small_house
):
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    service = service_with(cfg=small_house, thermal_model=heating_models())
    plan = await service.replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.active) == ("ok", True)
    # Vorheizen vor den teuren Abendstunden (18–21 Uhr Ortszeit), Absenken darin
    expensive = [s for s in plan.slots if s.import_ct > 25]
    cheap_before = [s for s in plan.slots if s.start < expensive[0].start]
    assert max(s.thermal_steps or 0 for s in cheap_before) > 0
    assert min(s.thermal_w for s in expensive) < 0
    # Plan des ersten Schritts bleibt: Bezug, Einspeisung und Kosten unverändert
    first = await service_with(cfg=small_house).replan("manual")
    assert [(s.import_w, s.export_w, s.cost_ct) for s in plan.slots] == [
        (s.import_w, s.export_w, s.cost_ct) for s in first.slots
    ]
    # Grundcodes je Slot mit Versatz
    for slot in plan.slots:
        codes = [r.code for r in slot.reasons]
        if (slot.thermal_steps or 0) > 0:
            assert "KLIMA_MEHR" in codes
        elif (slot.thermal_steps or 0) < 0:
            assert "KLIMA_WENIGER" in codes
        else:
            assert not {"KLIMA_MEHR", "KLIMA_WENIGER"} & set(codes)
    sent = intent_plan(plan, grid_charge_allowed=True)
    kept = plan.slots[: len(sent.slots)]
    assert [s.thermal_steps for s in sent.slots] == [s.thermal_steps for s in kept]
    assert all(s.thermal_steps is not None for s in sent.slots)
    assert [s.thermal_grid_w for s in sent.slots] == [float(round(s.thermal_grid_w)) for s in kept]


@pytest.mark.parametrize("mode", ["lueften", "aus", "unbekannt", None, "warm"])
async def test_no_thermal_steps_without_known_mode(
    service_with, live, cold, settings_service, mode
):
    set_thermal(settings_service, enabled=True)
    health = thermal_health()
    if mode is None:
        del health["thermal"]["klima"]["mode"]
    else:
        health["thermal"]["klima"]["mode"] = mode
    live.health = health
    plan = await service_with(thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.active) == ("mode", False)
    assert plan.thermal.mode == (mode if mode in ("lueften", "aus") else "unbekannt")
    assert all((s.thermal_w, s.thermal_steps) == (0.0, None) for s in plan.slots)


async def test_without_core_connection_the_mode_is_unknown(
    service_with, live, cold, settings_service
):
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    live.connected = False  # Health ist dann alt
    service = service_with(thermal_model=heating_models())
    inputs = evening_inputs()
    plan = await service._with_thermal(plan_once(inputs, solve(inputs)), inputs)
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.mode) == ("mode", "unbekannt")


async def test_model_for_other_mode_is_not_used(service_with, live, cold, settings_service):
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health("kuehlen", actual_c=24.0, base_c=24.0)
    # nur das Heizmodell taugt; die Anlage kühlt: kein Modell
    plan = await service_with(thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.mode, plan.thermal.active) == (
        "no_model",
        "kuehlen",
        False,
    )
    assert plan.thermal.model_computed_at == MODEL_AT  # der Stand des untauglichen Kühlmodells
    assert all(s.thermal_steps is None for s in plan.slots)
    # ganz ohne Stand der Betriebsart
    only_heat = FakeModels(heizen=(heat_model(), full_profile(), MODEL_AT))
    plan = await service_with(thermal_model=only_heat).replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.model_computed_at) == ("no_model", None)


async def test_switch_off_reports_off_unless_preview(service_with, live, cold):
    live.health = thermal_health("kuehlen")
    plan = await service_with(thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None and plan.thermal.reason == "off"


@pytest.mark.parametrize(("enabled", "reason"), [(False, "off"), (True, "solver")])
async def test_unexpected_error_keeps_the_plan(
    service_with, live, cold, monkeypatch, settings_service, enabled, reason
):
    set_thermal(settings_service, enabled=enabled)
    live.health = thermal_health()

    def broken(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("bindaems.app.planning.service.apply_thermal", broken)
    with capture_logs() as logs:
        plan = await service_with(thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.active, plan.thermal.mode) == (
        reason,
        False,
        "heizen",
    )
    assert all(s.thermal_steps is None for s in plan.slots)
    assert "Vorheizen und Vorkühlen nicht berechnet" in [e["event"] for e in logs]


async def test_hanging_stage_is_bounded_and_keeps_the_plan(
    service_with, live, cold, monkeypatch, settings_service
):
    # Plan 3b rechnet den Optimierer im Kindprozess, damit ein hängender Solver die app nicht
    # blockiert; der zweite Schritt läuft im Thread und bekommt eine äußere Grenze
    from bindaems.app.planning import service as service_module
    from bindaems.app.planning.thermal_shift import TIME_LIMIT_S

    assert service_module.THERMAL_STAGE_TIMEOUT_S == TIME_LIMIT_S + 5.0
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    release = threading.Event()

    def hanging(*args, **kwargs):
        release.wait(10)
        raise RuntimeError("zu spät")

    monkeypatch.setattr(service_module, "apply_thermal", hanging)
    monkeypatch.setattr(service_module, "THERMAL_STAGE_TIMEOUT_S", 0.05)
    try:
        with capture_logs() as logs:
            plan = await asyncio.wait_for(
                service_with(thermal_model=heating_models()).replan("manual"), timeout=5
            )
    finally:
        release.set()
    assert plan.thermal is not None
    assert (plan.thermal.reason, plan.thermal.active) == ("solver", False)
    assert all(s.thermal_steps is None for s in plan.slots)  # der Plan des ersten Schritts gilt
    assert (
        "Vorheizen und Vorkühlen nach der Zeitgrenze abgebrochen, der Plan gilt ohne Verschiebung"
        in [e["event"] for e in logs]
    )


async def test_error_reading_the_temperatures_degrades_like_the_rest(
    service_with, live, cold, settings_service, monkeypatch
):
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()

    def broken():
        raise RuntimeError("Prognose kaputt")

    monkeypatch.setattr(cold, "temperatures", broken)
    plan = await service_with(thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None and plan.thermal.reason == "solver"


async def test_fallback_plan_gets_the_shift(
    service_with, live, cold, settings_service, small_house
):
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    service = service_with(
        cfg=small_house,
        solver=returns(OptimizerResult("timeout", (), {}, None, 30000)),
        thermal_model=heating_models(),
    )
    plan = await service.replan("slot")
    assert plan.source == "fallback"
    assert plan.thermal is not None and (plan.thermal.reason, plan.thermal.active) == ("ok", True)
    assert any(s.thermal_steps for s in plan.slots)


async def test_thermal_settings_come_from_the_run(
    service_with, live, cold, settings_service, small_house, monkeypatch
):
    """Die Einstellungen der Verschiebung sind die, mit denen der Lauf begann (``collect``):
    Der Plan trägt deren Version. Ein Schalter, der während des Lösens umgelegt wird, plant
    danach ohnehin neu (``settings``)."""
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    service = service_with(cfg=small_house, thermal_model=heating_models())
    solve_inline_ = service._solver

    async def switching(inputs):
        set_thermal(settings_service, enabled=False)
        return await solve_inline_(inputs)

    monkeypatch.setattr(service, "_solver", switching)
    plan = await service.replan("manual")
    assert plan.thermal is not None and plan.thermal.active is True
    assert plan.settings_version == settings_service.current().version - 1


async def test_band_is_capped_by_the_max_offset_of_the_device(
    service_with, live, cold, settings_service, small_house
):
    """Mit ``max_offset_k`` 1,0 und dem Band ab Werk (+1,5 / −1,0 K) plant die Verschiebung
    höchstens +2 Stufen."""
    set_thermal(settings_service, enabled=True)
    live.health = thermal_health()
    device = small_house.thermal.devices["klima"].model_copy(update={"max_offset_k": 1.0})
    thermal = small_house.thermal.model_copy(update={"devices": {"klima": device}})
    capped = small_house.model_copy(update={"thermal": thermal})
    plan = await service_with(cfg=capped, thermal_model=heating_models()).replan("manual")
    assert plan.thermal is not None and plan.thermal.reason == "ok"
    steps = [s.thermal_steps or 0 for s in plan.slots]
    assert max(steps) == 2 and min(steps) >= -2
    wide = await service_with(cfg=small_house, thermal_model=heating_models()).replan("manual")
    assert max(s.thermal_steps or 0 for s in wide.slots) == 3  # ohne Deckel bis +1,5 K


def test_band_uses_the_smaller_of_setting_and_max_offset() -> None:
    settings = ThermalPlanSettings(heat_up_k=1.5, heat_down_k=1.0, cool_up_k=2.5, cool_down_k=0.5)
    assert thermal_stage.band("heizen", settings, max_offset_k=3.0) == (1.5, 1.0)
    assert thermal_stage.band("heizen", settings, max_offset_k=1.0) == (1.0, 1.0)
    assert thermal_stage.band("kuehlen", settings, max_offset_k=2.0) == (2.0, 0.5)
    # auf ein Vielfaches von 0,5 abgerundet
    assert thermal_stage.band("kuehlen", settings, max_offset_k=1.7) == (1.5, 0.5)


# --- Eingabe der Verschiebung --------------------------------------------------------------


def _recording_shift(seen: list[ShiftInput], reason: str = "ok", steps: int = 1):
    def fake(inp: ShiftInput) -> ShiftResult:
        seen.append(inp)
        slots = [ShiftedSlot(s.start, 500.0 * steps, 250.0, steps) for s in inp.slots]
        return ShiftResult(slots=slots, saving_ct=5.0, reason=reason)  # type: ignore[arg-type]

    return fake


def _apply(plan, entry, *, models=None, temps=None, settings=None, shift_fn=None, max_k=3.0):
    return thermal_stage.apply_thermal(
        plan,
        device="klima",
        max_offset_k=max_k,
        entry=entry,
        models=models or heating_models(),
        temperatures=temps if temps is not None else {},
        settings=settings or ThermalPlanSettings(enabled=True),
        end_value_ct=17.5,
        shift_fn=shift_fn or thermal_stage.shift,
    )


def _sample_plan(slots: list[str]):
    from tests.plan_helpers import sample_plan

    return sample_plan("2026-10-16T10:01:00+00:00", slots)


def test_shift_input_from_plan_slots_and_health() -> None:
    plan = _sample_plan(["10:00", "10:15", "10:30"])
    first, second, third = plan.slots
    plan = plan.model_copy(
        update={
            "slots": [
                first.model_copy(
                    update={"export_w": 800.0, "import_w": 0.0, "pv_w": 2000.0, "budget_kw": 5.5}
                ),
                second.model_copy(update={"import_w": 1200.0, "discharge_w": 300.0}),
                third,
            ]
        }
    )
    key = thermal_stage.profile_key(first.start)
    profile = ModeProfile(
        setpoint_c={key: 21.0, thermal_stage.profile_key(second.start): 20.0},
        on_share={key: 0.9},  # der zweite Slot hat keinen Laufanteil, der dritte nichts
    )
    model = heat_model()
    temps = {first.start: 5.0, second.start: 6.0, third.start: 7.0}
    seen: list[ShiftInput] = []
    _apply(
        plan,
        {"mode": "heizen", "actual_c": 21.6, "base_setpoint_c": 21.0},
        models=FakeModels(heizen=(model, profile, MODEL_AT)),
        temps=temps,
        settings=ThermalPlanSettings(enabled=True, heat_up_k=1.0, heat_down_k=0.5, margin_ct=3.0),
        shift_fn=_recording_shift(seen),
    )
    (inp,) = seen
    assert (inp.mode, inp.c_kwh_per_k, inp.tau_h, inp.max_w) == ("heizen", 3.0, 40.0, 4000.0)
    assert (inp.up_k, inp.down_k, inp.margin_ct, inp.end_value_ct) == (1.0, 0.5, 3.0, 17.5)
    assert inp.start_k == pytest.approx(0.6)
    a, b, c = inp.slots
    assert a.base_w == pytest.approx(120.0 * 16 * 0.9) and a.on_share == 0.9
    assert (a.export_w, a.import_w, a.budget_w) == (800.0, 0.0, 5500.0)
    assert (b.base_w, b.on_share) == (pytest.approx(0.0), 0.0)  # ohne Laufanteil
    assert (b.import_w, b.discharge_w, b.budget_w) == (1200.0, 300.0, None)
    assert (c.base_w, c.on_share) == (0.0, 0.0)  # ohne Sollwert
    assert (a.import_ct, a.feed_in_ct) == (first.import_ct, first.feed_in_ct)


def test_slot_without_temperature_is_not_shifted() -> None:
    plan = _sample_plan(["10:00", "10:15"])
    seen: list[ShiftInput] = []
    _apply(
        plan,
        {"mode": "heizen"},
        temps={plan.slots[0].start: 5.0},
        shift_fn=_recording_shift(seen),
    )
    first, second = seen[0].slots
    assert first.base_w > 0 and first.on_share == 1.0
    assert (second.base_w, second.on_share) == (0.0, 0.0)  # nie ein Ersatzwert
    assert seen[0].start_k == 0.0  # ohne Ist und Sollwert


def test_cooling_inverts_start_and_keeps_power_steps() -> None:
    """Beim Kühlen ist vorgekühlt positiv: Ist 23,4 °C bei Soll 24 °C ist ``start_k`` +0,6;
    die Stufen bleiben Leistung (positiv mehr Kühlen) und gehen so in Slots und Erklärung."""
    plan = _sample_plan(["10:00", "10:15"])
    seen: list[ShiftInput] = []
    models = FakeModels(kuehlen=(heat_model("kuehlen"), full_profile(24.0), MODEL_AT))
    result = _apply(
        plan,
        {"mode": "kuehlen", "actual_c": 23.4, "base_setpoint_c": 24.0},
        models=models,
        temps=dict.fromkeys((s.start for s in plan.slots), 32.0),
        settings=ThermalPlanSettings(enabled=True, cool_up_k=1.5, cool_down_k=1.0),
        shift_fn=_recording_shift(seen, steps=2),
    )
    (inp,) = seen
    assert inp.mode == "kuehlen" and inp.start_k == pytest.approx(0.6)
    assert (inp.up_k, inp.down_k) == (1.5, 1.0)
    assert inp.slots[0].base_w == pytest.approx(120.0 * 8)  # außen − Soll
    assert [s.thermal_steps for s in result.slots] == [2, 2]
    reason = [r for r in result.slots[0].reasons if r.code == "KLIMA_MEHR"]
    assert reason[0].params == {"mode": "kuehlen", "k": 1.0}
    warm = _apply(
        plan,
        {"mode": "kuehlen", "actual_c": 24.5, "base_setpoint_c": 24.0},
        models=models,
        temps={},
        shift_fn=_recording_shift(seen),
    )
    assert seen[-1].start_k == pytest.approx(-0.5) and warm.thermal is not None
    heating = _apply(
        plan,
        {"mode": "heizen", "actual_c": 20.5, "base_setpoint_c": 21.0},
        shift_fn=_recording_shift(seen),
    )
    assert seen[-1].start_k == pytest.approx(-0.5) and heating.thermal is not None


@pytest.mark.parametrize(("up_k", "max_k"), [(0.5, 3.0), (1.5, 1.0)])
def test_shift_outside_the_band_is_dropped(up_k: float, max_k: float) -> None:
    plan = _sample_plan(["10:00", "10:15"])
    seen: list[ShiftInput] = []
    with capture_logs() as logs:
        result = _apply(
            plan,
            {"mode": "heizen"},
            settings=ThermalPlanSettings(enabled=True, heat_up_k=up_k),
            shift_fn=_recording_shift(seen, steps=3),
            max_k=max_k,
        )
    assert seen[0].up_k == min(up_k, max_k)
    assert result.thermal is not None
    assert (result.thermal.reason, result.thermal.active, result.thermal.saving_ct) == (
        "solver",
        False,
        0.0,
    )
    assert all(s.thermal_steps is None for s in result.slots)
    assert "Verschiebung der Klimaanlage verworfen, weil sie unplausibel ist" in [
        e["event"] for e in logs
    ]


@pytest.mark.parametrize("reason", ["not_worth", "solver"])
def test_shift_without_result_leaves_the_slots(reason: str) -> None:
    plan = _sample_plan(["10:00", "10:15"])
    on = _apply(plan, {"mode": "heizen"}, shift_fn=_recording_shift([], reason=reason))
    assert on.thermal is not None and on.thermal.reason == reason
    assert on.slots == plan.slots
    off = _apply(
        plan,
        {"mode": "heizen"},
        settings=ThermalPlanSettings(),
        shift_fn=_recording_shift([], reason=reason),
    )
    assert off.thermal is not None and (off.thermal.reason, off.thermal.active) == ("off", False)


def test_model_with_unusable_values_is_no_model() -> None:
    plan = _sample_plan(["10:00"])
    bad = replace(heat_model(), tau_h=None)
    result = _apply(
        plan,
        {"mode": "heizen"},
        models=FakeModels(heizen=(bad, full_profile(), MODEL_AT)),
        shift_fn=_recording_shift([]),
    )
    assert result.thermal is not None and result.thermal.reason == "no_model"


# --- Auslöser „thermal“ ----------------------------------------------------------------------


def _stored_runs(service: PlanningService) -> int:
    plan = service.current()
    return plan.id or 0 if plan is not None else 0


async def test_mode_change_triggers_replan(service_with, live, clock, cold):
    """Heizen → Lüften plant mit „thermal“ neu; gleich danach Lüften → Heizen erst nach 5 min.
    Lüften → aus bleibt „Rest“ und löst nichts aus."""
    live.health = thermal_health("heizen")
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls), sleep=fast_sleep(), thermal_model=heating_models()
    )
    async with running(service):
        # auf den gespeicherten Plan warten: Die Verschiebung liest Health erst nach dem Solver
        await until(lambda: _stored_runs(service) == 1)
        live.health = thermal_health("lueften")
        await until(lambda: _stored_runs(service) == 2)
        live.health = thermal_health("aus")
        await asyncio.sleep(0.1)
        assert len(calls) == 2  # Lüften und aus sind beide „Rest“
        live.health = thermal_health("heizen")
        clock.advance(timedelta(seconds=60))
        await asyncio.sleep(0.1)
        assert len(calls) == 2  # gedrosselt
        clock.advance(timedelta(seconds=241))
        await until(lambda: _stored_runs(service) == 3)
        await asyncio.sleep(0.1)  # danach geplant: keine weitere Anfrage
    assert triggers(calls) == ["start", "thermal", "thermal"]
    plan = service.current()
    assert plan is not None and plan.thermal is not None and plan.thermal.mode == "heizen"


@pytest.mark.parametrize("change", ["mode", "model"])
async def test_thermal_change_is_requested_once_while_no_plan_comes(
    service_with, live, clock, cold, monkeypatch, change
):
    """Ergibt der Lauf „thermal“ keinen Plan (``PlanningUnavailable``), merkt sich
    ``_with_thermal`` nichts; dieselbe Änderung der Betriebsart (oder ein neues Wärmemodell)
    fordert trotzdem nur einmal an, nicht alle 5 min erneut."""
    live.health = thermal_health("heizen")
    calls: list[PlanInputs] = []
    models = FakeModels()
    service = service_with(solver=recording(calls), sleep=fast_sleep(), thermal_model=models)
    attempts: list[PlanTrigger] = []
    collect = service.collect

    async def unavailable_after_start(trigger: PlanTrigger) -> PlanInputs:
        if calls:
            attempts.append(trigger)
            raise PlanningUnavailable("Keine Preise")
        return await collect(trigger)

    monkeypatch.setattr(service, "collect", unavailable_after_start)
    checks = 0
    thermal_due = service._thermal_due

    def counted(now: datetime) -> bool:
        nonlocal checks
        checks += 1
        return thermal_due(now)

    monkeypatch.setattr(service, "_thermal_due", counted)
    async with running(service):
        await until(lambda: _stored_runs(service) == 1)
        if change == "mode":
            live.health = thermal_health("kuehlen")
        else:
            service.thermal_model_changed()
        await until(lambda: attempts == ["thermal"])
        clock.advance(timedelta(seconds=601))  # mehr als 5 min später: schon gesehen
        target = checks + 10
        await until(lambda: checks >= target)
    assert attempts.count("thermal") == 1  # die Viertelstunde („slot“) darf dazwischen planen
    assert triggers(calls) == ["start"]


async def test_missing_health_is_no_mode_change(service_with, live, cold):
    live.health = thermal_health("heizen")
    calls: list[PlanInputs] = []
    service = service_with(
        solver=recording(calls), sleep=fast_sleep(), thermal_model=heating_models()
    )
    async with running(service):
        await until(lambda: _stored_runs(service) == 1)
        live.health = None
        await asyncio.sleep(0.2)
    assert triggers(calls) == ["start"]


async def test_new_model_triggers_replan(service_with, live, clock, cold):
    live.health = thermal_health("heizen")
    calls: list[PlanInputs] = []
    models = FakeModels()
    service = service_with(solver=recording(calls), sleep=fast_sleep(), thermal_model=models)
    async with running(service):
        await until(lambda: _stored_runs(service) == 1)
        first = service.current()
        assert first is not None and first.thermal is not None
        assert (first.thermal.reason, first.thermal.model_computed_at) == ("off", None)
        models.states = heating_models().states
        service.thermal_model_changed()
        await until(lambda: _stored_runs(service) == 2)
        service.thermal_model_changed()
        await asyncio.sleep(0.1)
        assert len(calls) == 2  # höchstens alle 5 min
        clock.advance(timedelta(seconds=301))
        await until(lambda: _stored_runs(service) == 3)
    assert triggers(calls) == ["start", "thermal", "thermal"]
    plan = service.current()
    assert plan is not None and plan.thermal is not None
    assert plan.thermal.model_computed_at == MODEL_AT  # der Plan kennt das neue Modell


async def test_without_thermal_model_no_thermal_trigger(service_with, live):
    live.health = thermal_health("heizen")
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    service.thermal_model_changed()
    async with running(service):
        await until(lambda: len(calls) == 1)
        await asyncio.sleep(0.05)
        live.health = thermal_health("kuehlen")
        await asyncio.sleep(0.2)
    assert triggers(calls) == ["start"]


async def test_thermal_switch_replans_as_settings(service_with, settings_service):
    calls: list[PlanInputs] = []
    service = service_with(solver=recording(calls), sleep=fast_sleep())
    async with running(service):
        await until(lambda: len(calls) == 1)
        set_thermal(settings_service, enabled=True)
        await until(lambda: len(calls) == 2)
    assert triggers(calls) == ["start", "settings"]
