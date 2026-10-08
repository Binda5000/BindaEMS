import json

import pytest
from tests.helpers import run_until
from websockets.asyncio.server import ServerConnection, serve

from bindaems.core.adapters.homeassistant import connect_ws, convert_state, websocket_url
from bindaems.shared.domain import Quality

AUTH = [{"type": "auth_required"}, {"type": "auth_ok"}]


def _added(state: str) -> dict:
    return {"id": 1, "type": "event", "event": {"a": {"sensor.egolf_soc": {"s": state, "a": {}}}}}


async def test_auth_and_subscribe(ha_env) -> None:
    adapter, conn, _ = ha_env(inbound=AUTH)
    await run_until(adapter, lambda: len(conn.outbound) >= 2)
    assert conn.outbound[0] == {"type": "auth", "access_token": "tok"}
    assert conn.outbound[1]["type"] == "subscribe_entities"
    assert conn.outbound[1]["entity_ids"] == ["sensor.egolf_soc"]


async def test_initial_and_changed_states(ha_env) -> None:
    adapter, _, store = ha_env(
        inbound=[
            *AUTH,
            {
                "id": 1,
                "type": "event",
                "event": {"a": {"sensor.egolf_soc": {"s": "55", "a": {}, "lu": 1791450000.0}}},
            },
            {"id": 1, "type": "event", "event": {"c": {"sensor.egolf_soc": {"+": {"s": "56"}}}}},
        ]
    )
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.egolf.soc_pct") == 56.0)


async def test_unavailable_marks_invalid(ha_env) -> None:
    adapter, _, store = ha_env(inbound=[*AUTH, _added("unavailable")])
    await run_until(adapter, lambda: store.snapshot().get("vehicle.egolf.soc_pct") is not None)
    assert store.snapshot().get("vehicle.egolf.soc_pct").quality.value == "invalid"


async def test_removed_entity_marks_invalid(ha_env) -> None:
    adapter, _, store = ha_env(
        inbound=[
            *AUTH,
            _added("55"),
            {"id": 1, "type": "event", "event": {"r": ["sensor.egolf_soc"]}},
        ]
    )

    def removed() -> bool:
        reading = store.snapshot().get("vehicle.egolf.soc_pct")
        return reading is not None and reading.quality is Quality.INVALID

    await run_until(adapter, removed)


async def test_entity_mapped_to_multiple_signals(ha_env) -> None:
    entities = {
        "vehicle.egolf.soc_pct": "sensor.egolf_soc",
        "vehicle.egolf.soc_ha": "sensor.egolf_soc",
    }
    adapter, conn, store = ha_env(inbound=[*AUTH, _added("55")], entities=entities)

    def both() -> bool:
        snap = store.snapshot()
        return snap.ok("vehicle.egolf.soc_pct") == 55.0 and snap.ok("vehicle.egolf.soc_ha") == 55.0

    await run_until(adapter, both)
    assert conn.outbound[1]["entity_ids"] == ["sensor.egolf_soc"]


async def test_auth_invalid_backs_off(ha_env) -> None:
    adapter, _, _ = ha_env(
        inbound=[{"type": "auth_required"}, {"type": "auth_invalid", "message": "x"}]
    )
    await run_until(adapter, lambda: bool(adapter.sleeps))
    assert adapter.sleeps[0] == 300
    assert adapter.health().last_error == "HA-Token ungültig"


async def test_subscribe_failure_is_reported(ha_env) -> None:
    failure = {"id": 1, "type": "result", "success": False, "error": {"code": "invalid_format"}}
    adapter, _, _ = ha_env(inbound=[*AUTH, failure])
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert "subscribe_entities" in adapter.health().last_error


async def test_never_sends_service_calls(ha_env) -> None:
    adapter, conn, _ = ha_env(inbound=AUTH, ping_s=0.01)
    await run_until(adapter, lambda: len(conn.outbound) >= 4)
    assert {m["type"] for m in conn.outbound} <= {"auth", "subscribe_entities", "ping"}
    assert [m["id"] for m in conn.outbound[2:4]] == [2, 3]  # Ping-IDs aufsteigend nach dem Abo


async def test_disconnect_marks_values_stale(ha_env) -> None:
    adapter, _, store = ha_env(inbound=[*AUTH, _added("55"), ConnectionError("weg")])

    def stale_after_error() -> bool:  # geprüft, solange der Adapter läuft
        reading = store.snapshot().get("vehicle.egolf.soc_pct")
        return (
            adapter.health().error_count >= 1
            and reading is not None
            and reading.quality is Quality.STALE
        )

    await run_until(adapter, stale_after_error)


