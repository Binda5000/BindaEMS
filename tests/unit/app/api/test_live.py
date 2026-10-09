import pytest
from starlette.websockets import WebSocketDisconnect
from tests.app_helpers import login_as

from bindaems.app.api.live import live_router
from bindaems.app.core_link import LiveState
from bindaems.app.status import ComponentStatus


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {
        "ts": "2026-10-09T08:00:00+00:00",
        "signals": {"battery.soc_pct": {"v": 55.0, "ts": "2026-10-09T08:00:00+00:00", "q": "ok"}},
        "derived": {"grid_w": 512.0},
    }
    return state


@pytest.fixture
def client(make_client, live, guard):
    def ok_source() -> ComponentStatus:
        return ComponentStatus("prices", True, "Preise bis 10.10. 23:45")

    return make_client(live_router(live, guard, [ok_source], lambda: ["W1"]))


def test_state_requires_login_and_returns_cache(client, auth, live) -> None:
    assert client.get("/api/state").status_code == 401
    login_as(client, auth, "viewer")
    body = client.get("/api/state").json()
    assert body["core_connected"] is True and body["state"] == live.state


def test_system_lists_components_and_warnings(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/system").json()
    assert body["components"][0] == {
        "name": "prices",
        "ok": True,
        "message": "Preise bis 10.10. 23:45",
        "since": None,
        "details": {},
    }
    assert body["warnings"] == ["W1"] and body["core"]["connected"] is True


def test_live_websocket_sends_hello_then_relays(client, auth, live) -> None:
    login_as(client, auth, "viewer")
    cookie = f"bindaems_session={client.cookies['bindaems_session']}"
    with client.websocket_connect("/api/live", headers={"cookie": cookie}) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["data"]["core_connected"] is True
        # Broadcast gehört zur Ereignisschleife der App: über das Portal des Test-Clients senden
        ws.portal.call(live.publish, {"type": "state", "data": {"x": 1}})
        assert ws.receive_json() == {"type": "state", "data": {"x": 1}}


def test_live_websocket_rejects_missing_session(client) -> None:
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
    assert closed.value.code == 4401


def test_live_websocket_rejects_foreign_origin(client, auth) -> None:
    login_as(client, auth, "viewer")
    headers = {
        "cookie": f"bindaems_session={client.cookies['bindaems_session']}",
        "origin": "https://boese.example",
    }
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/api/live", headers=headers) as ws,
    ):
        ws.receive_json()
    assert closed.value.code == 4403
