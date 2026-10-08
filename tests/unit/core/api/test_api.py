import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from types import MappingProxyType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect
from tests.helpers import EMPTY_DERIVED, snap

from bindaems.core.api.app import HealthReport, create_api, snapshot_to_json

TOKEN = "x" * 32
H = {"Authorization": "Bearer " + TOKEN}


class FakeView:
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
