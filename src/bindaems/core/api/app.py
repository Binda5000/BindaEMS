"""Interne API: Gesundheit, Zustand und Live-Stream für die app, geschützt durch ein Token."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import fields
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, status
from pydantic import BaseModel, SecretStr

from bindaems.core.state.derived import Derived
from bindaems.shared.domain import Snapshot

WS_UNAUTHORIZED = 4401


class HealthReport(BaseModel):
    mode: Literal["OBSERVE"]
    version: str
    adapters: list[dict[str, Any]]
    selfcheck: list[dict[str, str]]
    alarms: list[dict[str, str]]
    cycle_ms_p95: float | None


class CoreView(Protocol):
    def health(self) -> HealthReport: ...

    def state(self) -> dict[str, Any]: ...

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]: ...


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def snapshot_to_json(snap: Snapshot, derived: Derived) -> dict[str, Any]:
    return {
        "ts": snap.ts.isoformat(),
        "signals": {
            signal: {"v": reading.value, "ts": reading.ts.isoformat(), "q": reading.quality.value}
            for signal, reading in snap.readings.items()
        },
        "derived": {field.name: _plain(getattr(derived, field.name)) for field in fields(derived)},
    }


def _bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, credentials = authorization.partition(" ")
    return credentials if scheme.lower() == "bearer" and credentials else None


def _matches(candidate: str | None, token: SecretStr) -> bool:
    if candidate is None:
        return False
    return hmac.compare_digest(candidate.encode(), token.get_secret_value().encode())


def create_api(view: CoreView, token: SecretStr) -> FastAPI:
    app = FastAPI(title="BindaEMS core", docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if not _matches(_bearer(authorization), token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token fehlt oder ist ungültig",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> HealthReport:
        return view.health()

    @app.get("/v1/state", dependencies=[Depends(require_token)])
    def state() -> dict[str, Any]:
        return view.state()

    @app.websocket("/v1/stream")
    async def stream(websocket: WebSocket) -> None:
        # nur per Header: ein Token in der URL landete in den Zugriffslogs
        candidate = _bearer(websocket.headers.get("authorization"))
        # erst annehmen, dann schließen: sonst sähe ein echter Client nur HTTP 403 statt 4401
        await websocket.accept()
        if not _matches(candidate, token):
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        async with view.subscribe() as messages:
            async for message in messages:
                await websocket.send_json(message)
        await websocket.close()

    return app
