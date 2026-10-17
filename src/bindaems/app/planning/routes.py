"""Plan-API: Plan lesen, neu planen, Netzladen schalten (Plan 3b, Task 11).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.planning.explain import slot_text
from bindaems.app.planning.service import PlanningService
from bindaems.app.settings.service import SettingsService, VersionConflictError
from bindaems.shared.plan import Plan, PlanSlot
from bindaems.shared.timeutil import Clock


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
        "codes": [reason.code for reason in slot.reasons],
        "text": slot_text(slot),
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
        "slots": [slot_json(slot) for slot in plan.slots],
    }


def planning_router(
    service: PlanningService, settings: SettingsService, clock: Clock, guard: Guard
) -> APIRouter:
    router = APIRouter(prefix="/api/plan")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Operator = Annotated[SessionInfo, Depends(guard.require("operator"))]

    @router.get("")
    def get_plan(session: Viewer) -> dict[str, Any]:
        component = service.component_status()
        plan = service.current()
        return {
            "status": {
                "ok": component.ok,
                "message": component.message,
                "running": service.status().running,
            },
            "grid_charge_allowed": settings.current().settings.planning.grid_charge_allowed,
            "plan": plan_json(plan, valid=plan.valid_at(clock.now())) if plan is not None else None,
        }

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
