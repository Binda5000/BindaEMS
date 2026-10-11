import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.websockets import WebSocketDisconnect
from tests.helpers import EMPTY_DERIVED, snap

from bindaems.core.api.app import HealthReport, create_api, snapshot_to_json
from bindaems.core.control.intent import IntentReply
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.control import Intent

TOKEN = "x" * 32
H = {"Authorization": "Bearer " + TOKEN}


class FakeView:
    def __init__(self) -> None:
        self.current: Intent | None = None
        self.reply = IntentReply("accepted", 1)
        self.submitted: list[Intent] = []

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.submitted.append(intent)
        return self.reply

    def health(self) -> HealthReport:
        return HealthReport(
            mode="OBSERVE",
            version="0.1.0",
            adapters=[{"name": "victron", "connected": True}],
            selfcheck=[],
            alarms=[],
            cycle_ms_p95=None,
        )

    def state(self) -> dict[str, Any]:
        return snapshot_to_json(snap({"grid.l1.power_w": 1000.0}), EMPTY_DERIVED)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        async def messages() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "state", "data": {}}

        yield messages()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_api(FakeView(), SecretStr(TOKEN)))


@pytest.fixture
def view_client() -> tuple[TestClient, FakeView]:
    view = FakeView()
    return TestClient(create_api(view, SecretStr(TOKEN))), view


def test_health_requires_token(client) -> None:
    assert client.get("/v1/health").status_code == 401


@pytest.mark.parametrize(
    "header", ["Bearer " + "y" * 32, "Basic " + TOKEN, TOKEN, "Bearer " + TOKEN + "x"]
)
def test_wrong_credentials_rejected(client, header: str) -> None:
    assert client.get("/v1/state", headers={"Authorization": header}).status_code == 401


def test_health_ok(client) -> None:
    body = client.get("/v1/health", headers=H).json()
    assert body["mode"] == "OBSERVE" and body["adapters"][0]["name"] == "victron"


def test_health_report_defaults_keep_existing_calls_valid() -> None:
    report = FakeView().health()  # ohne die Felder aus Plan 2a
    assert report.model_dump(exclude={"mode", "version", "adapters", "selfcheck", "alarms"}) == {
        "cycle_ms_p95": None,
        "mode_since": None,
        "mode_reasons": [],
        "intent": None,
        "actuators": [],
        "watchdog": {
            "state": "unknown",
            "trip_count": None,
            "last_trip": None,
            "last_trip_failures": None,
            "version": None,
            "expected_version": WATCHDOG_VERSION,
            "heartbeat": "off",
            "required": False,
        },
        "inputs": {"ok": True, "problems": []},
    }


@pytest.mark.parametrize("mode", ["START", "OBSERVE", "CONTROL", "DEGRADED", "SAFE"])
def test_health_report_accepts_the_operating_states(mode: str) -> None:
    report = FakeView().health().model_dump() | {"mode": mode}
    assert HealthReport.model_validate(report).mode == mode


def test_health_report_rejects_other_modes() -> None:
    with pytest.raises(ValidationError):
        HealthReport.model_validate(FakeView().health().model_dump() | {"mode": "observe"})


def test_state_shape(client) -> None:
    body = client.get("/v1/state", headers=H).json()
    assert body["signals"]["grid.l1.power_w"] == {
        "v": 1000.0,
        "ts": "2026-10-08T10:00:00+00:00",
        "q": "ok",
    }


def test_snapshot_to_json_converts_derived_mappings() -> None:
    derived = replace(
        EMPTY_DERIVED,
        house_load_w=1500.0,
        consumption_l=MappingProxyType({"L1": 500.0, "L2": None, "L3": 300.0}),
        wallbox_phase_a=MappingProxyType(
            {"evcs": MappingProxyType({"L1": 16.0, "L2": 0.0, "L3": 0.0})}
        ),
    )
    body = snapshot_to_json(snap({}), derived)
    assert body["ts"] == "2026-10-08T10:00:00+00:00"
    assert body["derived"]["house_load_w"] == 1500.0
    assert body["derived"]["grid_w"] is None
    assert body["derived"]["consumption_l"] == {"L1": 500.0, "L2": None, "L3": 300.0}
    assert body["derived"]["wallbox_phase_a"] == {"evcs": {"L1": 16.0, "L2": 0.0, "L3": 0.0}}
    assert type(body["derived"]["wallbox_phase_a"]["evcs"]) is dict  # JSON-fähig


