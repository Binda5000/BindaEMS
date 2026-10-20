"""PV- und Lastprognose für das UI.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.quality import ForecastQuality
from bindaems.app.forecast.service import ForecastService
from bindaems.app.status import status_json
from bindaems.shared.timeutil import LOCAL_TZ, Clock

DAYS = 3  # heute, morgen, übermorgen
QUALITY_DAYS = 14
DAILY_DAYS = 28


def forecast_router(
    service: ForecastService,
    load: LoadForecastService,
    quality: ForecastQuality,
    clock: Clock,
    guard: Guard,
) -> APIRouter:
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
                    {
                        "start": slot.isoformat(),
                        "p50_w": power_w,
                        "p25_w": forecast.p25_w[slot],
                        "p75_w": forecast.p75_w[slot],
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
            "status": status_json(service.component_status()),
        }

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
