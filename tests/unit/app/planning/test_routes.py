"""Tests der Plan-API: Plan lesen, neu planen, Schalter Netzladen (Plan 3b, Task 11)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import login_as

from bindaems.app.planning.explain import slot_text
from bindaems.app.planning.routes import planning_router
from bindaems.app.settings.service import SettingsService, VersionConflictError

USERS = {"viewer": "gast", "operator": "bedienen", "admin": "admin"}


@pytest.fixture
def planning(service):
    return service


@pytest.fixture
def client(make_client, planning, settings_service, clock, guard) -> TestClient:
    return make_client(planning_router(planning, settings_service, clock, guard))


@pytest.fixture
def client_as(client, auth) -> Callable[[str], TestClient]:
    """Meldet einen Benutzer der Rolle an; Schreibzugriffe tragen den CSRF-Header mit."""

    def build(role: str) -> TestClient:
        headers = login_as(client, auth, role, USERS[role])  # type: ignore[arg-type]
        client.headers.update(headers)
        return client

    return build


def conflict_once(monkeypatch: pytest.MonkeyPatch, settings: SettingsService) -> None:
    """Das erste ``update`` scheitert mit ``VersionConflictError``."""
    real = settings.update
    calls: list[int] = []

    def update(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise VersionConflictError(settings.current().version)
        return real(*args, **kwargs)

    monkeypatch.setattr(settings, "update", update)


def conflict_always(monkeypatch: pytest.MonkeyPatch, settings: SettingsService) -> None:
    def update(*args, **kwargs):
        raise VersionConflictError(settings.current().version)

    monkeypatch.setattr(settings, "update", update)


def test_get_plan_without_plan(client_as):
    body = client_as("viewer").get("/api/plan").json()
    assert body == {
        "status": {"ok": False, "message": "Noch kein Plan", "running": False},
        "grid_charge_allowed": False,
        "plan": None,
    }


def test_get_plan_requires_login(client):
    assert client.get("/api/plan").status_code == 401


async def test_get_plan_shape(client_as, planning):
    await planning.replan("manual")
    body = client_as("viewer").get("/api/plan").json()
    plan = body["plan"]
    assert body["status"]["ok"] is True and body["status"]["running"] is False
    assert plan["source"] == "optimizer" and plan["valid"] is True
    assert set(plan["slots"][0]) == {
        "start",
        "intent",
        "grid_charge_w",
        "soc_pct",
        "pv_w",
        "load_w",
        "import_w",
        "export_w",
        "battery_w",
        "import_ct",
        "feed_in_ct",
        "estimated",
        "budget_kw",
        "codes",
        "text",
    }
    assert set(plan) == {
        "id",
        "created_at",
        "trigger",
        "source",
        "solver_status",
        "fallback_reason",
        "valid_until",
        "valid",
        "horizon_end",
        "reserve_pct",
        "soc_max_pct",
        "month_peak_kw",
        "power_price",
        "expected_cost_eur",
        "baseline_cost_eur",
        "issues",
        "slots",
    }


async def test_get_plan_values_follow_the_plan(client_as, planning):
    stored = await planning.replan("manual")
    assert stored is not None
    plan = client_as("viewer").get("/api/plan").json()["plan"]
    assert plan["id"] == stored.id and plan["trigger"] == "manual"
    assert plan["expected_cost_eur"] == round(stored.expected_cost_ct / 100, 2)
    assert plan["baseline_cost_eur"] == round(stored.baseline_cost_ct / 100, 2)
    assert len(plan["slots"]) == len(stored.slots)
    assert plan["horizon_end"] == stored.horizon_end.isoformat()
    for item, slot in zip(plan["slots"], stored.slots, strict=True):
        assert item["start"] == slot.start.isoformat() and item["intent"] == slot.intent.kind
        assert item["soc_pct"] == slot.soc_end_pct
        assert item["battery_w"] == slot.charge_pv_w + slot.charge_grid_w - slot.discharge_w
        assert item["codes"] == [reason.code for reason in slot.reasons]
        assert item["text"] == slot_text(slot)


async def test_get_plan_marks_expired_plan(client_as, planning, clock):
    from datetime import timedelta

    await planning.replan("manual")
    clock.advance(timedelta(hours=2))
    body = client_as("viewer").get("/api/plan").json()
    assert body["plan"]["valid"] is False
    assert body["status"]["message"].startswith("Plan abgelaufen seit ")


def test_get_plan_shows_the_current_switch(client_as, planning, settings_service):
    planning.set_grid_charge(True, actor="x", source="ui")
    assert client_as("viewer").get("/api/plan").json()["grid_charge_allowed"] is True


def test_replan_requires_operator(client_as, planning):
    assert client_as("viewer").post("/api/plan/replan").status_code == 403
    assert planning.pending() is None
    response = client_as("operator").post("/api/plan/replan")
    assert response.status_code == 202 and response.json() == {"queued": True}
    assert planning.pending() == "manual"


def test_grid_charge_switch_creates_settings_version(client_as, settings_service, audit):
    body = client_as("operator").put("/api/plan/grid-charge", json={"on": True}).json()
    current = settings_service.current()
    assert body == {"grid_charge_allowed": True, "settings_version": current.version}
    assert (current.actor, current.source, current.comment) == ("bedienen", "ui", "Netzladen ein")
    assert current.settings.planning.grid_charge_allowed is True


def test_grid_charge_switch_off_comment(client_as, settings_service):
    api = client_as("operator")
    api.put("/api/plan/grid-charge", json={"on": True})
    api.put("/api/plan/grid-charge", json={"on": False})
    assert settings_service.current().comment == "Netzladen aus"


def test_grid_charge_same_value_creates_no_version(client_as, settings_service):
    before = settings_service.current().version
    body = client_as("operator").put("/api/plan/grid-charge", json={"on": False}).json()
    assert settings_service.current().version == before
    assert body == {"grid_charge_allowed": False, "settings_version": before}


def test_grid_charge_requires_operator(client_as, settings_service):
    before = settings_service.current().version
    assert client_as("viewer").put("/api/plan/grid-charge", json={"on": True}).status_code == 403
    assert settings_service.current().version == before


def test_grid_charge_rejects_bad_body(client_as):
    api = client_as("operator")
    assert api.put("/api/plan/grid-charge", json={"on": "vielleicht"}).status_code == 422
    assert api.put("/api/plan/grid-charge", json={}).status_code == 422


def test_grid_charge_retries_once_on_conflict(planning, settings_service, monkeypatch):
    conflict_once(monkeypatch, settings_service)
    version = planning.set_grid_charge(True, actor="x", source="ui")
    assert version.settings.planning.grid_charge_allowed
    assert settings_service.current().version == version.version


def test_grid_charge_conflict_after_retry_is_409(client_as, settings_service, monkeypatch):
    conflict_always(monkeypatch, settings_service)
    response = client_as("operator").put("/api/plan/grid-charge", json={"on": True})
    assert response.status_code == 409
    assert "Die Einstellungen wurden inzwischen geändert" in response.json()["detail"]


def test_grid_charge_switch_requests_a_replan(client_as, planning):
    client_as("operator").put("/api/plan/grid-charge", json={"on": True})
    assert planning.pending() == "settings"
