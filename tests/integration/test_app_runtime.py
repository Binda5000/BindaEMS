import asyncio
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.app_helpers import PASSWORD, T_APP
from tests.unit.shared.test_decisions import decision

from bindaems.app.control.decisions import DecisionQuery
from bindaems.app.control.intent import CoreDown
from bindaems.app.ha.entities import build_inputs
from bindaems.app.runtime import AppRuntime
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import Intent
from bindaems.shared.decisions import decision_to_json
from bindaems.shared.timeutil import ManualClock

TOKEN = "x" * 32


def make_runtime(cfg: Config, tmp_path: Path) -> AppRuntime:
    """Laufzeit ohne HTTP-Server; core und MQTT zeigen auf Port 1 (scheitern sofort)."""
    assert cfg.homeassistant is not None and cfg.homeassistant.mqtt is not None
    app = cfg.app.model_copy(
        update={
            "data_dir": tmp_path,
            "backup_dir": tmp_path / "backup",
            "core_url": "http://127.0.0.1:1",
        }
    )
    mqtt = cfg.homeassistant.mqtt.model_copy(update={"host": "127.0.0.1", "port": 1})
    homeassistant = cfg.homeassistant.model_copy(update={"mqtt": mqtt})
    local = cfg.model_copy(update={"app": app, "homeassistant": homeassistant})
    return AppRuntime(
        local,
        Secrets(internal_token=SecretStr(TOKEN)),
        clock=ManualClock(T_APP),
        serve=False,
    )


@pytest.fixture
def runtime(cfg: Config, tmp_path):
    rt = make_runtime(cfg, tmp_path)
    yield rt
    rt.engine.dispose()


