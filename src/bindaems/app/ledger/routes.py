"""Abrechnungs-API: Tageswerte, Slots und Neubewertung.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.ledger.service import DaySummary, LedgerService, LedgerSlot

MAX_DAYS = 400
MAX_SLOT_SPAN = timedelta(days=7)
DIGITS = 3


class RepriceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    first: Annotated[date, Field(alias="from")]
    last: Annotated[date, Field(alias="to")]


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, DIGITS)
    if isinstance(value, Mapping):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def day_json(day: DaySummary) -> dict[str, Any]:
    data = {key: _rounded(value) for key, value in asdict(day).items()}
    data["date"] = day.date.isoformat()
    return data


def slot_json(slot: LedgerSlot) -> dict[str, Any]:
    return {
        "slot_start": slot.slot_start.isoformat(),
        "covered_s": slot.covered_s,
        "flows_wh": dict(slot.flows_wh),
        "counters": {signal: list(bounds) for signal, bounds in slot.counters.items()},
        "import_wh": slot.import_wh,
        "export_wh": slot.export_wh,
        "import_price_ct": slot.import_price_ct,
        "origin": slot.origin,
    }


def _bad_request(text: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, text)


def _check_days(first: date, last: date) -> None:
    if first > last:
        raise _bad_request("Der Beginn muss vor dem Ende liegen")
    if (last - first).days + 1 > MAX_DAYS:
        raise _bad_request(f"Höchstens {MAX_DAYS} Tage auf einmal")


def ledger_router(service: LedgerService, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/ledger")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("/days")
    def days(
        session: Viewer,
        first: Annotated[date, Query(alias="from")],
        last: Annotated[date, Query(alias="to")],
    ) -> list[dict[str, Any]]:
        _check_days(first, last)
        return [day_json(day) for day in service.days(first, last)]

    @router.get("/slots")
    def slots(
        session: Viewer,
        start: Annotated[datetime, Query(alias="from")],
        end: Annotated[datetime, Query(alias="to")],
    ) -> list[dict[str, Any]]:
        if start.tzinfo is None or end.tzinfo is None:
            raise _bad_request("Zeitangaben brauchen eine Zeitzone, z. B. …Z")
        if start >= end:
            raise _bad_request("Der Beginn muss vor dem Ende liegen")
        if end - start > MAX_SLOT_SPAN:
            raise _bad_request("Der Zeitraum darf höchstens 7 Tage umfassen")
        return [slot_json(slot) for slot in service.slots(start, end)]

    @router.post("/reprice")
    def reprice(body: RepriceBody, session: Admin) -> dict[str, int]:
        _check_days(body.first, body.last)
        count = service.reprice(body.first, body.last, actor=session.user.username, source="ui")
        return {"repriced": count}

    return router
