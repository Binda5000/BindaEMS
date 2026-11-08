"""Endpunkte der PV-Anlagen: Baum mit Live-Werten für alle, Änderungen nur für Admins.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from dataclasses import asdict
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.consumers.days import SourceDays
from bindaems.app.consumers.energy import UNREADABLE, EnergyCache, source_energy
from bindaems.app.consumers.values import (
    HaValueCache,
    ha_candidates,
    number,
    source_note,
    source_power,
)
from bindaems.app.core_link import LiveState
from bindaems.app.db.fields import MAX_ID
from bindaems.app.history.influx import InfluxReader
from bindaems.app.pv.curve import day_curve
from bindaems.app.pv.periods import SourceEnergy, buckets, period_energy, unassigned
from bindaems.app.pv.service import PvSource, PvSourceError, PvSourceInput, PvSourceService
from bindaems.app.pv.tree import build_pv_tree
from bindaems.app.stats.service import Period, period_range
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ, Clock, SystemClock

PvId = Annotated[int, Path(ge=1, le=MAX_ID)]
PV_DC_SIGNAL = "pv_dc.power_w"


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day is not None else None


def pv_json(source: PvSource) -> dict[str, Any]:
    return asdict(source)


def pv_router(
    service: PvSourceService,
    live: LiveState,
    ha: HaValueCache | None,
    reader: InfluxReader | None,
    cfg: Config,
    guard: Guard,
    energy: EnergyCache | None = None,
    days: SourceDays | None = None,
    clock: Clock | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/pv")
    now = clock or SystemClock()

    def today() -> date:
        return now.now().astimezone(LOCAL_TZ).date()

    def checked(day: date | None) -> date:
        """``day`` (Standard heute); ein Tag in der Zukunft ist ungültig (422)."""
        current = today()
        if day is not None and day > current:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Tag liegt in der Zukunft")
        return day or current

    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("")
    def read(session: Viewer) -> dict[str, Any]:
        sources = service.list()
        total = number(live.derived().get("pv_total_w"))
        total_kwh = energy.pv_total_kwh() if energy is not None else None
        tree = build_pv_tree(
            sources,
            lambda s: source_power(s, live, ha),
            lambda s: source_note(s, ha),
            lambda s: source_energy(s, ha, energy),
            total,
            total_kwh,
        )
        since = energy.since if energy is not None else None
        return {
            "total_w": total,
            "total_kwh": total_kwh,
            "tree": [asdict(node) for node in tree],
            "sources": [pv_json(s) for s in sources],
            "energy_since": since.isoformat() if since is not None else None,
        }

    @router.get("/day")
    async def read_day(
        session: Viewer, day_: Annotated[date | None, Query(alias="date")] = None
    ) -> dict[str, Any]:
        day = checked(day_)
        sources = service.list()
        curves = await day_curve(reader, cfg.influxdb.ha_database, sources, day) if reader else None
        series = [
            {
                "id": s.id,
                "points": [[ms, w] for ms, w in (curves[s.id] if curves else [])],
                "note": curves.notes.get(s.id) if curves else UNREADABLE,
            }
            for s in sources
        ]
        return {"date": day.isoformat(), "series": series}

    @router.get("/period")
    def read_period(
        session: Viewer, period: Period = "day", day: date | None = None
    ) -> dict[str, Any]:
        chosen = checked(day)
        first, last = period_range(period, chosen)
        sources = service.list()
        now_day = today()
        stored = days.daily(first, min(last, now_day - timedelta(days=1))) if days else {}
        todays: dict[int, float | None] = {
            s.id: energy.energy_kwh(s) if energy is not None else None for s in sources
        }
        todays[0] = energy.pv_total_kwh() if energy is not None else None
        parts = buckets(period, first, last)
        found = period_energy(stored, todays, parts, now_day)
        kwp = {s.id: s.kwp for s in sources}

        def note(source: PvSource, found_: SourceEnergy, has_today: bool) -> str | None:
            if found_.sum_kwh is not None:
                return None
            live_note = energy.note(source) if energy is not None and has_today else None
            return live_note or found_.note

        def per_kwp(sid: int) -> float | None:
            total, size = found[sid].sum_kwh, kwp[sid]
            return round(total / size, 3) if total is not None and size else None

        top = [found[s.id] for s in sources if s.parent_id is None]
        rest, rest_sum = unassigned(found[0], top) if top else ([], None)
        return {
            "period": period,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "buckets": [{"start": b.start.isoformat(), "end": b.end.isoformat()} for b in parts],
            "total": {"kwh": found[0].kwh, "sum_kwh": found[0].sum_kwh},
            "unassigned": {"kwh": rest, "sum_kwh": rest_sum} if top else None,
            "sources": [
                {
                    "id": s.id,
                    "kwh": found[s.id].kwh,
                    "sum_kwh": found[s.id].sum_kwh,
                    "kwh_per_kwp": per_kwp(s.id),
                    "first_known": _iso(found[s.id].first_known),
                    "note": note(s, found[s.id], first <= now_day <= last),
                }
                for s in sources
            ],
        }

    @router.get("/candidates")
    async def list_candidates(session: Admin) -> dict[str, Any]:
        signals = live.state.get("signals") if live.state is not None else None
        core = {
            name
            for name in signals or {}
            if name.startswith("pv.") and name.endswith(".power_w") and name.count(".") == 2
        }
        core.add(PV_DC_SIGNAL)
        found, error = await ha_candidates(cfg, reader)
        return {"core": sorted(core), "ha": found, "ha_error": error}

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create(body: PvSourceInput, session: Admin) -> dict[str, Any]:
        try:
            created = service.create(body, actor=session.user.username, source="ui")
        except PvSourceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return pv_json(created)

    @router.patch("/{source_id}")
    def change(source_id: PvId, body: PvSourceInput, session: Admin) -> dict[str, Any]:
        try:
            changed = service.update(source_id, body, actor=session.user.username, source="ui")
        except PvSourceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return pv_json(changed)

    @router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(source_id: PvId, session: Admin) -> None:
        try:
            service.delete(source_id, actor=session.user.username, source="ui")
        except PvSourceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    return router
