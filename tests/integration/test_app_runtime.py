import asyncio
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.app_helpers import PASSWORD, T_APP

from bindaems.app.ha.entities import build_inputs
from bindaems.app.runtime import AppRuntime
from bindaems.shared.config import Config, Secrets
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
        yield ("", b"")  # nie erreicht; macht die Methode zum Generator


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
