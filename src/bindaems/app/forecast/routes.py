"""PV-Prognose für das UI.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.forecast.service import ForecastService
from bindaems.app.status import status_json
from bindaems.shared.timeutil import LOCAL_TZ, Clock

DAYS = 3  # heute, morgen, übermorgen


def forecast_router(service: ForecastService, clock: Clock, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/forecast")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("/pv")
    def pv(session: Viewer) -> dict[str, Any]:
        forecast = service.latest()
        today = clock.now().astimezone(LOCAL_TZ).date()
        days = [today + timedelta(days=offset) for offset in range(DAYS)]
        return {
            "issued_at": forecast.issued_at.isoformat() if forecast is not None else None,
            "source": forecast.source if forecast is not None else None,
            "slots": (
                [
                    {"start": slot.isoformat(), "p50_w": power_w}
                    for slot, power_w in sorted(forecast.p50_w.items())
                ]
                if forecast is not None
                else []
            ),
            "days": [
                {
                    "date": day.isoformat(),
                    "kwh": forecast.day_energy_kwh(day) if forecast is not None else None,
                }
                for day in days
            ],
            "status": status_json(service.component_status()),
        }

    return router
