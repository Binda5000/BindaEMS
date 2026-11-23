import pytest
from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from structlog.testing import capture_logs

from bindaems.app.api.access import (
    CN_MAX,
    LOG_MEMORY,
    UNTRUSTED_WARNING,
    Access,
    AccessGate,
    PeerMarker,
    access_of,
)
from bindaems.app.api.server import create_app
from bindaems.shared.config import ClientCertConfig

PROXY = "10.0.0.2"
RFC1918 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
DENIED = {"detail": "Zugriff nur mit Client-Zertifikat"}
CERT = {"X-BindaEMS-Client-Cert": "1", "X-BindaEMS-Client-CN": "handy-binda"}


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ping")
    def ping(request: Request) -> dict[str, object]:
        access = access_of(request)
        return {"access": None if access is None else access.__dict__}

    @router.websocket("/api/live-test")
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        access = websocket.scope["bindaems.access"]
        await websocket.send_json({"device": access.device, "remote": access.remote})
        await websocket.close()

    return router


def _app(
    *,
    required: bool = True,
    trusted: list[str] | None = None,
    exempt: list[str] | None = None,
) -> FastAPI:
    return create_app(
        [_router()],
        trusted_proxies=[PROXY] if trusted is None else trusted,
        client_cert=ClientCertConfig(
            required=required,
            exempt_networks=["192.168.1.0/24"] if exempt is None else exempt,
        ),
    )


def _client(ip: str, **kwargs: object) -> TestClient:
    return TestClient(_app(**kwargs), client=(ip, 50000))  # type: ignore[arg-type]


def _access(response: object) -> Access:
    body = response.json()  # type: ignore[attr-defined]
    return Access(**body["access"])


def test_lan_client_needs_no_cert() -> None:
    response = _client("192.168.1.5").get("/api/ping")
    assert response.status_code == 200
    assert _access(response) == Access("192.168.1.5", via_proxy=False, remote=False, device=None)


def test_proxy_request_with_verified_cert_passes() -> None:
    headers = {"X-Forwarded-For": "203.0.113.7", **CERT}
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert response.status_code == 200
    assert _access(response) == Access(
        client_ip="203.0.113.7", via_proxy=True, remote=True, device="handy-binda"
    )


def test_proxy_request_without_cert_is_rejected() -> None:
    headers = {"X-Forwarded-For": "203.0.113.7", "X-BindaEMS-Client-CN": "handy-binda"}
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert response.status_code == 403 and response.json() == DENIED
    assert response.headers["content-type"] == "application/json"
    # auch die Abweisung trägt die Security-Header und landet in keinem Cache
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("value", ["0", "", "true", "1 ", "11"])
def test_cert_header_counts_only_when_exactly_one(value: str) -> None:
    headers = {"X-Forwarded-For": "203.0.113.7", "X-BindaEMS-Client-Cert": value}
    assert _client(PROXY).get("/api/ping", headers=headers).status_code == 403


def test_verified_cert_without_cn_passes() -> None:
    headers = {"X-Forwarded-For": "203.0.113.7", "X-BindaEMS-Client-Cert": "1"}
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert response.status_code == 200 and _access(response).device is None


def test_repeated_cert_header_is_rejected() -> None:
    headers = [
        ("X-Forwarded-For", "203.0.113.7"),
        ("X-BindaEMS-Client-Cert", "0"),
        ("X-BindaEMS-Client-Cert", "1"),
    ]
    assert _client(PROXY).get("/api/ping", headers=headers).status_code == 403


def test_proxy_lan_client_passes_without_cert() -> None:
    response = _client(PROXY).get("/api/ping", headers={"X-Forwarded-For": "192.168.1.20"})
    assert response.status_code == 200
    assert _access(response) == Access("192.168.1.20", via_proxy=True, remote=False, device=None)


def test_client_supplied_forwarded_for_does_not_count_as_lan() -> None:
    headers = {"X-Forwarded-For": "192.168.1.5, 203.0.113.7"}
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert response.status_code == 403 and response.json() == DENIED


