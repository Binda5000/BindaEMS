"""Live-Zustand für das UI: REST-Abfragen und ein WebSocket mit den Nachrichten des core.

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect

from bindaems.app.auth.service import SessionInfo
from bindaems.app.auth.web import Guard
from bindaems.app.core_link import LiveState
from bindaems.app.status import StatusSource, status_json

WS_UNAUTHORIZED = 4401
WS_FORBIDDEN_ORIGIN = 4403
SESSION_CHECK_S = 60.0


def live_router(
    live: LiveState,
    guard: Guard,
    sources: Sequence[StatusSource],
    warnings: Callable[[], list[str]],
) -> APIRouter:
    router = APIRouter()
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    def snapshot() -> dict[str, Any]:
        return {
            "core_connected": live.connected,
            "updated_at": live.updated_at.isoformat() if live.updated_at is not None else None,
            "state": live.state,
            "alarms": live.alarms,
        }

    @router.get("/api/state")
    def state(session: Viewer) -> dict[str, Any]:
        return snapshot()

    @router.get("/api/system")
    def system(session: Viewer) -> dict[str, Any]:
        return {
            "core": {"connected": live.connected, "health": live.health},
            "components": [status_json(source()) for source in sources],
            "warnings": warnings(),
        }

    @router.websocket("/api/live")
    async def live_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        origin = websocket.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != websocket.headers.get("host", ""):
            await websocket.close(code=WS_FORBIDDEN_ORIGIN)  # fremde Seite im Browser
            return
        if await guard.websocket_session(websocket) is None:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        hello = snapshot()
        del hello["updated_at"]
        await websocket.send_json({"type": "hello", "data": hello})
        async with live.subscribe() as messages:

            async def relay() -> None:
                async for message in messages:
                    await websocket.send_json(message)

            async def until_closed() -> None:
                while (await websocket.receive())["type"] != "websocket.disconnect":
                    pass

            tasks = {asyncio.create_task(relay()), asyncio.create_task(until_closed())}
            try:
                while True:
                    done, _ = await asyncio.wait(
                        tasks, timeout=SESSION_CHECK_S, return_when=asyncio.FIRST_COMPLETED
                    )
                    if done:
                        break  # Client weg oder Senden gescheitert
                    if await guard.websocket_session(websocket) is None:
                        await websocket.close(code=WS_UNAUTHORIZED)  # Sitzung abgelaufen
                        break
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(
                        asyncio.CancelledError, WebSocketDisconnect, RuntimeError
                    ):
                        await task

    return router
