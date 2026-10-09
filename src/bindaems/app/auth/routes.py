"""Endpunkte für Anmeldung, eigenes Konto, Benutzerverwaltung und Änderungsprotokoll.

Ohne ``from __future__ import annotations``: FastAPI muss die lokal gebauten Abhängigkeiten
(``Viewer``, ``Admin``) in den Signaturen auflösen können.
"""

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import (
    MAX_PASSWORD_LEN,
    AuthService,
    InvalidCredentialsError,
    InvalidTotpError,
    InvalidUsernameError,
    LastAdminError,
    LockedError,
    PasswordPolicyError,
    Role,
    SessionInfo,
    TotpAlreadyEnabledError,
    TotpRequiredError,
    User,
    UserExistsError,
    UserNotFoundError,
)
from bindaems.app.auth.web import SESSION_COOKIE, Guard

log = structlog.get_logger(__name__)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Body):
    username: Annotated[str, Field(max_length=64)]
    password: Annotated[str, Field(max_length=MAX_PASSWORD_LEN)]
    totp: Annotated[str | None, Field(max_length=16)] = None
    remember: bool = False


class PasswordBody(_Body):
    old_password: Annotated[str, Field(max_length=MAX_PASSWORD_LEN)]
    new_password: Annotated[str, Field(max_length=MAX_PASSWORD_LEN)]


class TotpCodeBody(_Body):
    code: Annotated[str, Field(max_length=16)]


class TotpDisableBody(_Body):
    password: Annotated[str, Field(max_length=MAX_PASSWORD_LEN)]


class NewUserBody(_Body):
    username: Annotated[str, Field(max_length=64)]
    password: Annotated[str, Field(max_length=MAX_PASSWORD_LEN)]
    role: Role


class UserPatchBody(_Body):
    role: Role | None = None
    password: Annotated[str | None, Field(max_length=MAX_PASSWORD_LEN)] = None


def user_json(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "totp_enabled": user.totp_enabled,
        "created_at": user.created_at.isoformat(),
    }


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def auth_router(auth: AuthService, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]

    @router.post("/login")
    def login(body: LoginBody, request: Request) -> Response:
        ip = _client_ip(request)
        try:
            session = auth.login(
                body.username, body.password, totp=body.totp, remember=body.remember
            )
        except LockedError as exc:
            log.warning("Anmeldung gesperrt", username=body.username, ip=ip)
            return JSONResponse(
                {"detail": str(exc)},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(exc.retry_after_s)},
            )
        except TotpRequiredError as exc:
            return JSONResponse(
                {"detail": str(exc), "totp_required": True},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        except InvalidCredentialsError as exc:
            log.warning("Anmeldung fehlgeschlagen", username=body.username, ip=ip)
            return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_401_UNAUTHORIZED)
        log.info("Anmeldung erfolgreich", username=session.user.username, ip=ip)
        response = JSONResponse({"user": user_json(session.user)})
        guard.set_session_cookies(response, session)
        return response

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(request: Request, session: Viewer) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            auth.logout(token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        guard.clear_session_cookies(response)
        return response

    @router.get("/me")
    def me(session: Viewer) -> dict[str, Any]:
        return {"user": user_json(session.user)}

    @router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
    def change_password(body: PasswordBody, session: Viewer) -> None:
        if not auth.verify_password(session.user.id, body.old_password):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Altes Passwort falsch")
        try:
            auth.set_password(
                session.user.id,
                body.new_password,
                actor=session.user.username,
                source="ui",
                keep_session=session.session_id,
            )
        except PasswordPolicyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    @router.post("/totp/setup")
    def totp_setup(session: Viewer) -> dict[str, str]:
        try:
            secret, uri = auth.totp_begin(session.user.id)
        except TotpAlreadyEnabledError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"secret": secret, "uri": uri}

    @router.post("/totp/enable", status_code=status.HTTP_204_NO_CONTENT)
    def totp_enable(body: TotpCodeBody, session: Viewer) -> None:
        try:
            auth.totp_enable(session.user.id, body.code, actor=session.user.username, source="ui")
        except InvalidTotpError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
    def totp_disable(body: TotpDisableBody, session: Viewer) -> None:
        try:
            auth.totp_disable(
                session.user.id, body.password, actor=session.user.username, source="ui"
            )
        except InvalidCredentialsError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Passwort falsch") from exc

    return router


def users_router(auth: AuthService, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/users")
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("")
    def list_users(session: Admin) -> list[dict[str, Any]]:
        return [user_json(user) for user in auth.list_users()]

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create_user(body: NewUserBody, session: Admin) -> dict[str, Any]:
        try:
            user = auth.create_user(
                body.username, body.password, body.role, actor=session.user.username, source="ui"
            )
        except UserExistsError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except (InvalidUsernameError, PasswordPolicyError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return user_json(user)

    @router.patch("/{user_id}")
    def change_user(user_id: int, body: UserPatchBody, session: Admin) -> dict[str, Any]:
        own = user_id == session.user.id
        try:
            user = auth.update_user(
                user_id,
                password=body.password,
                role=body.role,
                actor=session.user.username,
                source="ui",
                keep_session=session.session_id if own else None,
            )
            return user_json(user)
        except UserNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except LastAdminError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except PasswordPolicyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    @router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_user(user_id: int, session: Admin) -> None:
        try:
            auth.delete_user(user_id, actor=session.user.username, source="ui")
        except UserNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except LastAdminError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return router


def audit_router(audit: AuditLog, guard: Guard) -> APIRouter:
    router = APIRouter(prefix="/api/audit")
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    @router.get("")
    def recent(
        session: Admin, limit: Annotated[int, Query(ge=1, le=1000)] = 100
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": entry.id,
                "ts": entry.ts.isoformat(),
                "actor": entry.actor,
                "source": entry.source,
                "action": entry.action,
                "target": entry.target,
                "details": entry.details,
            }
            for entry in audit.recent(limit)
        ]

    return router
