"""Tests des Planungslaufs: Eingangsdaten, Lösen, Plausibilität, Ersatzplan, Speicherung und
Status (Plan 3b, Task 9)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from tests.app_helpers import ingest
from tests.plan_helpers import (
    flows_of,
    live_signals,
    month,
    resimulate,
    returns,
    scenario,
    small_battery,
)

from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import OptimizerResult, solve
from bindaems.app.planning.service import PlanningStatus, plan_once
from bindaems.shared.plan import BatteryIntent

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
