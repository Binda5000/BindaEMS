"""Interne API: Gesundheit, Zustand, Absicht und Live-Stream für die app, mit Token geschützt."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import fields
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, status
from pydantic import BaseModel, SecretStr

from bindaems.core.control.intent import IntentReply
from bindaems.core.state.derived import Derived
from bindaems.shared.control import Intent
from bindaems.shared.domain import Snapshot

WS_UNAUTHORIZED = 4401

# Starlette hat den Namen für 422 geändert (ENTITY zu CONTENT); die Zahl gilt in jeder Version.
HTTP_UNPROCESSABLE = 422


class HealthReport(BaseModel):
    mode: Literal["OBSERVE"]
    version: str
    adapters: list[dict[str, Any]]
    selfcheck: list[dict[str, str]]
    alarms: list[dict[str, str]]
    cycle_ms_p95: float | None


class CurrentIntent(BaseModel):
    intent: Intent | None


class IntentResponse(BaseModel):
    result: Literal["accepted", "clamped", "rejected"]
    version: int | None
    details: list[str]


class CoreView(Protocol):
    def health(self) -> HealthReport: ...

    def state(self) -> dict[str, Any]: ...

    def intent(self) -> Intent | None: ...

    def submit_intent(self, intent: Intent) -> IntentReply: ...

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


def _intent_status(reply: IntentReply) -> int:
    """200 für eine angenommene (auch geklemmte) Absicht, 422 für unbekannte Aktoren, sonst 409."""
    if reply.result != "rejected":
        return status.HTTP_200_OK
    if reply.rejection == "unknown_actuator":
        return HTTP_UNPROCESSABLE
    return status.HTTP_409_CONFLICT


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

    @app.get("/v1/intent", dependencies=[Depends(require_token)])
    def get_intent() -> CurrentIntent:
        return CurrentIntent(intent=view.intent())

    @app.put("/v1/intent", dependencies=[Depends(require_token)])
    def put_intent(intent: Intent, response: Response) -> IntentResponse:
        reply = view.submit_intent(intent)
        response.status_code = _intent_status(reply)
        return IntentResponse(
            result=reply.result, version=reply.version, details=list(reply.details)
        )

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
