"""Zugangsprüfung: von außen nur mit Client-Zertifikat, das der vertraute Reverse Proxy geprüft hat.

HAProxy prüft das Zertifikat (mTLS) und meldet das Ergebnis in ``X-BindaEMS-Client-Cert`` und
``X-BindaEMS-Client-CN``. Die app wertet beide nur aus, wenn die direkte Gegenstelle in
``app.trusted_proxies`` liegt; von jeder anderen Adresse ignoriert sie sie.

Reihenfolge der Schichten (außen nach innen): ``PeerMarker`` merkt sich die rohe Gegenstelle,
uvicorns ``ProxyHeadersMiddleware`` setzt die echte Client-Adresse aus ``X-Forwarded-For``,
``AccessGate`` entscheidet.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from bindaems.shared.config import ClientCertConfig

log = structlog.get_logger(__name__)

CERT_HEADER = "x-bindaems-client-cert"
CN_HEADER = "x-bindaems-client-cn"
CN_MAX = 64
PEER_KEY = "bindaems.peer"
ACCESS_KEY = "bindaems.access"
DENIED_DETAIL = "Zugriff nur mit Client-Zertifikat"
POLICY_VIOLATION = 1008  # WebSocket-Schließcode „Policy Violation“
LOG_INTERVAL_S = 60.0  # höchstens ein Log je Adresse und Minute
LOG_MEMORY = 1024  # so viele Adressen merkt sich die Drosselung höchstens
_OPEN_PATHS = frozenset({"/health"})  # Docker- und Proxy-Healthcheck

type _Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class Access:
    """Woher eine Anfrage kommt.

    ``remote``: echte Client-Adresse nicht in ``exempt_networks``. ``device``: CN des Zertifikats,
    nur wenn der vertraute Proxy ein geprüftes Zertifikat meldet, sonst ``None``.
    """

    client_ip: str | None
    via_proxy: bool
    remote: bool
    device: str | None


def access_of(request: Request) -> Access | None:
    """``Access`` der Anfrage; ``None``, wenn keine Zugangsprüfung davor liegt."""
    access = request.scope.get(ACCESS_KEY)
    return access if isinstance(access, Access) else None


def networks(entries: Iterable[str]) -> tuple[_Network, ...]:
    """Adressen und Netze wie in der Konfiguration (``strict=False``, Hostbits erlaubt)."""
    return tuple(ipaddress.ip_network(entry, strict=False) for entry in entries)


def _within(host: str | None, nets: Sequence[_Network]) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:  # z. B. "testclient" oder ein Unix-Socket
        return False
    return any(ip in net for net in nets)


def _header(scope: Scope, name: str) -> str | None:
    """Wert einer Kopfzeile, nur wenn sie genau einmal vorkommt; mehrfach zählt als fehlend."""
    wanted = name.encode("latin-1")
    values = [value for key, value in scope["headers"] if key.lower() == wanted]
    return values[0].decode("latin-1") if len(values) == 1 else None


class PeerMarker:
    """Äußerste Schicht: legt die rohe Gegenstelle ab, bevor ``X-Forwarded-For`` sie ersetzt."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            scope[PEER_KEY] = client[0] if client else None
        await self._app(scope, receive, send)


class AccessGate:
    """Legt ``Access`` im Scope ab und weist mit ``client_cert.required`` Anfragen von außen ohne
    geprüftes Zertifikat ab (HTTP 403, WebSocket Code 1008 vor dem Annehmen)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_proxies: Sequence[str],
        client_cert: ClientCertConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._proxies = networks(trusted_proxies)
        self._exempt = networks(client_cert.exempt_networks)
        self._required = client_cert.required
        self._clock = clock
        self._logged: dict[str, float] = {}  # Adresse → letzter Log (monoton)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        access, verified = self._access(scope)
        scope[ACCESS_KEY] = access
        allowed = not access.remote or verified
        if not self._required or scope["path"] in _OPEN_PATHS or allowed:
            await self._app(scope, receive, send)
            return
        self._log_rejection(access.client_ip, scope["path"])
        if scope["type"] == "websocket":
            await WebSocketClose(code=POLICY_VIOLATION)(scope, receive, send)
        else:
            await JSONResponse({"detail": DENIED_DETAIL}, status_code=403)(scope, receive, send)

    def _access(self, scope: Scope) -> tuple[Access, bool]:
        """``Access`` der Anfrage und ob der vertraute Proxy ein geprüftes Zertifikat meldet."""
        via_proxy = _within(scope.get(PEER_KEY), self._proxies)
        client = scope.get("client")
        client_ip: str | None = client[0] if client else None
        # Ohne verwertbares X-Forwarded-For bleibt der Proxy selbst die Adresse: Die echte ist dann
        # unbekannt und zählt nicht als Heimnetz, auch wenn der Proxy darin steht.
        unknown = via_proxy and _within(client_ip, self._proxies)
        remote = unknown or not _within(client_ip, self._exempt)
        verified = via_proxy and _header(scope, CERT_HEADER) == "1"
        device = None
        if verified:
            device = (_header(scope, CN_HEADER) or "").strip()[:CN_MAX] or None
        access = Access(client_ip=client_ip, via_proxy=via_proxy, remote=remote, device=device)
        return access, verified

    def _log_rejection(self, ip: str | None, path: str) -> None:
        key = ip or "-"
        now = self._clock()
        last = self._logged.get(key)
        if last is not None and now - last < LOG_INTERVAL_S:
            return
        if len(self._logged) >= LOG_MEMORY:
            self._logged = {k: t for k, t in self._logged.items() if now - t < LOG_INTERVAL_S}
            if len(self._logged) >= LOG_MEMORY:  # alle frisch: lieber einmal zu oft loggen
                self._logged.clear()
        self._logged[key] = now
        log.warning("Zugriff ohne Client-Zertifikat abgewiesen", ip=ip, path=path)
