"""Tests des Planungslaufs: Eingangsdaten, Lösen, Plausibilität, Ersatzplan, Speicherung und
Status (Plan 3b, Task 9); Auslöser und Schleife des Dienstes (Task 10)."""

from __future__ import annotations

import asyncio
import contextlib
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
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.app.planning.service import PlanningService, PlanningStatus, plan_once
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.plan import BatteryIntent, PlanTrigger
from bindaems.shared.settings import PlanningSettings
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


def _driving_profile(soc: float = 40.0) -> VehicleProfile:
    """Anwesenheit 0,8 ab 14:00 Ortszeit an jedem Tag, Ladestand bei Ankunft ``soc``."""
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
        presence=[[0.0] * 56 + [0.8] * 40 for _ in range(7)],
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
