"""FastAPI-Anwendung: Health, Security-Header, deutsche Fehlertexte und statisches UI."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bindaems import __version__

log = structlog.get_logger(__name__)

IMMUTABLE = "public, max-age=31536000, immutable"
# Standardtexte von Starlette → deutsche Texte; eigene Fehlertexte bleiben unverändert
_GERMAN_DETAILS = {"Not Found": "Nicht gefunden", "Method Not Allowed": "Methode nicht erlaubt"}
_INLINE_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.DOTALL)
_STATIC_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)


def content_security_policy(index_html: str | None) -> str:
    """CSP; Inline-Skripte aus ``index.html`` sind nur über ihre SHA-256-Hashes erlaubt."""
    hashes = ""
    if index_html is not None:
        for match in _INLINE_SCRIPT.finditer(index_html):
            if "src=" in match["attrs"]:
                continue
            digest = hashlib.sha256(match["body"].encode("utf-8")).digest()
            hashes += f" 'sha256-{base64.b64encode(digest).decode()}'"
    return (
        f"default-src 'self'; script-src 'self'{hashes}; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )


class SecurityHeaders:
    """Setzt die Security-Header auf jede HTTP-Antwort; WebSockets bleiben unberührt."""

    def __init__(self, app: ASGIApp, csp: str) -> None:
        self._app = app
        self._csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path: str = scope["path"]
        api = path == "/api" or path.startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _STATIC_HEADERS:
                    headers[name] = value
                headers["Content-Security-Policy"] = self._csp
                if api:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self._app(scope, receive, send_with_headers)


def create_app(routers: Sequence[APIRouter], *, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="BindaEMS", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        detail = _GERMAN_DETAILS.get(exc.detail, exc.detail)
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    for router in routers:
        app.include_router(router)

    index_html = _mount_ui(app, ui_dir) if ui_dir is not None else None
    app.add_middleware(SecurityHeaders, csp=content_security_policy(index_html))
    return app


def _mount_ui(app: FastAPI, ui_dir: Path) -> str | None:
    """Liefert das UI als SPA aus; unbekannte Pfade erhalten ``index.html``."""
    root = ui_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        log.warning("UI-Verzeichnis ohne index.html, UI wird nicht ausgeliefert", ui_dir=str(root))
        return None

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            immutable = path.startswith("_app/immutable/")
            return FileResponse(
                candidate, headers={"Cache-Control": IMMUTABLE} if immutable else None
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return index.read_text(encoding="utf-8")
