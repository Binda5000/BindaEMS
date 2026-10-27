"""Lastmodell, Fahrverhalten und Wärmemodell für das UI: Zustand, Tasten „Jetzt neu trainieren“ und
„Jetzt neu berechnen“, HA-Sensoren zur Auswahl.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.learning.driving_auto import DrivingAutomation
from bindaems.app.learning.driving_service import DrivingService
from bindaems.app.learning.ha_sensors import HaSensorReader
from bindaems.app.learning.service import LoadModelService
from bindaems.app.learning.thermal_service import ThermalModelService
from bindaems.app.settings.service import SettingsService

TRAINING_RUNNING = "Training läuft schon."
RECOMPUTE_RUNNING = "Berechnung läuft schon."
NO_THERMAL_DEVICE = "Keine Klimaanlage eingerichtet."


def learning_router(
    model: LoadModelService,
    sensors: HaSensorReader,
    load: LoadForecastService,
    guard: Guard,
    driving: DrivingService,
    automation: DrivingAutomation,
    thermal: ThermalModelService | None,
    settings: SettingsService,
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

    # ``async``: Dienst und Automatik ändern ihren Zustand in der Ereignisschleife; so liest ihn die
    # Antwort ohne Wettlauf
    @router.get("/driving")
    async def driving_status(session: Viewer) -> dict[str, Any]:
        """Fahrverhalten je Auto (Präzisierung 11): der Zustand des Dienstes, dazu je Auto die
        Automatik."""
        body = driving.status()
        for vehicle in body["vehicles"]:
            vehicle["automation"] = automation.state_json(vehicle["key"])
        return body

    # ``async``: die Berechnung braucht die laufende Ereignisschleife
    @router.post("/driving/recompute", status_code=status.HTTP_202_ACCEPTED)
    async def recompute(session: Admin) -> dict[str, bool]:
        if not driving.start_recompute():
            raise HTTPException(status.HTTP_409_CONFLICT, RECOMPUTE_RUNNING)
        return {"started": True}

    # ``async``: Dienst und Taste ändern ihren Zustand in der Ereignisschleife. Ohne Klimaanlage der
    # Art ``coe_offset`` gibt es den Dienst nicht (``thermal`` ist ``None``): 404
    @router.get("/thermal")
    async def thermal_status(session: Viewer) -> dict[str, Any]:
        """Wärmemodell je Betriebsart (Präzisierung 9): der Zustand des Dienstes, dazu der Schalter
        „Vorheizen und Vorkühlen“ aus den Einstellungen."""
        if thermal is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_THERMAL_DEVICE)
        enabled = settings.current().settings.learning.thermal.enabled
        return {"enabled": enabled} | thermal.status()

    # ``async``: die Berechnung braucht die laufende Ereignisschleife
    @router.post("/thermal/recompute", status_code=status.HTTP_202_ACCEPTED)
    async def thermal_recompute(session: Admin) -> dict[str, bool]:
        if thermal is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_THERMAL_DEVICE)
        if not thermal.start_run():
            raise HTTPException(status.HTTP_409_CONFLICT, RECOMPUTE_RUNNING)
        return {"started": True}

    @router.get("/candidates")
    async def candidates(session: Admin) -> dict[str, Any]:
        found, error = await sensors.candidates()
        return {
            "entities": [{"entity_id": item.entity_id, "unit": item.unit} for item in found],
            "error": error,
        }

    return router
