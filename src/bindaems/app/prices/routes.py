"""Preis-API: Preise je Slot, aktueller Preis mit den nächsten 3 h, Abruf auf Anforderung.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from datetime import datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.prices.pipeline import PricePipeline, PriceStatus
from bindaems.app.prices.service import PriceService, PriceView
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, slot_start

MAX_SPAN = timedelta(days=31)
DEFAULT_SPAN = timedelta(days=2)
NEXT_SLOTS = 12  # 3 h


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def view_json(view: PriceView) -> dict[str, Any]:
    return {
        "start": view.start.isoformat(),
        "spot_net_ct": view.spot_net_ct,
        "import_net_ct": view.import_net_ct,
        "import_gross_ct": view.import_gross_ct,
        "feed_in_ct": view.feed_in_ct,
        "origin": view.origin,
        "missing": list(view.missing),
    }


def status_json(price_status: PriceStatus) -> dict[str, Any]:
    detection = price_status.vat_detection
    return {
        "last_attempt": _iso(price_status.last_attempt),
        "last_success": _iso(price_status.last_success),
        "vat_mode": price_status.vat_mode,
        "vat_detection": (
            None
            if detection is None
            else {"result": detection.result, "ratio": detection.ratio, "slots": detection.slots}
        ),
        "days": [
            {"date": day.day.isoformat(), "origin": day.origin, "findings": day.findings}
            for day in price_status.days
        ],
        "errors": price_status.errors,
    }


def _bad_request(text: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, text)


def prices_router(
    service: PriceService, pipeline: PricePipeline, clock: Clock, guard: Guard
) -> APIRouter:
    router = APIRouter(prefix="/api/prices")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("")
    def prices(
        session: Viewer,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> dict[str, Any]:
        if start is None:
            today = clock.now().astimezone(LOCAL_TZ).date()
            start = datetime.combine(today, time(0), tzinfo=LOCAL_TZ)
        if end is None:
            end = start + DEFAULT_SPAN
        if start.tzinfo is None or end.tzinfo is None:
            raise _bad_request("Zeitangaben brauchen eine Zeitzone, z. B. …Z")
        if start >= end:
            raise _bad_request("Der Beginn muss vor dem Ende liegen")
        if end - start > MAX_SPAN:
            raise _bad_request("Der Zeitraum darf höchstens 31 Tage umfassen")
        return {
            "status": status_json(pipeline.status()),
            "slots": [view_json(view) for view in service.slots(start, end)],
        }

    @router.get("/now")
    def now(session: Viewer) -> dict[str, Any]:
        current = slot_start(clock.now())
        views = service.slots(current, current + SLOT * (NEXT_SLOTS + 1))
        present = [view for view in views if view.start == current]
        upcoming = [view for view in views if view.start > current][:NEXT_SLOTS]
        return {
            "now": view_json(present[0]) if present else None,
            "next_3h": [view_json(view) for view in upcoming],
        }

    @router.post("/refresh")
    async def refresh(session: Admin) -> dict[str, Any]:
        return status_json(await pipeline.refresh())

    return router
