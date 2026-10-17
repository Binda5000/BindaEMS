"""Statistik-API: ``GET /api/stats?period=day|week|month|year[&date=JJJJ-MM-TT]``.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.ledger.routes import _rounded
from bindaems.app.stats.service import Period, StatsService


def _rounded_deep(value: Any) -> Any:
    """Rundung wie in ``ledger/routes.py`` (3 Stellen), auch in Listen."""
    if isinstance(value, list):
        return [_rounded_deep(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _rounded_deep(item) for key, item in value.items()}
    return _rounded(value)


def stats_router(service: StatsService, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/stats")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("")
    def stats(
        session: Viewer,
        period: Period,
        day: Annotated[date | None, Query(alias="date")] = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = _rounded_deep(service.period(period, day or service.local_today()))
        return result

    return router
