"""Routen des Betriebschecks (``/api/ops``).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.core_link import LiveState
from bindaems.app.ops.check import DecisionRates, check_json, operations_check
from bindaems.app.status import StatusSource
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

RATE_WINDOW = timedelta(hours=1)


def ops_router(
    *,
    live: LiveState,
    guard: Guard,
    sources: Sequence[StatusSource],
    decisions: DecisionStore,
    clock: Clock,
) -> APIRouter:
    router = APIRouter(prefix="/api/ops")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("/check")
    async def check(session: Viewer) -> dict[str, Any]:
        now = clock.now()
        rates: DecisionRates | None
        try:
            counts = await run_in_threadpool(decisions.counts, now - RATE_WINDOW)
            rates = DecisionRates(counts)
        except Exception:
            log.exception("Entscheidungsraten für den Betriebscheck nicht lesbar")
            rates = None
        result = operations_check(
            connected=live.connected,
            health=live.health,
            components=[source() for source in sources],
            rates=rates,
            now=now,
        )
        return check_json(result)

    return router
