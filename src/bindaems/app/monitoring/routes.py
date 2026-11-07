"""``GET /api/backup``: USV-Reichweite, Netzausfälle und Lebenszeichen für UI und HA.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.monitoring.backup_range import BackupRange, LoadBasis
from bindaems.app.monitoring.heartbeat import Heartbeat
from bindaems.app.monitoring.outages import Outage, OutageTracker
from bindaems.shared.timeutil import Clock


def _outage_json(outage: Outage, now: Any) -> dict[str, Any]:
    end = outage.end if outage.end is not None else now
    return {
        "start": outage.start.isoformat(),
        "end": outage.end.isoformat() if outage.end is not None else None,
        "end_estimated": outage.end_estimated,
        "duration_s": max(int((end - outage.start).total_seconds()), 0),
        "soc_start": outage.soc_start,
        "soc_end": outage.soc_end,
        "soc_min": outage.soc_min,
        "mean_w": outage.mean_w,
        "load_kwh": outage.load_wh / 1000.0,
    }


def backup_router(
    *,
    range_now: Callable[[], BackupRange],
    basis: Callable[[], LoadBasis | None],
    outages: OutageTracker,
    heartbeat: Heartbeat,
    floor_pct: float,
    clock: Clock,
    guard: Guard,
) -> APIRouter:
    router = APIRouter(prefix="/api/backup")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.get("")
    def backup(session: Viewer) -> dict[str, Any]:
        reach = range_now()
        load = basis()
        current = outages.current()
        now = clock.now()
        ping = heartbeat.ping_info()
        return {
            "now_h": reach.now_h,
            "reserve_h": reach.reserve_h,
            "night_h": reach.night_h,
            "load_w": reach.load_w,
            "load_source": reach.load_source,
            "floor_soc_pct": floor_pct,
            "basis": (
                None
                if load is None
                else {
                    "first_day": load.first_day.isoformat(),
                    "last_day": load.last_day.isoformat(),
                    "day_slots": load.day_slots,
                    "night_slots": load.night_slots,
                    "night_w": load.night_w,
                }
            ),
            "outage": (
                None
                if current is None
                else {
                    "start": current.start.isoformat(),
                    "soc_start": current.soc_start,
                    "mean_w": current.mean_w,
                }
            ),
            "outages": [_outage_json(o, now) for o in outages.recent()],
            "ping": {
                "configured": ping.configured,
                "ok": ping.ok,
                "last_sent": ping.last_sent.isoformat() if ping.last_sent is not None else None,
                "text": ping.text,
            },
        }

    return router
