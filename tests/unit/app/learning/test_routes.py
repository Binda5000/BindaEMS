"""Tests der Lern-API: Zustand, Taste „Jetzt neu trainieren“, Auswahl der HA-Sensoren."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.app_helpers import T_APP, login_as
from tests.unit.app.control.conftest import FakeCoreClient, FakeFeed
from tests.unit.app.learning.conftest import FakeReader
from tests.unit.app.learning.test_driving_auto import commuter
from tests.unit.app.learning.test_driving_service import put_row

from bindaems.app.audit import AuditLog
from bindaems.app.consumers.values import NOT_CONFIGURED
from bindaems.app.control.intent import IntentService
from bindaems.app.core_link import LiveState
from bindaems.app.forecast.archive import ForecastArchive
from bindaems.app.forecast.load import LoadForecast
from bindaems.app.learning.driving_auto import DrivingAutomation
from bindaems.app.learning.driving_service import DrivingService
from bindaems.app.learning.ha_sensors import HaSensorReader, SensorCandidate
from bindaems.app.learning.registry import ModelRegistry
from bindaems.app.learning.routes import RECOMPUTE_RUNNING, TRAINING_RUNNING, learning_router
from bindaems.app.learning.service import LoadModelService
from bindaems.app.settings.service import SettingsService
from bindaems.shared.config import Config
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
def driving(
    engine: Engine, cfg: Config, settings_service: SettingsService, clock: ManualClock
) -> DrivingService:
    """Der echte Dienst; die Quellen antworten leer, die Stände kommen aus der Datenbank."""
    return DrivingService(
        reader=FakeReader(),  # type: ignore[arg-type]
        engine=engine,
        cfg=cfg,
        settings=settings_service,
        clock=clock,
    )


@pytest.fixture
def automation(
    engine: Engine,
    cfg: Config,
    settings_service: SettingsService,
    audit: AuditLog,
    clock: ManualClock,
    driving: DrivingService,
) -> DrivingAutomation:
    intents = IntentService(FakeCoreClient(), LiveState(), FakeFeed(), audit, clock, cfg)  # type: ignore[arg-type]
    return DrivingAutomation(
        intents=intents,
        live=LiveState(),
        profiles=driving.profile,
        charges=dict,
        settings=settings_service,
        audit=audit,
        engine=engine,
        cfg=cfg,
        clock=clock,
    )


@pytest.fixture
def client(make_client, model, sensors, load, guard, driving, automation) -> TestClient:
    return make_client(learning_router(model, sensors, load, guard, driving, automation))  # type: ignore[arg-type]


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
    assert body["status"] == {"ok": True, "text": "Noch kein Modell"}
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


def test_candidates_without_reader_name_the_reason(
    make_client, model, load, guard, auth, driving, automation
) -> None:
    # Datenbank benannt, aber kein Leser: sonst hielte der Editor alle Sensoren für verschwunden
    client = make_client(
        learning_router(
            model,
            HaSensorReader(None, "homeassistant"),
            load,
            guard,
            driving,
            automation,  # type: ignore[arg-type]
        )
    )
    client.headers.update(login_as(client, auth, "admin", "admin"))
    assert client.get("/api/learning/candidates").json() == {
        "entities": [],
        "error": "Home Assistant ist nicht eingerichtet.",
    }


def test_candidates_list_entities_and_units(
    make_client, model, load, guard, auth, driving, automation
) -> None:
    found = [
        SensorCandidate("sensor.aussentemperatur", "°C"),
        SensorCandidate("person.chris", None),
    ]
    client = make_client(
        learning_router(model, StubSensors(found, None), load, guard, driving, automation)  # type: ignore[arg-type]
    )
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


# --- Fahrverhalten (Plan 5b, Präzisierung 11 und 12) -------------------------------------------

DRIVING_KEYS = [
    "computed_at",
    "running",
    "status",
    "vehicles",
]
VEHICLE_KEYS = [
    "key",
    "name",
    "source",
    "data_from",
    "data_to",
    "days",
    "weekdays",
    "next_trip",
    "presence",
    "quality",
    "automation",
]
AUTOMATION_KEYS = ["enabled", "state", "text", "paused_until", "last_set_at"]


async def with_profile(driving: DrivingService, engine: Engine) -> None:
    """Der Tesla hat einen Stand (Pendler, Anwesenheit überall ein Drittel), der e-Golf keinen."""
    profile = commuter().model_copy(update={"presence": [[1 / 3] * 96 for _ in range(7)]})
    put_row(engine, "tesla", T_APP - timedelta(hours=6), profile)
    await driving.load_stored()


async def test_driving_status_shape(client_as, driving, engine) -> None:
    await with_profile(driving, engine)
    body = client_as("viewer").get("/api/learning/driving").json()
    assert list(body) == DRIVING_KEYS
    assert body["running"] is False
    assert body["computed_at"] == (T_APP - timedelta(hours=6)).isoformat()
    assert body["status"] == {"ok": True, "text": "Fahrverhalten berechnet am 09.10. 04:00."}
    tesla, egolf = body["vehicles"]
    assert list(tesla) == VEHICLE_KEYS and tesla["name"] == "Tesla Model 3"
    assert (tesla["key"], tesla["source"], tesla["days"]) == ("tesla", "tessie", 56)
    assert tesla["data_to"] == "2026-10-09T08:00:00+00:00"  # ISO 8601 in UTC
    assert len(tesla["weekdays"]) == 7 and tesla["weekdays"][0]["departure"] == "07:15"
    assert tesla["next_trip"] is not None and tesla["next_trip"]["date"] == "2026-10-12"
    assert [egolf["key"], egolf["name"]] == ["egolf", "e-Golf"]


async def test_presence_lists_the_next_48_hours_in_quarter_hours(
    client_as, driving, engine
) -> None:
    await with_profile(driving, engine)
    tesla = client_as("viewer").get("/api/learning/driving").json()["vehicles"][0]
    presence = tesla["presence"]
    assert len(presence) == 192
    assert presence[0] == {"start": "2026-10-09T08:00:00+00:00", "p": 0.33}  # auf 2 Stellen
    assert presence[1]["start"] == "2026-10-09T08:15:00+00:00"
    assert presence[-1]["start"] == "2026-10-11T07:45:00+00:00"
    assert all(list(item) == ["start", "p"] and item["p"] == 0.33 for item in presence)


def test_vehicle_without_profile_has_zero_days(client_as) -> None:
    body = client_as("viewer").get("/api/learning/driving").json()
    assert body["computed_at"] is None
    assert body["status"] == {"ok": True, "text": "Noch keine Fahrdaten."}
    for vehicle in body["vehicles"]:
        assert list(vehicle) == VEHICLE_KEYS
        assert (vehicle["source"], vehicle["data_from"], vehicle["data_to"]) == (None, None, None)
        assert (vehicle["days"], vehicle["weekdays"], vehicle["presence"]) == (0, [], [])
        assert vehicle["next_trip"] is None


def test_every_vehicle_carries_its_automation(client_as, automation, settings_service) -> None:
    client = client_as("viewer")
    egolf = client.get("/api/learning/driving").json()["vehicles"][1]
    assert list(egolf["automation"]) == AUTOMATION_KEYS
    assert egolf["automation"] == {
        "enabled": False,
        "state": "off",
        "text": "Automatik aus.",
        "paused_until": None,
        "last_set_at": None,
    }
    switch_on(settings_service, "tesla")
    paused_until = T_APP + timedelta(hours=3)
    automation.on_manual_target("tesla", paused_until)  # Bedienung von Hand
    tesla = client.get("/api/learning/driving").json()["vehicles"][0]
    assert tesla["automation"]["enabled"] is True and tesla["automation"]["state"] == "paused"
    assert tesla["automation"]["paused_until"] == paused_until.isoformat()
    assert tesla["automation"]["last_set_at"] is None


def switch_on(settings_service: SettingsService, key: str) -> None:
    current = settings_service.current()
    driving = current.settings.learning.driving
    learning = current.settings.learning.model_copy(
        update={"driving": driving.model_copy(update={"automation": {key: True}})}
    )
    settings_service.update(
        current.settings.model_copy(update={"learning": learning}),
        base_version=current.version,
        actor="test",
        source="cli",
    )


def test_driving_status_needs_login(client) -> None:
    assert client.get("/api/learning/driving").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_recompute_needs_admin(client_as, driving, monkeypatch, role) -> None:
    called: list[int] = []
    monkeypatch.setattr(driving, "start_recompute", lambda: called.append(1) or True)
    response = client_as(role).post("/api/learning/driving/recompute")
    assert response.status_code == 403 and called == []


def test_recompute_needs_login(client) -> None:
    assert client.post("/api/learning/driving/recompute").status_code == 401


def test_admin_starts_the_recompute(client_as, driving, monkeypatch) -> None:
    monkeypatch.setattr(driving, "start_recompute", lambda: True)
    response = client_as("admin").post("/api/learning/driving/recompute")
    assert response.status_code == 202 and response.json() == {"started": True}


def test_recompute_while_running_is_409(client, auth, driving, fake_reader, monkeypatch) -> None:
    release = threading.Event()

    async def slow(*args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(release.wait, 5)

    monkeypatch.setattr(driving, "_compute_all", slow)
    with client:  # eine Ereignisschleife für alle Anfragen
        client.headers.update(login_as(client, auth, "admin", "admin"))
        first = client.post("/api/learning/driving/recompute")
        assert first.status_code == 202 and driving.running()
        assert client.get("/api/learning/driving").json()["running"] is True
        again = client.post("/api/learning/driving/recompute")
        assert again.status_code == 409
        assert again.json() == {"detail": RECOMPUTE_RUNNING}
        assert RECOMPUTE_RUNNING == "Berechnung läuft schon."
        release.set()
        for _ in range(200):
            if not driving.running():
                break
            time.sleep(0.01)
        assert not driving.running()
        assert client.post("/api/learning/driving/recompute").status_code == 202
