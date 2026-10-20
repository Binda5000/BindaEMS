"""Lastmodell für das UI: Zustand, Taste „Jetzt neu trainieren“, HA-Sensoren zur Auswahl.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.learning.ha_sensors import HaSensorReader
from bindaems.app.learning.service import LoadModelService

TRAINING_RUNNING = "Training läuft schon."


def learning_router(
    model: LoadModelService,
    sensors: HaSensorReader,
    load: LoadForecastService,
    guard: Guard,
) -> APIRouter:
    router = APIRouter(prefix="/api/learning")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("/load")
    def load_status(session: Viewer) -> dict[str, Any]:
        state = model.status()
        forecast = load.latest()
        method = forecast.method if forecast is not None else "profile"
        # Reihenfolge wie in Präzisierung 7: das Verfahren steht gleich nach dem Schalter
        return {"enabled": state["enabled"], "method": method} | {
            key: value for key, value in state.items() if key != "enabled"
        }

    # ``async``: das Training braucht die laufende Ereignisschleife
    @router.post("/load/train", status_code=status.HTTP_202_ACCEPTED)
    async def train(session: Admin) -> dict[str, bool]:
        if not model.start_training():
            raise HTTPException(status.HTTP_409_CONFLICT, TRAINING_RUNNING)
        return {"started": True}

    @router.get("/candidates")
    async def candidates(session: Admin) -> dict[str, Any]:
        found, error = await sensors.candidates()
        return {
            "entities": [{"entity_id": item.entity_id, "unit": item.unit} for item in found],
            "error": error,
        }

    return router
