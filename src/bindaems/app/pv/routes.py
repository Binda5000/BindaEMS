"""Endpunkte der PV-Anlagen: Baum mit Live-Werten für alle, Änderungen nur für Admins.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.consumers.energy import EnergyCache, source_energy
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
from bindaems.app.pv.service import PvSource, PvSourceError, PvSourceInput, PvSourceService
from bindaems.app.pv.tree import build_pv_tree
from bindaems.shared.config import Config

PvId = Annotated[int, Path(ge=1, le=MAX_ID)]
PV_DC_SIGNAL = "pv_dc.power_w"


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
) -> APIRouter:
    router = APIRouter(prefix="/api/pv")
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
