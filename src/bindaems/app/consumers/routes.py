"""Endpunkte der Verbraucher: Baum mit Live-Werten für alle, Änderungen nur für Admins.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.consumers.service import (
    MAX_ID,
    Consumer,
    ConsumerError,
    ConsumerInput,
    ConsumerService,
)
from bindaems.app.consumers.values import (
    HaValueCache,
    build_tree,
    candidates,
    consumer_note,
    consumer_power,
)
from bindaems.app.core_link import LiveState
from bindaems.app.history.influx import InfluxReader
from bindaems.shared.config import Config

ConsumerId = Annotated[int, Path(ge=1, le=MAX_ID)]


def consumer_json(consumer: Consumer) -> dict[str, Any]:
    return asdict(consumer)


def consumers_router(
    service: ConsumerService,
    live: LiveState,
    ha: HaValueCache | None,
    reader: InfluxReader | None,
    cfg: Config,
    guard: Guard,
) -> APIRouter:
    router = APIRouter(prefix="/api/consumers")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("")
    def read(session: Viewer) -> dict[str, Any]:
        consumers = service.list()
        house = live.derived().get("house_load_w")
        house_w = float(house) if isinstance(house, int | float) else None
        tree = build_tree(
            consumers,
            lambda c: consumer_power(c, live, ha),
            house_w,
            lambda c: consumer_note(c, ha),
        )
        return {"tree": asdict(tree), "consumers": [consumer_json(c) for c in consumers]}

    @router.get("/candidates")
    async def list_candidates(session: Admin) -> dict[str, Any]:
        return await candidates(cfg, live, reader)

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create(body: ConsumerInput, session: Admin) -> dict[str, Any]:
        try:
            consumer = service.create(body, actor=session.user.username, source="ui")
        except ConsumerError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return consumer_json(consumer)

    @router.patch("/{consumer_id}")
    def change(consumer_id: ConsumerId, body: ConsumerInput, session: Admin) -> dict[str, Any]:
        try:
            consumer = service.update(consumer_id, body, actor=session.user.username, source="ui")
        except ConsumerError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return consumer_json(consumer)

    @router.delete("/{consumer_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(consumer_id: ConsumerId, session: Admin) -> None:
        try:
            service.delete(consumer_id, actor=session.user.username, source="ui")
        except ConsumerError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    return router
