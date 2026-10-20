"""Tests der Lern-API: Zustand, Taste „Jetzt neu trainieren“, Auswahl der HA-Sensoren."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.app_helpers import login_as

from bindaems.app.consumers.values import NOT_CONFIGURED
from bindaems.app.forecast.archive import ForecastArchive
from bindaems.app.forecast.load import LoadForecast
from bindaems.app.learning.ha_sensors import HaSensorReader, SensorCandidate
from bindaems.app.learning.registry import ModelRegistry
from bindaems.app.learning.routes import TRAINING_RUNNING, learning_router
from bindaems.app.learning.service import LoadModelService
from bindaems.app.settings.service import SettingsService
from bindaems.shared.timeutil import ManualClock

NOW = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
USERS = {"viewer": "gast", "operator": "bedienen", "admin": "admin"}


class StubLoad:
    """Ersatz für ``LoadForecastService.latest``."""

    def __init__(self) -> None:
        self.forecast: LoadForecast | None = None

    def latest(self) -> LoadForecast | None:
        return self.forecast


class StubSensors(HaSensorReader):
    def __init__(self, found: list[SensorCandidate], error: str | None) -> None:
        super().__init__(None, None)
        self.found, self.error = found, error

    async def candidates(self) -> tuple[list[SensorCandidate], str | None]:
        return self.found, self.error


@pytest.fixture
def load() -> StubLoad:
    return StubLoad()


@pytest.fixture
def model(engine: Engine, settings_service: SettingsService, tmp_path: Path) -> LoadModelService:
    """Das echte ``LoadModelService``; für den Zustand genügen Archiv, Registry, Einstellungen."""
    unused: Any = None
    return LoadModelService(
        load=unused,
        ledger=unused,
        weather=unused,
        forecast=unused,
        sensors=HaSensorReader(None, None),
        archive=ForecastArchive(engine),
        registry=ModelRegistry(engine, tmp_path / "models"),
        settings=settings_service,
        clock=ManualClock(NOW),
    )


@pytest.fixture
def sensors() -> HaSensorReader:
    return HaSensorReader(None, None)


@pytest.fixture
def client(make_client, model, sensors, load, guard) -> TestClient:
    return make_client(learning_router(model, sensors, load, guard))  # type: ignore[arg-type]


@pytest.fixture
def client_as(client, auth) -> Callable[[str], TestClient]:
    def build(role: str) -> TestClient:
        client.headers.update(login_as(client, auth, role, USERS[role]))  # type: ignore[arg-type]
        return client

    return build


def test_learning_status_shape(client_as, model) -> None:
    body = client_as("viewer").get("/api/learning/load").json()
    assert list(body) == [
        "enabled",
        "method",
        "activation",
        "model",
        "backtest",
        "training",
        "sensors",
        "status",
    ]
    assert body["enabled"] is True and body["method"] == "profile"
    assert body["activation"]["reason"] == "no_model" and body["activation"]["active"] is False
    assert body["model"] is None and body["backtest"] is None
    assert body["training"] == {"running": False, "last_at": None, "outcome": None, "text": None}
    assert body["sensors"] == []
    assert body["status"] == {"ok": False, "text": "Noch kein Modell"}
    assert {k: v for k, v in body.items() if k != "method"} == {
        k: v for k, v in model.status().items()
    }


def test_learning_status_names_the_method_of_the_latest_forecast(client_as, load) -> None:
    client = client_as("viewer")
    load.forecast = LoadForecast(NOW, {}, {"ledger": 0, "ha": 0}, "model")
    assert client.get("/api/learning/load").json()["method"] == "model"
    load.forecast = LoadForecast(NOW, {}, {"ledger": 0, "ha": 0})
    assert client.get("/api/learning/load").json()["method"] == "profile"


def test_learning_status_needs_login(client) -> None:
    assert client.get("/api/learning/load").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_train_requires_admin(client_as, model, monkeypatch, role) -> None:
    called: list[int] = []
    monkeypatch.setattr(model, "start_training", lambda: called.append(1) or True)
    response = client_as(role).post("/api/learning/load/train")
    assert response.status_code == 403 and called == []


def test_train_needs_login(client) -> None:
    assert client.post("/api/learning/load/train").status_code == 401


def test_admin_starts_the_training(client_as, model, monkeypatch) -> None:
    monkeypatch.setattr(model, "start_training", lambda: True)
    response = client_as("admin").post("/api/learning/load/train")
    assert response.status_code == 202 and response.json() == {"started": True}


def test_second_train_is_conflict(client_as, model, monkeypatch) -> None:
    answers = iter([True, False])
    monkeypatch.setattr(model, "start_training", lambda: next(answers))
    client = client_as("admin")
    assert client.post("/api/learning/load/train").status_code == 202
    again = client.post("/api/learning/load/train")
    assert again.status_code == 409
    assert (
        again.json() == {"detail": TRAINING_RUNNING} and TRAINING_RUNNING == "Training läuft schon."
    )


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_candidates_require_admin(client_as, role) -> None:
    assert client_as(role).get("/api/learning/candidates").status_code == 403


def test_candidates_need_login(client) -> None:
    assert client.get("/api/learning/candidates").status_code == 401


def test_candidates_without_ha_database(client_as) -> None:
    body = client_as("admin").get("/api/learning/candidates").json()
    assert body == {"entities": [], "error": NOT_CONFIGURED}
    assert NOT_CONFIGURED == "HA-Datenbank nicht eingerichtet (influxdb.ha_database)"


def test_candidates_list_entities_and_units(make_client, model, load, guard, auth) -> None:
    found = [
        SensorCandidate("sensor.aussentemperatur", "°C"),
        SensorCandidate("person.chris", None),
    ]
    client = make_client(learning_router(model, StubSensors(found, None), load, guard))  # type: ignore[arg-type]
    client.headers.update(login_as(client, auth, "admin", "admin"))
    assert client.get("/api/learning/candidates").json() == {
        "entities": [
            {"entity_id": "sensor.aussentemperatur", "unit": "°C"},
            {"entity_id": "person.chris", "unit": None},
        ],
        "error": None,
    }


def test_train_starts_a_real_task_in_the_server_loop(client, auth, model, monkeypatch) -> None:
    release = threading.Event()

    async def slow_training() -> None:
        await asyncio.to_thread(release.wait, 5)

    monkeypatch.setattr(model, "train", slow_training)
    with client:  # eine Ereignisschleife für alle Anfragen, sonst endet die Aufgabe mit der ersten
        client.headers.update(login_as(client, auth, "admin", "admin"))
        first = client.post("/api/learning/load/train")
        assert first.status_code == 202 and model.running()
        assert client.get("/api/learning/load").json()["training"]["running"] is True
        assert client.post("/api/learning/load/train").status_code == 409
        release.set()
        for _ in range(200):
            if not model.running():
                break
            time.sleep(0.01)
        assert not model.running()
        assert client.post("/api/learning/load/train").status_code == 202