async def test_backoff_grows_and_resets_after_session_with_events(ha_env) -> None:
    adapter, _, _ = ha_env(
        inbound=[
            ConnectionError("vor der Anmeldung"),
            *AUTH,
            _added("55"),
            ConnectionError("nach Ereignissen"),
            ConnectionError("vor der Anmeldung"),
        ]
    )
    await run_until(adapter, lambda: len(adapter.sleeps) >= 3)
    assert adapter.sleeps[:3] == [1.0, 1.0, 2.0]


async def test_failures_after_login_without_events_back_off(ha_env) -> None:
    # z. B. ungültige Entitäts-ID: Anmeldung klappt, das Abo scheitert – nicht jede Sekunde neu
    failure = {"id": 1, "type": "result", "success": False, "error": {"code": "invalid_format"}}
    adapter, _, _ = ha_env(inbound=[*AUTH, failure] * 3)
    await run_until(adapter, lambda: len(adapter.sleeps) >= 3)
    assert adapter.sleeps[:3] == [1.0, 2.0, 4.0]


async def test_cancel_marks_source_disconnected(ha_env) -> None:
    adapter, _, store = ha_env(inbound=[*AUTH, _added("55")])
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.egolf.soc_pct") == 55.0)
    assert store.snapshot().get("vehicle.egolf.soc_pct").quality is Quality.STALE


def test_convert_state() -> None:
    assert [convert_state(s) for s in ("unavailable", "unknown", "")] == [None, None, None]
    assert convert_state("on") is True and convert_state("off") is False
    assert convert_state("12.5") == 12.5 and isinstance(convert_state("3"), float)
    assert convert_state("nan") is None
    assert convert_state("home") == "home"


def test_websocket_url() -> None:
    assert (
        websocket_url("http://homeassistant.lan:8123")
        == "ws://homeassistant.lan:8123/api/websocket"
    )
    assert websocket_url("https://ha.example.org/") == "wss://ha.example.org/api/websocket"


async def test_connect_ws_roundtrip_with_local_server() -> None:
    paths: list[str] = []
    received: list[dict] = []

    async def handler(ws: ServerConnection) -> None:
        paths.append(ws.request.path if ws.request else "")
        await ws.send(json.dumps({"type": "auth_required"}))
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "auth_ok"}))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        conn = await connect_ws(f"http://127.0.0.1:{port}")
        try:
            assert await conn.recv_json() == {"type": "auth_required"}
            await conn.send_json({"type": "auth", "access_token": "tok"})
            assert await conn.recv_json() == {"type": "auth_ok"}
        finally:
            await conn.close()
    assert paths == ["/api/websocket"]
    assert received == [{"type": "auth", "access_token": "tok"}]


@pytest.mark.parametrize("payload", ["[1, 2]", "kein json"])
async def test_connect_ws_rejects_non_object_messages(payload: str) -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.send(payload)
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        conn = await connect_ws(f"http://127.0.0.1:{port}")
        try:
            with pytest.raises(ValueError):
                await conn.recv_json()
        finally:
            await conn.close()


async def test_entities_not_resent_after_reconnect_are_stale(ha_env) -> None:
    holder = []
    adapter, _, store = ha_env(
        inbound=[
            *AUTH,
            _added("55"),
            lambda: holder[0].clock.advance(1),
            ConnectionError("weg"),
            *AUTH,  # neue Sitzung: die Entität wird nicht mehr gemeldet
        ]
    )
    holder.append(adapter)

    def stale_after_reconnect() -> bool:
        reading = store.snapshot().get("vehicle.egolf.soc_pct")
        return (
            adapter.health().error_count >= 1
            and adapter.health().connected
            and reading is not None
            and reading.quality is Quality.STALE
        )

    await run_until(adapter, stale_after_reconnect)


async def test_invalid_token_is_logged(ha_env) -> None:
    from structlog.testing import capture_logs

    adapter, _, _ = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_invalid"}])
    with capture_logs() as logs:
        await run_until(adapter, lambda: bool(adapter.sleeps))
    assert any(e["event"] == "Adapter-Fehler" and e["error"] == "HA-Token ungültig" for e in logs)
