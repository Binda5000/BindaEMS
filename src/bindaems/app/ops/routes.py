"""Routen des Betriebschecks (``/api/ops``).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.core_link import LiveState
from bindaems.app.ops.check import DecisionRates, OpsCheck, check_json, operations_check
from bindaems.app.ops.findings import Finding
from bindaems.app.ops.service import FindingsService, ReportSender, finding_json
from bindaems.app.status import StatusSource
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

RATE_WINDOW = timedelta(hours=1)
FINDINGS_DAYS_MAX = 60


def ops_router(
    *,
    live: LiveState,
    guard: Guard,
    sources: Sequence[StatusSource],
    decisions: DecisionStore,
    clock: Clock,
    findings: FindingsService,
    report: ReportSender,
) -> APIRouter:
    router = APIRouter(prefix="/api/ops")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("/check")
    async def check(session: Viewer) -> dict[str, Any]:
        return check_json(await run_in_threadpool(current_check, live, sources, decisions, clock))

    @router.get("/findings")
    async def findings_by_day(
        session: Viewer,
        days: Annotated[int, Query(ge=1, le=FINDINGS_DAYS_MAX)] = 14,
    ) -> dict[str, Any]:
        """Die Auffälligkeiten der letzten ``days`` Tage bis gestern, neueste zuerst; ein Tag ohne
        Lauf hat ``computed`` falsch. ``report`` ist der Tagesbericht über gestern, auch ohne Home
        Assistant."""
        last = clock.now().astimezone(LOCAL_TZ).date() - timedelta(days=1)
        first = last - timedelta(days=days - 1)
        by_day = await run_in_threadpool(findings.days, first, last)
        listed = [last - timedelta(days=back) for back in range(days)]
        try:
            text = await run_in_threadpool(report.text, last)
            summary: dict[str, Any] | None = {"day": last.isoformat(), "text": text}
        except Exception:
            log.exception("Tagesbericht für die Betriebsseite nicht erstellt")
            summary = None
        return {"days": [_day_json(day, by_day.get(day)) for day in listed], "report": summary}

    return router


def current_check(
    live: LiveState,
    sources: Sequence[StatusSource],
    decisions: DecisionStore,
    clock: Clock,
) -> OpsCheck:
    """Der Betriebscheck von jetzt (blockierend: liest die Entscheidungsraten der Datenbank);
    ohne lesbare Raten sind sie ``unknown``. Route und Tagesbericht nutzen ihn gemeinsam."""
    now = clock.now()
    rates: DecisionRates | None
    try:
        rates = DecisionRates(decisions.counts(now - RATE_WINDOW))
    except Exception:
        log.exception("Entscheidungsraten für den Betriebscheck nicht lesbar")
        rates = None
    return operations_check(
        connected=live.connected,
        health=live.health,
        components=[source() for source in sources],
        rates=rates,
        now=now,
    )


def _day_json(day: date, found: Sequence[Finding] | None) -> dict[str, Any]:
    return {
        "day": day.isoformat(),
        "computed": found is not None,
        "findings": [finding_json(item) for item in found or []],
    }