def test_spoofed_headers_from_untrusted_peer_are_ignored() -> None:
    headers = {"X-Forwarded-For": "192.168.1.5", **CERT}
    response = _client("203.0.113.9").get("/api/ping", headers=headers)
    assert response.status_code == 403 and response.json() == DENIED


def test_forwarded_for_as_separate_header_lines_is_rejected() -> None:
    # Regressionstest: vom Client eine eigene Zeile „192.168.1.5“, vom Proxy „203.0.113.7“
    headers = [("X-Forwarded-For", "192.168.1.5"), ("X-Forwarded-For", "203.0.113.7")]
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert response.status_code == 403 and response.json() == DENIED


@pytest.mark.parametrize(
    "headers",
    [{"X-Forwarded-For": "203.0.113.7"}, {"X-BindaEMS-Client-Cert": "1"}],
    ids=["forwarded-for", "cert-header"],
)
def test_proxy_headers_from_lan_peer_not_in_trusted_proxies_are_rejected(
    headers: dict[str, str],
) -> None:
    # OPNsense erreicht die VM von 10.0.0.3, konfiguriert ist 10.0.0.2: kein Heimnetz-Freipass
    client = _client("10.0.0.3", exempt=RFC1918)
    with capture_logs() as logs:
        response = client.get("/api/ping", headers=headers)
        client.get("/api/ping", headers=headers)
    assert response.status_code == 403 and response.json() == DENIED
    warned = [e for e in logs if e["event"] == UNTRUSTED_WARNING]
    assert [(e["ip"], e["log_level"]) for e in warned] == [("10.0.0.3", "warning")]


def test_lan_client_without_proxy_headers_passes_with_rfc1918() -> None:
    response = _client("192.168.1.5", exempt=RFC1918).get("/api/ping")
    assert response.status_code == 200
    assert _access(response) == Access("192.168.1.5", via_proxy=False, remote=False, device=None)


def test_proxy_without_forwarded_for_is_not_lan() -> None:
    # ohne X-Forwarded-For ist die echte Adresse unbekannt: der Proxy zählt nicht als Heimnetz
    app = _app(trusted=["192.168.1.1"])
    response = TestClient(app, client=("192.168.1.1", 50000)).get("/api/ping")
    assert response.status_code == 403


def test_proxy_given_as_network_with_host_bits_is_trusted() -> None:
    # die Konfiguration erlaubt "192.168.1.1/24" (strict=False); das muss auch uvicorn so sehen
    app = _app(trusted=["10.0.0.9/24"])
    headers = {"X-Forwarded-For": "203.0.113.7", **CERT}
    response = TestClient(app, client=(PROXY, 50000)).get("/api/ping", headers=headers)
    assert response.status_code == 200
    assert _access(response).client_ip == "203.0.113.7"


def test_ipv4_mapped_lan_client_counts_as_lan() -> None:
    response = _client("::ffff:192.168.1.5", exempt=RFC1918).get("/api/ping")
    assert response.status_code == 200 and _access(response).remote is False


def test_ipv4_mapped_proxy_is_trusted() -> None:
    # Dual-Stack-Socket: der Proxy 10.0.0.2 erscheint als ::ffff:10.0.0.2
    headers = {"X-Forwarded-For": "203.0.113.7", **CERT}
    response = _client("::ffff:10.0.0.2").get("/api/ping", headers=headers)
    assert response.status_code == 200
    assert _access(response) == Access(
        client_ip="203.0.113.7", via_proxy=True, remote=True, device="handy-binda"
    )


def test_websocket_without_cert_is_rejected() -> None:
    client = _client(PROXY)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/api/live-test", headers={"X-Forwarded-For": "203.0.113.7"}
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_websocket_with_cert_passes() -> None:
    headers = {"X-Forwarded-For": "203.0.113.7", **CERT}
    with _client(PROXY).websocket_connect("/api/live-test", headers=headers) as ws:
        assert ws.receive_json() == {"device": "handy-binda", "remote": True}