class SilentBroker:
    """MQTT-Verbindung, die alles annimmt und keine Nachrichten liefert."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def __aenter__(self) -> "SilentBroker":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def publish(self, topic: str, payload: str, *, retain: bool) -> None:
        self.published.append(topic)

    async def subscribe(self, topic: str) -> None:
        return None

    async def messages(self):
        await asyncio.Event().wait()
        yield ("", b"", False)  # nie erreicht; macht die Methode zum Generator


def test_runtime_wires_routes_and_components(runtime) -> None:
    client = TestClient(runtime.app, base_url="https://testserver")
    runtime.auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    login = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert login.status_code == 200
    body = client.get("/api/system").json()
    assert [c["name"] for c in body["components"]] == [
        "core",
        "prices",
        "forecast",
        "ledger",
        "influx",
        "ha",
    ]
    assert "Admin „chris“ hat keine Zwei-Faktor-Anmeldung (TOTP)." in body["warnings"]
    for path in (
        "/api/settings",
        "/api/consumers",
        "/api/prices",
        "/api/prices/now",
        "/api/forecast/pv",
        "/api/history/catalog",
        "/api/ledger/days?from=2026-10-09&to=2026-10-09",
        "/api/limits",
        "/api/state",
    ):
        assert client.get(path).status_code == 200, path


def test_runtime_wires_the_control_routes(runtime) -> None:
    client = TestClient(runtime.app, base_url="https://testserver")
    runtime.auth.create_user("chef", PASSWORD, "admin", actor="cli", source="cli")
    login = client.post("/api/auth/login", json={"username": "chef", "password": PASSWORD})
    csrf = {"X-CSRF-Token": login.cookies["bindaems_csrf"]}
    # der core ist nicht erreichbar (Port 1): lesen geht, bedienen meldet es
    assert client.get("/api/control").json() == {"core_connected": False, "intent": None}
    assert client.get("/api/decisions/latest").json() == {"decisions": []}
    runtime.decisions.add([decision(id="r1", ts=T_APP)])
    assert [d["id"] for d in client.get("/api/decisions").json()["decisions"]] == ["r1"]
    changed = client.put("/api/control/regulation", json={"on": True}, headers=csrf)
    assert (changed.status_code, changed.json()["detail"]) == (503, "core nicht erreichbar.")
    ack = client.post("/api/control/locks/wallbox.evcs/ack", headers=csrf)
    assert ack.status_code == 503


async def test_decisions_from_the_stream_reach_the_table(runtime) -> None:
    await runtime.live_feed._dispatch_decision(decision_to_json(decision(id="x1", ts=T_APP)))
    await runtime.collector.flush()
    found, _ = runtime.decisions.query(DecisionQuery())
    assert [item.decision.id for item in found] == ["x1"]


@pytest.mark.respx(assert_all_called=False)
async def test_connecting_to_the_core_catches_up_the_decisions(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)
    with capture_logs() as logs:
        await runtime.live_feed._run_connect_handlers()
    assert "Rückstand der Entscheidungen nicht lesbar" in [entry["event"] for entry in logs]


@pytest.mark.respx(assert_all_called=False)
async def test_maintenance_removes_decisions_older_than_the_retention(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)
    keep = timedelta(days=runtime._cfg.app.decisions_keep_days)
    runtime.decisions.add(
        [
            decision(id="old", ts=T_APP - keep - timedelta(days=1)),
            decision(id="young", ts=T_APP - keep + timedelta(days=1)),
        ]
    )
    task = asyncio.create_task(runtime._maintenance_loop())
    try:
        for _ in range(100):
            found, _ = runtime.decisions.query(DecisionQuery())
            if [item.decision.id for item in found] == ["young"]:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
    assert [item.decision.id for item in found] == ["young"]


@pytest.mark.respx(assert_all_called=False)
async def test_runtime_starts_and_stops_cleanly(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)  # kein echter Netzverkehr
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0.3)
    await runtime.shutdown()
    await asyncio.wait_for(task, timeout=2)
    assert runtime.pipeline.status().last_attempt is not None  # Preise wurden versucht


@pytest.mark.respx(assert_all_called=False)
async def test_ha_values_are_built_off_the_event_loop(cfg, tmp_path, respx_mock, monkeypatch):
    # Die Werte für HA lesen die Datenbank; in der Ereignisschleife blockierte das den Server
    respx_mock.route().respond(503)
    broker = SilentBroker()
    monkeypatch.setattr("bindaems.app.runtime.AiomqttHaTransport", lambda *_args: broker)
    threads: list[int] = []

    def spy(*args, **kwargs):
        threads.append(threading.get_ident())
        return build_inputs(*args, **kwargs)

    monkeypatch.setattr("bindaems.app.runtime.build_inputs", spy)
    runtime = make_runtime(cfg, tmp_path)
    task = asyncio.create_task(runtime.run())
    try:
        for _ in range(300):
            if any("/state/" in topic for topic in broker.published):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("keine Zustände veröffentlicht")
    finally:
        await runtime.shutdown()
        await asyncio.wait_for(task, timeout=2)
        runtime.engine.dispose()
    assert threads
    assert threading.get_ident() not in threads


async def test_ha_inputs_carry_the_intent(runtime, monkeypatch) -> None:
    valid = Intent(version=5, control=True)

    async def current() -> Intent | None:
        return valid

    monkeypatch.setattr(runtime.intents, "current", current)
    assert await runtime._ha_intent() == valid
    inputs = runtime._build_ha_inputs(valid)
    assert inputs.intent == valid
    runtime._ha_inputs = inputs
    # nach einem Befehl gilt gleich die neue Absicht des Dienstes
    runtime.intents._cached = Intent(version=6, control=False)
    assert runtime._current_ha_inputs().intent == Intent(version=6, control=False)


async def test_ha_intent_falls_back_when_the_core_has_none_or_is_down(runtime, monkeypatch) -> None:
    async def none() -> Intent | None:
        return None

    monkeypatch.setattr(runtime.intents, "current", none)
    assert await runtime._ha_intent() == Intent(version=1)  # Basis ohne gültige Absicht

    async def down() -> Intent | None:
        raise CoreDown

    runtime.intents._cached = Intent(version=3)
    monkeypatch.setattr(runtime.intents, "current", down)
    assert await runtime._ha_intent() == Intent(version=3)  # zuletzt bekannte

    async def broken() -> Intent | None:
        raise ValueError("kaputt")

    monkeypatch.setattr(runtime.intents, "current", broken)
    with capture_logs() as logs:
        assert await runtime._ha_intent() == Intent(version=3)
    assert logs[0]["event"] == "Absicht für Home Assistant nicht bestimmbar"


def test_runtime_wires_the_ha_commands(runtime) -> None:
    assert runtime.ha_publisher is not None
    assert runtime.ha_publisher._commands is not None