def test_stream_sends_messages(client) -> None:
    with client.websocket_connect("/v1/stream", headers=H) as ws:
        assert ws.receive_json()["type"] == "state"


def test_stream_rejects_token_in_query(client) -> None:
    # ein Token in der URL landet in Zugriffslogs (uvicorn protokolliert den Pfad): nur Header
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/stream?token={TOKEN}") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


@pytest.mark.parametrize("path", ["/v1/stream?token=falsch", "/v1/stream"])
def test_stream_rejects_bad_token(client, path: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_put_intent_accepted(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply("accepted", 3)
    r = client.put("/v1/intent", json={"version": 3, "control": True}, headers=H)
    assert (r.status_code, r.json()) == (200, {"result": "accepted", "version": 3, "details": []})
    assert view.submitted == [Intent(version=3, control=True)]


def test_put_intent_stale_is_409(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply(
        "rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"
    )
    assert client.put("/v1/intent", json={"version": 4}, headers=H).status_code == 409


def test_put_intent_unknown_actuator_is_422(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply(
        "rejected", None, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"
    )
    r = client.put(
        "/v1/intent", json={"version": 1, "actuators": {"wallbox.twc": "live"}}, headers=H
    )
    assert (r.status_code, r.json()["details"]) == (422, ["Unbekannter Aktor „wallbox.twc“."])


def test_get_intent(view_client) -> None:
    client, view = view_client
    view.current = Intent(version=3)
    assert client.get("/v1/intent").status_code == 401
    assert client.get("/v1/intent", headers=H).json() == {
        "intent": {"version": 3, "control": False, "actuators": {}}
    }


# Weitere Randfälle


def test_get_intent_without_intent_is_null(view_client) -> None:
    client, _ = view_client
    assert client.get("/v1/intent", headers=H).json() == {"intent": None}


def test_put_intent_clamped_is_200_with_details(view_client) -> None:
    client, view = view_client
    details = (
        "EVCS „evcs“: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run.",
    )
    view.reply = IntentReply("clamped", 1, details)
    r = client.put(
        "/v1/intent", json={"version": 1, "actuators": {"wallbox.evcs": "live"}}, headers=H
    )
    assert (r.status_code, r.json()) == (
        200,
        {"result": "clamped", "version": 1, "details": list(details)},
    )
    assert view.submitted == [
        Intent.model_validate({"version": 1, "actuators": {"wallbox.evcs": "live"}})
    ]


def test_put_intent_requires_token(view_client) -> None:
    client, view = view_client
    for headers in ({}, {"Authorization": "Bearer " + "y" * 32}):
        assert client.put("/v1/intent", json={"version": 1}, headers=headers).status_code == 401
        # ohne Token erfährt niemand, was der Body hätte erfüllen müssen
        assert client.put("/v1/intent", json={"version": 0}, headers=headers).status_code == 401
    assert view.submitted == []


@pytest.mark.parametrize(
    "body",
    [
        {"version": 0},
        {"control": True},
        {"version": 1, "actuators": {"victron": "an"}},
        {"version": 1, "unbekannt": True},
    ],
    ids=["version-0", "ohne-version", "ungueltiger-modus", "fremdfeld"],
)
def test_put_intent_with_invalid_body_is_422_and_not_submitted(view_client, body) -> None:
    client, view = view_client
    assert client.put("/v1/intent", json=body, headers=H).status_code == 422
    assert view.submitted == []


def test_no_public_schema_or_docs(client) -> None:
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


async def test_real_client_sees_4401_not_http_403() -> None:
    import asyncio
    import socket

    import uvicorn
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_api(FakeView(), SecretStr(TOKEN)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - uvicorn bietet dafür kein Event
                await asyncio.sleep(0.01)
        async with connect(f"ws://127.0.0.1:{port}/v1/stream?token=falsch") as ws:
            with pytest.raises(ConnectionClosed) as exc:
                await ws.recv()
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4401
    finally:
        server.should_exit = True
        await task