def test_health_needs_no_cert() -> None:
    assert _client("127.0.0.1").get("/health").status_code == 200
    assert _client(PROXY).get("/health").status_code == 200
    headers = {"X-Forwarded-For": "203.0.113.7"}
    assert _client(PROXY).get("/health", headers=headers).status_code == 200


def test_cn_is_truncated_and_ignored_without_cert() -> None:
    long_cn = "x" * 80
    headers = {
        "X-Forwarded-For": "203.0.113.7",
        "X-BindaEMS-Client-Cert": "1",
        "X-BindaEMS-Client-CN": long_cn,
    }
    response = _client(PROXY).get("/api/ping", headers=headers)
    assert _access(response).device == "x" * CN_MAX
    assert CN_MAX == 64

    lan = _client(PROXY).get(
        "/api/ping",
        headers={"X-Forwarded-For": "192.168.1.20", "X-BindaEMS-Client-CN": "handy-binda"},
    )
    assert lan.status_code == 200 and _access(lan).device is None


def test_off_never_blocks_but_records_access() -> None:
    response = _client("203.0.113.9", required=False).get("/api/ping")
    assert response.status_code == 200
    assert _access(response) == Access("203.0.113.9", via_proxy=False, remote=True, device=None)


def test_forwarded_for_still_used_for_client_ip_when_off() -> None:
    headers = {"X-Forwarded-For": "203.0.113.7"}
    via_proxy = _client(PROXY, required=False).get("/api/ping", headers=headers)
    assert _access(via_proxy).client_ip == "203.0.113.7"
    direct = _client("203.0.113.9", required=False).get("/api/ping", headers=headers)
    assert _access(direct).client_ip == "203.0.113.9"
    assert _access(direct).via_proxy is False


def test_forwarded_for_ignored_without_trusted_proxies() -> None:
    # ab Werk ohne trusted_proxies zählt nur die Gegenstelle, wie bisher ohne proxy_headers
    app = create_app([_router()])
    client = TestClient(app, client=(PROXY, 50000))
    response = client.get("/api/ping", headers={"X-Forwarded-For": "192.168.1.5", **CERT})
    assert response.status_code == 200
    assert _access(response) == Access(PROXY, via_proxy=False, remote=True, device=None)


def test_rejection_is_logged_once_per_address_and_minute() -> None:
    now = [1000.0]
    gate = PeerMarker(
        AccessGate(
            _app(required=False),
            trusted_proxies=[],
            client_cert=ClientCertConfig(required=True),
            clock=lambda: now[0],
        )
    )
    with capture_logs() as logs:
        for ip in ["203.0.113.7", "203.0.113.7", "203.0.113.8"]:
            TestClient(gate, client=(ip, 50000)).get("/api/ping")
        now[0] += 61.0
        TestClient(gate, client=("203.0.113.7", 50000)).get("/api/ping")
    rejected = [e for e in logs if e["event"] == "Zugriff ohne Client-Zertifikat abgewiesen"]
    assert [(e["ip"], e["path"]) for e in rejected] == [
        ("203.0.113.7", "/api/ping"),
        ("203.0.113.8", "/api/ping"),
        ("203.0.113.7", "/api/ping"),
    ]
    assert all(e["log_level"] == "warning" for e in rejected)


def test_log_throttle_stays_bounded() -> None:
    now = [0.0]
    gate = AccessGate(
        _app(required=False),
        trusted_proxies=[],
        client_cert=ClientCertConfig(required=True),
        clock=lambda: now[0],
    )
    with capture_logs():
        for i in range(LOG_MEMORY + 10):
            TestClient(gate, client=(f"198.51.{i // 256}.{i % 256}", 1)).get("/api/ping")
            now[0] += 0.001
    assert len(gate._logged) <= LOG_MEMORY


def test_access_of_without_gate_is_none() -> None:
    app = FastAPI()

    @app.get("/x")
    def x(request: Request) -> dict[str, bool]:
        return {"none": access_of(request) is None}

    assert TestClient(app).get("/x").json() == {"none": True}
