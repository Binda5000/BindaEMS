"""Sitzungs-Cookies, CSRF-Prüfung und Rollen als FastAPI-Abhängigkeiten."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable

from fastapi import HTTPException, Request, Response, WebSocket, status

from bindaems.app.auth.service import (
    REMEMBER_FOR,
    ROLE_RANK,
    AuthService,
    NewSession,
    Role,
    SessionInfo,
)

SESSION_COOKIE = "bindaems_session"
CSRF_COOKIE = "bindaems_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Guard:
    """Prüft Anmeldung, Rolle und CSRF-Token; setzt und löscht die Cookies."""

    def __init__(self, auth: AuthService, *, cookie_secure: bool) -> None:
        self._auth = auth
        self._secure = cookie_secure

    def require(self, role: Role) -> Callable[[Request], SessionInfo]:
        def dependency(request: Request) -> SessionInfo:
            token = request.cookies.get(SESSION_COOKIE)
            session = self._auth.resolve(token) if token else None
            if session is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
            if ROLE_RANK[session.user.role] < ROLE_RANK[role]:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Keine Berechtigung")
            if request.method not in _SAFE_METHODS:
                header = request.headers.get(CSRF_HEADER, "")
                if not hmac.compare_digest(header.encode(), session.csrf_token.encode()):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN, "CSRF-Token fehlt oder ist ungültig"
                    )
            return session

        return dependency

    async def websocket_session(self, ws: WebSocket) -> SessionInfo | None:
        """Sitzung des WebSockets, ohne sie zu verlängern."""
        token = ws.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        return await asyncio.to_thread(self._auth.resolve, token, touch=False)

    def set_session_cookies(self, response: Response, session: NewSession) -> None:
        max_age = int(REMEMBER_FOR.total_seconds()) if session.remember else None
        for name, value, http_only in (
            (SESSION_COOKIE, session.token, True),
            (CSRF_COOKIE, session.csrf_token, False),  # das UI liest es für den Header
        ):
            response.set_cookie(
                name,
                value,
                max_age=max_age,
                path="/",
                secure=self._secure,
                httponly=http_only,
                samesite="strict",
            )

    def clear_session_cookies(self, response: Response) -> None:
        for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
            response.delete_cookie(
                name, path="/", secure=self._secure, httponly=http_only, samesite="strict"
            )
