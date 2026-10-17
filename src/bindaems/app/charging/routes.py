"""API der Ladesessions: ``GET /api/charging/sessions``.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.charging.sessions import (
    UNKNOWN,
    ChargingSession,
    SessionService,
    vehicle_name,
    wallbox_name,
)
from bindaems.app.ledger.routes import _check_days, _rounded
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config

MAX_SESSIONS = 200


def session_json(
    session: ChargingSession, cfg: Config, wallbox_names: dict[str, str]
) -> dict[str, Any]:
    vehicle = session.vehicle or UNKNOWN
    split = session.split
    return {
        "id": session.id,
        "wallbox": session.wallbox,
        "wallbox_name": wallbox_name(cfg, wallbox_names, session.wallbox),
        "vehicle": vehicle,
        "vehicle_name": vehicle_name(cfg, vehicle),
        "start": session.start.isoformat(),
        "end": session.end.isoformat() if session.end is not None else None,
        "open": session.end is None,
        **_rounded(
            {
                "kwh": split.kwh,
                "pv_kwh": split.pv_kwh,
                "battery_kwh": split.battery_kwh,
                "grid_kwh": split.grid_kwh,
                "cost_eur": split.cost_eur,
            }
        ),
    }


def charging_router(
    service: SessionService, cfg: Config, settings: SettingsService, guard: Guard
) -> APIRouter:
    router = APIRouter(prefix="/api/charging")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("/sessions")
    def sessions(
        session: Viewer,
        first: Annotated[date, Query(alias="from")],
        last: Annotated[date, Query(alias="to")],
        vehicle: str | None = None,
    ) -> list[dict[str, Any]]:
        _check_days(first, last)
        found = service.sessions(first, last, vehicle, limit=MAX_SESSIONS)
        names = settings.current().settings.wallbox_names
        return [session_json(item, cfg, names) for item in found]

    return router
