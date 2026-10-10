"""Antworten der API in den Formen, die das UI erwartet (ui/src/lib/api/contract/).

Neu schreiben: UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py
"""

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.ui_world import DEMO_PASSWORD, DEMO_TOTP_SECRET, T_DEMO, World, build_world

from bindaems.shared.domain import Alarm, Severity

CONTRACT = Path("ui/src/lib/api/contract")
GET_ENDPOINTS = {
    "me": "/api/auth/me",
    "state": "/api/state",
    "system": "/api/system",
    "settings": "/api/settings",
    "settings-versions": "/api/settings/versions",
    "limits": "/api/limits",
    "consumers": "/api/consumers",
    "consumer-candidates": "/api/consumers/candidates",
    "history-catalog": "/api/history/catalog",
    "history": "/api/history?series=grid,soc.battery&from=2026-10-09T06:00:00Z"
    "&to=2026-10-09T08:00:00Z",
    "prices": "/api/prices",
    "prices-now": "/api/prices/now",
    "forecast": "/api/forecast/pv",
    "ledger-days": "/api/ledger/days?from=2026-10-08&to=2026-10-09",
    "users": "/api/users",
    "audit": "/api/audit",
}
OTHER = {
    "live-hello",
    "live-state",
    "live-core",
    "live-alarm",
    "health",
    "totp-setup",
    "reprice",
    "error-422",
}


def collect_responses(world: World) -> dict[str, object]:
    """Antworten aller Vertragsdateien, angemeldet als ``admin``."""
    client = TestClient(world.app.app, base_url="https://testserver")
    login = client.post("/api/auth/login", json={"username": "admin", "password": DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    csrf = {"X-CSRF-Token": client.cookies["bindaems_csrf"]}
    actual: dict[str, object] = {}
    for name, path in GET_ENDPOINTS.items():
        response = client.get(path)
        assert response.status_code == 200, (name, response.text)
        actual[name] = response.json()

    cookie = f"bindaems_session={client.cookies['bindaems_session']}"
    with client.websocket_connect("/api/live", headers={"cookie": cookie}) as ws:
        actual["live-hello"] = ws.receive_json()
    actual["live-state"] = json.loads(json.dumps({"type": "state", "data": world.app.live.state}))

    current: Any = copy.deepcopy(actual["settings"])
    for component in current["settings"]["tariff"]["components"]:
        if component["id"] == "grid_usage":
            component["value_ct"] = "abc"
    body = {"base_version": current["version"], "settings": current["settings"]}
    rejected = client.put("/api/settings", json=body, headers=csrf)
    assert rejected.status_code == 422, rejected.text
    actual["error-422"] = rejected.json()

    health = client.get("/health")
    assert health.status_code == 200, health.text
    actual["health"] = health.json()
    setup = client.post("/api/auth/totp/setup", json={"password": DEMO_PASSWORD}, headers=csrf)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]  # bei jedem Lauf neu: für den Vertrag zählt die Form
    actual["totp-setup"] = {
        "secret": DEMO_TOTP_SECRET,
        "uri": setup.json()["uri"].replace(secret, DEMO_TOTP_SECRET),
    }
    days = {"from": "2026-10-09", "to": "2026-10-09"}
    repriced = client.post("/api/ledger/reprice", json=days, headers=csrf)
    assert repriced.status_code == 200, repriced.text
    actual["reprice"] = repriced.json()
    return actual


async def live_messages(world: World) -> dict[str, object]:
    """``alarm`` und ``core`` so, wie sie über /api/live kommen; je Alarmstufe ein Alarm."""
    alarms = [
        Alarm(f"demo.{severity.value}", severity, f"Beispiel ({severity.value})", T_DEMO)
        for severity in Severity
    ]
    async with world.core.subscribe() as core_stream, world.app.live.subscribe() as ui_stream:
        world.core._update_alarms(alarms)  # Serialisierung des core
        await world.app.live_feed._dispatch(await asyncio.wait_for(anext(core_stream), 2))
        alarm = await asyncio.wait_for(anext(ui_stream), 2)  # von der app weitergereicht
        world.app.live_feed._disconnected()
        core = await asyncio.wait_for(anext(ui_stream), 2)
        world.app.live_feed._connected()
    return {"live-alarm": alarm, "live-core": core}


def matches_contract(name: str, body: object) -> bool:
    """Vergleicht mit der Vertragsdatei; mit ``UPDATE_UI_CONTRACT=1`` wird sie neu geschrieben."""
    path = CONTRACT / f"{name}.json"
    # Reihenfolge wie in der Antwort: sie ist Teil des Vertrags (z. B. die Fahrzeuge), daher
    # Vergleich als Text statt als Objekt
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_UI_CONTRACT") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    return path.is_file() and path.read_text(encoding="utf-8") == text


async def test_api_responses_match_ui_contract(cfg, tmp_path) -> None:
    world = await build_world(cfg, tmp_path)
    try:
        actual = await asyncio.to_thread(collect_responses, world)  # GET, WebSocket, POST, 422
        actual.update(await live_messages(world))
    finally:
        world.close()
    mismatched = [name for name, body in actual.items() if not matches_contract(name, body)]
    assert sorted(actual) == sorted(GET_ENDPOINTS.keys() | OTHER)
    assert mismatched == []


async def test_demo_world_is_deterministic(cfg, tmp_path) -> None:
    # Sonst wechselten die Vertragsdateien bei jedem Lauf
    bodies = []
    for name in ("a", "b"):
        world = await build_world(cfg, tmp_path / name)
        try:
            body = await asyncio.to_thread(collect_responses, world)
            body.update(await live_messages(world))
            bodies.append(body)
        finally:
            world.close()
    assert bodies[0] == bodies[1]


def test_no_stale_contract_files() -> None:
    names = {path.stem for path in CONTRACT.glob("*.json")}
    assert names == GET_ENDPOINTS.keys() | OTHER


def test_contract_comparison_respects_key_order(tmp_path, monkeypatch) -> None:
    # Die Reihenfolge ist Teil des Vertrags: das UI zeigt z. B. die Fahrzeuge so, wie sie kommen
    monkeypatch.setitem(globals(), "CONTRACT", tmp_path)
    monkeypatch.delenv("UPDATE_UI_CONTRACT", raising=False)
    (tmp_path / "x.json").write_text(json.dumps({"a": 1, "b": 2}, indent=2) + "\n")
    assert matches_contract("x", {"a": 1, "b": 2})
    assert not matches_contract("x", {"b": 2, "a": 1})
