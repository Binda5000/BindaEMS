"""Tests der Plan-API: Plan lesen, neu planen, Schalter Netzladen (Plan 3b, Task 11)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import ingest, login_as
from tests.plan_helpers import planned_health, sample_inputs, sample_plan, vehicle_health

from bindaems.app.planning.explain import slot_text
from bindaems.app.planning.routes import planning_router
from bindaems.app.settings.service import SettingsService, VersionConflictError
from bindaems.shared.plan import PlanThermal

USERS = {"viewer": "gast", "operator": "bedienen", "admin": "admin"}


@pytest.fixture
def planning(service):
    return service


@pytest.fixture
def client(
    make_client, planning, settings_service, live, clock, guard, ledger, store
) -> TestClient:
    return make_client(
        planning_router(planning, settings_service, live, clock, guard, ledger=ledger, store=store)
    )


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
        "execution": None,
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
        "support_soc_pct",
        "ev_w",
        "thermal_w",
        "thermal_steps",
        "thermal_grid_w",
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
        "vehicles",
        "thermal",
        "slots",
    }
    assert plan["vehicles"] == [] and plan["slots"][0]["ev_w"] == {}
    # ohne Klimaanlage: kein Block, keine Verschiebung in den Slots
    assert plan["thermal"] is None
    assert (plan["slots"][0]["thermal_w"], plan["slots"][0]["thermal_steps"]) == (0.0, None)
    assert plan["slots"][0]["thermal_grid_w"] == 0.0


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
        assert item["support_soc_pct"] == slot.support_soc_pct
        assert item["battery_w"] == slot.charge_pv_w + slot.charge_grid_w - slot.discharge_w
        assert item["codes"] == [reason.code for reason in slot.reasons]
        assert item["text"] == slot_text(slot)


async def test_get_plan_shows_the_planned_cars(client_as, planning, live):
    live.health = planned_health(vehicle_health("tesla"), version=3)
    stored = await planning.replan("manual")
    assert stored is not None and [car.key for car in stored.vehicles] == ["tesla"]
    plan = client_as("viewer").get("/api/plan").json()["plan"]
    (car,) = plan["vehicles"]
    source = stored.vehicles[0]
    assert car == {
        "key": "tesla",
        "name": source.name,
        "wallbox": "twc",
        "target_soc_pct": 80.0,
        "departure_at": source.departure_at.isoformat(),
        "soc_start_pct": 40.0,
        "soc_assumed": False,
        "limit_pct": None,
        "need_kwh": source.need_kwh,
        "planned_kwh": source.planned_kwh,
        "shortfall_kwh": source.shortfall_kwh,
        "expected_soc_pct": source.expected_soc_pct,
        "finish_at": source.finish_at.isoformat() if source.finish_at else None,
        "reachable": source.reachable,
        "expected": False,
        "arrival_at": None,
    }
    assert car["departure_at"].endswith("+00:00") and car["planned_kwh"] > 0
    for item, slot in zip(plan["slots"], stored.slots, strict=True):
        assert item["ev_w"] == slot.ev_w
    assert any(item["ev_w"].get("tesla", 0) > 0 for item in plan["slots"])


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


def core_holds(live, plan_id: int, **changes) -> None:
    """Health des core: Absicht mit Regelbetrieb und dem Plan ``plan_id``, der dort gilt, Victron
    im Dry-Run."""
    live.connected = True
    live.health = {
        "mode": "CONTROL",
        "intent": {
            "control": True,
            "plan": {"id": plan_id, "grid_charge_allowed": False},
        },
        "actuators": [{"id": "victron", "effective": "dry_run", "locked": []}],
        "plan": {"id": plan_id, "state": "valid"},
        **changes,
    }


async def test_plan_route_reports_the_execution(client_as, planning, live):
    plan = await planning.replan("manual")
    assert plan is not None
    api = client_as("viewer")
    live.health = None  # verbunden, aber noch ohne Health
    assert api.get("/api/plan").json()["execution"] == {
        "state": "pending",
        "text": "Der Plan ist noch nicht beim core.",
    }
    core_holds(live, plan.id)
    assert api.get("/api/plan").json()["execution"] == {
        "state": "dry_run",
        "text": "Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron.",
    }
    core_holds(live, plan.id, mode="SAFE")
    assert api.get("/api/plan").json()["execution"] == {
        "state": "idle",
        "text": "Der core regelt gerade nicht (SAFE).",
    }
    # der core hat den Plan, er gilt dort aber nicht (etwa eine Uhrabweichung; Gesamtreview)
    core_holds(live, plan.id, plan={"id": plan.id, "state": "expired"})
    assert api.get("/api/plan").json()["execution"]["state"] == "pending"
    live.connected = False
    assert api.get("/api/plan").json()["execution"]["state"] == "unreachable"


async def test_plan_route_follows_the_grid_charge_switch(client_as, planning, live):
    plan = await planning.replan("manual")
    assert plan is not None
    core_holds(live, plan.id)
    api = client_as("viewer")
    assert api.get("/api/plan").json()["execution"]["state"] == "dry_run"
    planning.set_grid_charge(True, actor="x", source="ui")  # der core hat den Plan ohne Netzladen
    assert api.get("/api/plan").json()["execution"]["state"] == "pending"


async def test_plan_route_has_no_execution_for_an_expired_plan(client_as, planning, live, clock):
    from datetime import timedelta

    plan = await planning.replan("manual")
    assert plan is not None
    core_holds(live, plan.id)
    clock.advance(timedelta(hours=2))
    body = client_as("viewer").get("/api/plan").json()
    assert body["plan"]["valid"] is False and body["execution"] is None


# --- Plan gegen Ist (Plan 3c, Task 14); die Uhr steht auf 2026-10-16T10:01Z -------------------


def test_review_requires_login(client):
    assert client.get("/api/plan/review").status_code == 401


def test_review_defaults_to_today_up_to_now(client_as):
    body = client_as("viewer").get("/api/plan/review").json()
    assert body["day"] == "2026-10-16"
    # 00:00 Ortszeit (22:00Z) bis zum letzten vollen Slot 09:45Z
    assert len(body["slots"]) == 48
    assert body["slots"][-1]["start"] == "2026-10-16T09:45:00+00:00"
    assert set(body["totals"]) == {"planned", "actual"}


def test_review_joins_plan_and_ledger(client_as, store, ledger):
    store.save(sample_plan("2026-10-16T09:30:00+00:00", ["09:30", "09:45"]), sample_inputs())
    ingest(ledger, "2026-10-16T09:45:00+00:00", {"grid>house": 100.0, "grid>battery": 200.0})
    body = client_as("viewer").get("/api/plan/review?day=2026-10-16").json()
    last = body["slots"][-1]
    assert last["start"] == "2026-10-16T09:45:00+00:00"
    assert last["planned"]["plan_id"] == 1 and last["planned"]["intent"] == "EIGENVERBRAUCH"
    assert last["actual"]["grid_charge_w"] == 800.0 and last["actual"]["battery_w"] == 800.0
    assert body["slots"][-2]["planned"] is not None and body["slots"][-2]["actual"] is None


def test_review_of_a_past_day_has_all_slots(client_as, clock):
    clock.advance(timedelta(days=11))  # 2026-10-27
    api = client_as("viewer")
    assert len(api.get("/api/plan/review?day=2026-10-25").json()["slots"]) == 100
    assert len(api.get("/api/plan/review?day=2026-10-26").json()["slots"]) == 96


def test_review_rejects_days_outside_30_days(client_as):
    api = client_as("viewer")
    assert api.get("/api/plan/review?day=2026-10-17").status_code == 422  # Zukunft
    assert api.get("/api/plan/review?day=2026-09-15").status_code == 422  # zu alt
    assert api.get("/api/plan/review?day=2026-09-16").status_code == 200  # 30 Tage zurück
    assert api.get("/api/plan/review?day=gestern").status_code == 422


async def test_plan_has_thermal_fields(client_as, planning, monkeypatch):
    stored = await planning.replan("manual")
    assert stored is not None
    computed = stored.created_at - timedelta(hours=7)
    slots = [
        slot.model_copy(update={"thermal_w": watts, "thermal_grid_w": grid, "thermal_steps": steps})
        for slot, (watts, grid, steps) in zip(
            stored.slots, [(1200.0, 300.0, 2), (-450.0, 0.0, -1), (0.0, 0.0, 0)], strict=False
        )
    ] + stored.slots[3:]
    thermal = PlanThermal(
        device="klima",
        mode="heizen",
        active=False,
        saving_ct=12.345,
        model_computed_at=computed,
        reason="ok",
    )
    shifted = stored.model_copy(update={"slots": slots, "thermal": thermal})
    monkeypatch.setattr(planning, "current", lambda: shifted)
    client = client_as("viewer")
    plan = client.get("/api/plan").json()["plan"]
    assert plan["thermal"] == {
        "device": "klima",
        "mode": "heizen",
        "active": False,
        "saving_ct": 12.3,  # auf 0,1 ct gerundet
        "model_computed_at": computed.isoformat(),
        "reason": "ok",
    }
    got = [(s["thermal_w"], s["thermal_grid_w"], s["thermal_steps"]) for s in plan["slots"][:4]]
    assert got == [(1200.0, 300.0, 2), (-450.0, 0.0, -1), (0.0, 0.0, 0), (0.0, 0.0, None)]
    # ohne Wärmemodell: ``model_computed_at`` bleibt leer
    bare = thermal.model_copy(update={"model_computed_at": None, "reason": "no_model"})
    monkeypatch.setattr(planning, "current", lambda: shifted.model_copy(update={"thermal": bare}))
    body = client.get("/api/plan").json()["plan"]["thermal"]
    assert (body["model_computed_at"], body["reason"]) == (None, "no_model")


def test_thermal_plan_switch_creates_a_settings_version(planning, settings_service, audit):
    version = planning.set_thermal_plan(True, actor="home-assistant", source="ha")
    current = settings_service.current()
    assert version == current and current.settings.learning.thermal.enabled is True
    assert (current.actor, current.source, current.comment) == (
        "home-assistant",
        "ha",
        "Vorheizen und Vorkühlen ein",
    )
    entry = next(e for e in audit.recent() if e.action == "settings.update")
    assert (entry.actor, entry.source) == ("home-assistant", "ha")
    assert planning.pending() == "settings"  # die Einstellung löst die Neuplanung aus
    planning.set_thermal_plan(False, actor="home-assistant", source="ha")
    assert settings_service.current().comment == "Vorheizen und Vorkühlen aus"


def test_thermal_plan_same_value_creates_no_version(planning, settings_service):
    before = settings_service.current().version
    assert planning.set_thermal_plan(False, actor="x", source="ha").version == before
    assert settings_service.current().version == before


def test_thermal_plan_keeps_the_other_thermal_settings(planning, settings_service):
    planning.set_thermal_plan(True, actor="x", source="ha")
    thermal = settings_service.current().settings.learning.thermal
    assert thermal.enabled and thermal.heat_up_k == 1.5 and thermal.margin_ct == 2.0


def test_thermal_plan_retries_once_on_conflict(planning, settings_service, monkeypatch):
    conflict_once(monkeypatch, settings_service)
    version = planning.set_thermal_plan(True, actor="x", source="ha")
    assert version.settings.learning.thermal.enabled
    assert settings_service.current().version == version.version


def test_thermal_plan_conflict_after_retry_raises(planning, settings_service, monkeypatch):
    conflict_always(monkeypatch, settings_service)
    with pytest.raises(VersionConflictError):
        planning.set_thermal_plan(True, actor="x", source="ha")
