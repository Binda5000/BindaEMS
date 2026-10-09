"""Verlaufs-API für Diagramme im UI.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.history.influx import RP_LONG, RP_RAW, InfluxQueryError, InfluxReader
from bindaems.app.history.series import (
    LONG_MIN_STEP_S,
    SeriesSpec,
    build_query,
    choose_rp_and_step,
)
from bindaems.shared.timeutil import Clock

MAX_SERIES = 8
MAX_SPAN = timedelta(days=400)


def _bad_request(text: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, text)


def history_router(
    reader: InfluxReader, catalog: Mapping[str, SeriesSpec], clock: Clock, guard: Guard
) -> APIRouter:
    router = APIRouter(prefix="/api/history")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("")
    async def history(
        session: Viewer,
        series: Annotated[str, Query(max_length=400)],
        start: Annotated[datetime, Query(alias="from")],
        end: Annotated[datetime, Query(alias="to")],
    ) -> dict[str, Any]:
        ids = [item.strip() for item in series.split(",") if item.strip()]
        if not ids or len(ids) > MAX_SERIES:
            raise _bad_request(f"1 bis {MAX_SERIES} Reihen angeben")
        for series_id in ids:
            if series_id not in catalog:
                raise _bad_request(f"Unbekannte Reihe: {series_id}")
        if start.tzinfo is None or end.tzinfo is None:
            raise _bad_request("Zeitangaben brauchen eine Zeitzone, z. B. …Z")
        if start >= end:
            raise _bad_request("Der Beginn muss vor dem Ende liegen")
        if end - start > MAX_SPAN:
            raise _bad_request("Der Zeitraum darf höchstens 400 Tage umfassen")
        specs = [catalog[series_id] for series_id in ids]
        # eine gemeinsame Auflösung für alle Reihen, damit die Diagramme zusammenpassen
        choices = [choose_rp_and_step(spec, start, end, clock.now()) for spec in specs]
        rp = RP_RAW if all(choice[0] == RP_RAW for choice in choices) else RP_LONG
        step = max(choice[1] for choice in choices)
        if rp == RP_LONG:
            step = max(step, LONG_MIN_STEP_S)
        result: dict[str, Any] = {}
        for spec in specs:
            try:
                rows = await reader.query(build_query(spec, rp, start, end, step))
            except InfluxQueryError as exc:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, "InfluxDB nicht erreichbar"
                ) from exc
            points = [
                [row[0], row[1] * spec.scale]
                for item in rows
                for row in item.values
                if len(row) >= 2 and row[1] is not None
            ]
            result[spec.id] = {"label": spec.label, "unit": spec.unit, "points": points}
        return {"rp": rp, "step_s": step, "series": result}

    @router.get("/catalog")
    def catalog_list(session: Viewer) -> list[dict[str, str]]:
        return [
            {"id": spec.id, "label": spec.label, "unit": spec.unit} for spec in catalog.values()
        ]

    return router
