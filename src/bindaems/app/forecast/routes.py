"""PV- und Lastprognose für das UI.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

import asyncio
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.quality import ForecastQuality
from bindaems.app.forecast.service import NO_FORECAST, ForecastService, PvForecast
from bindaems.app.status import status_json
from bindaems.shared.timeutil import LOCAL_TZ, Clock

DAYS = 3  # heute, morgen, übermorgen
QUALITY_DAYS = 14
DAILY_DAYS = 28


class SnowBody(BaseModel):
    """Knopf der Seite „Prognosen“: ``covered`` „Schnee auf den Modulen“, ``free`` „Module sind
    frei“, ``null`` wieder Automatik."""

    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["covered", "free"] | None


def snow_json(forecast: PvForecast, override: Literal["covered", "free"] | None) -> dict[str, Any]:
    """Zustand der Kürzung bei Schnee: ``factor_pct`` ist der Anteil der Prognose jetzt (100 bei
    frei), ``override`` der wirkende Knopf (``None``: Automatik)."""
    snow = forecast.snow
    return {
        "state": snow.state,
        "factor_pct": round(snow.factor * 100),
        "since": snow.since.isoformat() if snow.since is not None else None,
        "override": override,
    }


def forecast_router(
    service: ForecastService,
    load: LoadForecastService,
    quality: ForecastQuality,
    clock: Clock,
    guard: Guard,
) -> APIRouter:
    router = APIRouter(prefix="/api/forecast")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Operator = Annotated[SessionInfo, Depends(guard.require("operator"))]

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
                    {
                        "start": slot.isoformat(),
                        "p50_w": power_w,
                        "p25_w": forecast.p25_w[slot],
                        "p75_w": forecast.p75_w[slot],
                        "uncut_p50_w": forecast.uncut_p50_w[slot],
                    }
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
            "snow": (
                snow_json(forecast, service.snow_override()) if forecast is not None else None
            ),
            "status": status_json(service.component_status()),
        }

    @router.post("/pv/snow")
    async def pv_snow(body: SnowBody, session: Operator) -> dict[str, Any]:
        """Handschalter bei Schnee (Entscheidung 9); ohne Prognose 409."""
        done = await asyncio.to_thread(
            service.set_snow_override, body.mode, actor=session.user.username, source="ui"
        )
        forecast = service.latest()
        if not done or forecast is None:
            raise HTTPException(status.HTTP_409_CONFLICT, NO_FORECAST)
        return snow_json(forecast, service.snow_override())

    @router.get("/load")
    def load_forecast(session: Viewer) -> dict[str, Any]:
        forecast = load.latest()
        today = clock.now().astimezone(LOCAL_TZ).date()
        days = [today + timedelta(days=offset) for offset in range(DAYS)]
        return {
            "issued_at": forecast.issued_at.isoformat() if forecast is not None else None,
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
            "method": forecast.method if forecast is not None else "profile",
            "basis": (
                {"ledger": forecast.basis["ledger"], "ha": forecast.basis["ha"]}
                if forecast is not None
                else {"ledger": 0, "ha": 0}
            ),
            "status": status_json(load.component_status()),
        }

    @router.get("/quality")
    def forecast_quality(
        session: Viewer, days: Annotated[int, Query(ge=1, le=60)] = QUALITY_DAYS
    ) -> dict[str, Any]:
        """Kennzahlen der letzten ``days`` lokalen Tage einschließlich heute."""
        last = clock.now().astimezone(LOCAL_TZ).date()
        rows = quality.summary(last - timedelta(days=days - 1), last)
        return {
            "days": days,
            "rows": [
                {
                    "kind": row.kind,
                    "horizon": row.horizon,
                    "n": row.n,
                    "mae_w": row.mae_w,
                    "bias_w": row.bias_w,
                    "rel_mae_pct": row.rel_mae_pct,
                    "improvement_pct": row.improvement_pct,
                }
                for row in rows
            ],
        }

    @router.get("/quality/daily")
    def forecast_quality_daily(
        session: Viewer, days: Annotated[int, Query(ge=1, le=60)] = DAILY_DAYS
    ) -> dict[str, Any]:
        """MAE je lokalem Tag und Art der letzten ``days`` Tage einschließlich heute."""
        last = clock.now().astimezone(LOCAL_TZ).date()
        rows = quality.daily(last - timedelta(days=days - 1), last)
        return {
            "days": days,
            "rows": [
                {"date": row.day.isoformat(), "kind": row.kind, "n": row.n, "mae_w": row.mae_w}
                for row in rows
            ],
        }

    return router
