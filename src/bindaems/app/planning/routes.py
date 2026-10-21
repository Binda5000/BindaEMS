"""Plan-API: Plan lesen, neu planen, Netzladen schalten (Plan 3b, Task 11).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.core_link import LiveState
from bindaems.app.ledger.service import LedgerService
from bindaems.app.planning.execution import execution_state, execution_text
from bindaems.app.planning.explain import slot_text
from bindaems.app.planning.review import review
from bindaems.app.planning.service import PlanningService
from bindaems.app.planning.store import KEEP_PLANS, PlanStore
from bindaems.app.settings.service import SettingsService, VersionConflictError
from bindaems.shared.plan import Plan, PlanSlot, PlanVehicle
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, local_day_slots


class GridChargeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    on: bool


def _euro(ct: float) -> float:
    return round(ct / 100, 2)


def slot_json(slot: PlanSlot) -> dict[str, Any]:
    return {
        "start": slot.start.isoformat(),
        "intent": slot.intent.kind.value,
        "grid_charge_w": slot.intent.grid_charge_w,
        "soc_pct": slot.soc_end_pct,
        "pv_w": slot.pv_w,
        "load_w": slot.load_w,
        "import_w": slot.import_w,
        "export_w": slot.export_w,
        "battery_w": slot.charge_pv_w + slot.charge_grid_w - slot.discharge_w,
        "import_ct": slot.import_ct,
        "feed_in_ct": slot.feed_in_ct,
        "estimated": slot.estimated,
        "budget_kw": slot.budget_kw,
        "support_soc_pct": slot.support_soc_pct,
        "ev_w": dict(slot.ev_w),
        "codes": [reason.code for reason in slot.reasons],
        "text": slot_text(slot),
    }


def vehicle_json(car: PlanVehicle) -> dict[str, Any]:
    return {
        "key": car.key,
        "name": car.name,
        "wallbox": car.wallbox,
        "target_soc_pct": car.target_soc_pct,
        "departure_at": car.departure_at.isoformat(),
        "soc_start_pct": car.soc_start_pct,
        "soc_assumed": car.soc_assumed,
        "limit_pct": car.limit_pct,
        "need_kwh": car.need_kwh,
        "planned_kwh": car.planned_kwh,
        "shortfall_kwh": car.shortfall_kwh,
        "expected_soc_pct": car.expected_soc_pct,
        "finish_at": car.finish_at.isoformat() if car.finish_at is not None else None,
        "reachable": car.reachable,
        "expected": car.expected,
        "arrival_at": car.arrival_at.isoformat() if car.arrival_at is not None else None,
    }


def plan_json(plan: Plan, *, valid: bool) -> dict[str, Any]:
    return {
        "id": plan.id,
        "created_at": plan.created_at.isoformat(),
        "trigger": plan.trigger,
        "source": plan.source,
        "solver_status": plan.solver_status,
        "fallback_reason": plan.fallback_reason,
        "valid_until": plan.valid_until.isoformat(),
        "valid": valid,
        "horizon_end": plan.horizon_end.isoformat(),
        "reserve_pct": plan.reserve_pct,
        "soc_max_pct": plan.soc_max_pct,
        "month_peak_kw": plan.month_peak_kw,
        "power_price": plan.power_price,
        "expected_cost_eur": _euro(plan.expected_cost_ct),
        "baseline_cost_eur": _euro(plan.baseline_cost_ct),
        "issues": list(plan.issues),
        "vehicles": [vehicle_json(car) for car in plan.vehicles],
        "slots": [slot_json(slot) for slot in plan.slots],
    }


def planning_router(
    service: PlanningService,
    settings: SettingsService,
    live: LiveState,
    clock: Clock,
    guard: Guard,
    *,
    ledger: LedgerService,
    store: PlanStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/plan")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Operator = Annotated[SessionInfo, Depends(guard.require("operator"))]

    @router.get("")
    def get_plan(session: Viewer) -> dict[str, Any]:
        component = service.component_status()
        plan = service.current()
        valid = plan is not None and plan.valid_at(clock.now())
        allowed = settings.current().settings.planning.grid_charge_allowed
        state = execution_state(
            plan,
            valid=valid,
            connected=live.connected,
            health=live.health,
            grid_charge_allowed=allowed,
        )
        text = execution_text(state, live.health)
        return {
            "status": {
                "ok": component.ok,
                "message": component.message,
                "running": service.status().running,
            },
            "grid_charge_allowed": allowed,
            "plan": plan_json(plan, valid=valid) if plan is not None else None,
            "execution": {"state": state, "text": text} if text is not None else None,
        }

    @router.get("/review")
    def get_review(session: Viewer, day: Annotated[date | None, Query()] = None) -> dict[str, Any]:
        """Plan gegen Ist je vergangener Viertelstunde des lokalen Tages (Standard heute); Tage
        vor den letzten 30 Tagen (so lange hält die Planablage) und künftige Tage gibt es nicht."""
        now = clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        wanted = day or today
        if wanted > today or today - wanted > KEEP_PLANS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Nur die letzten {KEEP_PLANS.days} Tage bis heute",
            )
        start = local_day_slots(wanted)[0]
        end = min(now, local_day_slots(wanted)[-1] + SLOT)
        plans = store.plans_between(start, end)
        return review(wanted, plans, ledger.slots(start, end), now)

    @router.post("/replan", status_code=status.HTTP_202_ACCEPTED)
    def replan(session: Operator) -> dict[str, bool]:
        service.request("manual")
        return {"queued": True}

    @router.put("/grid-charge")
    def grid_charge(body: GridChargeBody, session: Operator) -> dict[str, Any]:
        try:
            version = service.set_grid_charge(body.on, actor=session.user.username, source="ui")
        except VersionConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {
            "grid_charge_allowed": version.settings.planning.grid_charge_allowed,
            "settings_version": version.version,
        }

    return router
