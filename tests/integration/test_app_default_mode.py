"""Standard-Lademodus je Ladepunkt in der app (Plan Standard-Lademodus, Task 3).

Die app läuft mit einem nachgebildeten core hinter respx (``GET``/``PUT /v1/intent``); der
Tracker beobachtet wie im Betrieb nach jeder Antwort von Health.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import PASSWORD
from tests.helpers import stop
from tests.integration.test_app_runtime import make_runtime
from tests.plan_helpers import until

from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent

CORE = "http://127.0.0.1:1"


class FakeCore:
    """Die gültige Absicht des core: ``GET`` liefert sie, ``PUT`` nimmt eine höhere Version an."""

    def __init__(self, intent: Intent) -> None:
        self.intent = intent
        self.sent: list[Intent] = []

    def get(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"intent": self.intent.model_dump(mode="json")})

    def put(self, request: httpx.Request) -> httpx.Response:
        intent = Intent.model_validate(json.loads(request.content))
        self.sent.append(intent)
        if intent.version <= self.intent.version:
            body = {"result": "rejected", "version": self.intent.version, "details": []}
            return httpx.Response(409, json=body)
        self.intent = intent
        return httpx.Response(200, json={"result": "accepted", "version": intent.version})


def observe_twc(runtime, *, connected: bool) -> None:
    """Eine Antwort von Health: der Tesla am Wall Connector oder dieser frei."""
    runtime.live.connected = True
    signal = {"v": connected, "q": "ok"}
    runtime.live.state = {"signals": {"wallbox.twc.vehicle_connected": signal}}
    occupant = {"occupant": "tesla", "assignment": "auto"} if connected else {}
    runtime.live.health = {"wallboxes": [{"id": "twc", **occupant}]}
    for handler in runtime.live_feed.health_handlers:
        handler()


@pytest.mark.respx(assert_all_called=False)
async def test_unplug_switches_mode_to_wallbox_default(cfg: Config, tmp_path, respx_mock) -> None:
    runtime = make_runtime(cfg, tmp_path)
    core = FakeCore(Intent(version=5, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)}))
    respx_mock.get(f"{CORE}/v1/intent").mock(side_effect=core.get)
    respx_mock.put(f"{CORE}/v1/intent").mock(side_effect=core.put)
    client = TestClient(runtime.app, base_url="https://testserver")
    runtime.auth.create_user("chef", PASSWORD, "admin", actor="cli", source="cli")
    login = client.post("/api/auth/login", json={"username": "chef", "password": PASSWORD})
    csrf = {"X-CSRF-Token": login.cookies["bindaems_csrf"]}
    current = client.get("/api/settings").json()
    settings = current["settings"] | {"wallbox_default_modes": {"twc": "pv"}}
    body = {"base_version": current["version"], "settings": settings}
    assert client.put("/api/settings", json=body, headers=csrf).status_code == 200
    await runtime.intents.current()  # die gültige Absicht im Cache, wie nach dem Verbinden
    tracker = asyncio.create_task(runtime.charging.run())
    try:
        observe_twc(runtime, connected=True)
        await until(lambda: len(runtime.charging.open_sessions()) == 1)
        assert core.sent == []
        runtime._clock.advance(timedelta(minutes=5))
        observe_twc(runtime, connected=False)
        await until(lambda: bool(core.sent))
    finally:
        await stop(tracker)
        runtime.engine.dispose()
    [sent] = core.sent
    assert sent.vehicles["tesla"].mode == ChargeMode.PV
    assert core.intent == sent
    entries = [e for e in runtime.audit.recent() if e.actor == "Standard-Lademodus"]
    assert [(e.source, e.action, e.target) for e in entries] == [("system", "intent.mode", "tesla")]
    assert (entries[0].details or {})["to"] == "pv"
