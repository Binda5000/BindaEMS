import copy
from datetime import timedelta

import pytest
from tests.app_helpers import login_as
from tests.unit.app.ops.test_check import _health
from tests.unit.shared.test_decisions import decision

from bindaems.app.control.decisions import DecisionStore
from bindaems.app.core_link import LiveState
from bindaems.app.ops.routes import ops_router
from bindaems.app.status import ComponentStatus
from bindaems.shared.timeutil import ManualClock


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.health = copy.deepcopy(_health())
    return state


@pytest.fixture
def store(engine, clock: ManualClock) -> DecisionStore:
    return DecisionStore(engine, clock)


@pytest.fixture
def client(make_client, live, guard, store, clock):
    def ok_source() -> ComponentStatus:
        return ComponentStatus("prices", True, "Preise bis 10.10. 23:45")

    return make_client(
        ops_router(live=live, guard=guard, sources=[ok_source], decisions=store, clock=clock)
    )


def test_check_route_requires_login(client) -> None:
    assert client.get("/api/ops/check").status_code == 401


def test_check_route_returns_level_and_items(client, auth) -> None:
    login_as(client, auth, "viewer")
    response = client.get("/api/ops/check")
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "ok"
    ids = [item["id"] for item in body["items"]]
    assert {"core", "mode", "watchdog"} <= set(ids)


def test_check_route_without_core(client, auth, live) -> None:
    live.connected = False
    login_as(client, auth, "viewer")
    assert client.get("/api/ops/check").json()["level"] == "error"


def test_check_route_counts_the_last_hour(client, auth, store, clock) -> None:
    now = clock.now()
    store.add(
        [decision(id=f"e{i}", ts=now - timedelta(minutes=1 + i)) for i in range(25)]
        + [decision(id="old", ts=now - timedelta(hours=2))]
    )
    login_as(client, auth, "viewer")
    items = {i["id"]: i for i in client.get("/api/ops/check").json()["items"]}
    assert items["evcs_rate"]["level"] == "warn"


def test_check_route_survives_a_database_error(client, auth, store, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(store, "counts", boom)
    login_as(client, auth, "viewer")
    response = client.get("/api/ops/check")
    assert response.status_code == 200
    items = {i["id"]: i for i in response.json()["items"]}
    assert items["evcs_rate"]["level"] == "unknown"
