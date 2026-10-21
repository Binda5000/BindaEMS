import json
from datetime import date, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import PASSWORD, T_APP, ingest, login_as, set_tariff
from tests.plan_helpers import with_power_price
from tests.unit.app.control.conftest import FakeCoreClient
from tests.unit.shared.test_decisions import decision

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import AuthService
from bindaems.app.control.decisions import DecisionStore
from bindaems.app.control.intent import IntentService
from bindaems.app.control.routes import control_router
from bindaems.app.core_link import CoreReply, CoreUnavailableError, LiveState
from bindaems.app.learning.driving_auto import DrivingAutomation
from bindaems.shared.control import ActuatorMode, ChargeMode, Intent, VehicleIntent
from bindaems.shared.decisions import DecisionResult

LIVE_BODY = {"modes": {"wallbox.evcs": "live"}, "password": PASSWORD, "confirmed": True}


@pytest.fixture
def automation(
    service: IntentService, live: LiveState, settings_service, audit, engine, cfg, clock
) -> DrivingAutomation:
    return DrivingAutomation(
        intents=service,
        live=live,
        profiles=lambda key: None,
        charges=dict,
        settings=settings_service,
        audit=audit,
        engine=engine,
        cfg=cfg,
        clock=clock,
    )


@pytest.fixture
def client(
    make_client,
    service: IntentService,
    store: DecisionStore,
    fake_client: FakeCoreClient,
    auth: AuthService,
    audit: AuditLog,
    guard,
    clock,
    live: LiveState,
    settings_service,
    ledger,
    cfg,
    automation: DrivingAutomation,
) -> TestClient:
    router = control_router(
        service,
        store,
        fake_client,  # type: ignore[arg-type]
        auth,
        audit,
        guard,
        settings=settings_service,
        ledger=ledger,
        cfg=cfg,
        clock=clock,
        live=live,
        automation=automation,
    )
    return make_client(router)


def test_viewer_cannot_change_and_operator_can(client, auth) -> None:
    viewer = login_as(client, auth, "viewer")
    denied = client.put("/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=viewer)
    assert denied.status_code == 403
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=operator
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"]["vehicles"]["tesla"]["mode"] == "now"
    assert set(body) == {"result", "version", "details", "intent"}
    assert (body["result"], body["details"]) == ("accepted", [])


def test_every_change_route_needs_operator_and_a_csrf_token(client, auth) -> None:
    viewer = login_as(client, auth, "viewer")
    calls = [
        ("/api/control/regulation", {"on": True}),
        ("/api/control/vehicles/tesla/boost", {"on": True}),
        ("/api/control/vehicles/tesla/automation", {"on": True}),
        ("/api/control/vehicles/tesla/target", {"target_soc_pct": 80, "departure": "07:00"}),
        ("/api/control/wallboxes/evcs/vehicle", {"vehicle": "tesla"}),
        ("/api/control/guest", {"mode": "now"}),
        ("/api/control/support", {"allowed": True, "soc_pct": 60.0}),
        (
            "/api/control/priority",
            {"priority": ["tesla", "egolf"], "surplus_priority": {"kind": "cars_first"}},
        ),
        ("/api/control/actuators", {"modes": {}}),
    ]
    for path, body in calls:
        assert client.put(path, json=body, headers=viewer).status_code == 403, path
    assert client.post("/api/control/locks/wallbox.evcs/ack", headers=viewer).status_code == 403
    operator = login_as(client, auth, "operator")
    assert client.put(calls[0][0], json=calls[0][1]).status_code == 403  # ohne CSRF-Token
    # Sperre quittieren und Betriebsart ändern darf nur ein Admin
    assert (
        client.put("/api/control/actuators", json={"modes": {}}, headers=operator).status_code
        == 403
    )
    assert client.post("/api/control/locks/wallbox.evcs/ack", headers=operator).status_code == 403


def test_operator_changes_reach_the_core(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    assert (
        client.put("/api/control/regulation", json={"on": True}, headers=operator).status_code
        == 200
    )
    boost = client.put("/api/control/vehicles/tesla/boost", json={"on": True}, headers=operator)
    assert boost.status_code == 200
    assert boost.json()["intent"]["vehicles"]["tesla"]["boost_until"] is not None
    assign = client.put(
        "/api/control/wallboxes/evcs/vehicle", json={"vehicle": "tesla"}, headers=operator
    )
    assert assign.json()["intent"]["vehicles"]["tesla"]["wallbox"] == "evcs"
    guest = client.put("/api/control/guest", json={"mode": "min_pv"}, headers=operator)
    assert guest.json()["intent"]["guest"]["mode"] == "min_pv"
    priority = client.put(
        "/api/control/priority",
        json={"priority": ["egolf", "tesla"], "surplus_priority": {"kind": "cars_first"}},
        headers=operator,
    )
    assert priority.json()["intent"]["priority"] == ["egolf", "tesla"]
    assert priority.json()["intent"]["surplus_priority"]["kind"] == "cars_first"
    assert fake_client.valid is not None and fake_client.valid.control is True
    assert len(fake_client.sent) == 5


def test_bodies_forbid_extra_fields(client, auth) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/regulation", json={"on": True, "mode": "now"}, headers=operator
    )
    assert response.status_code == 422


def test_get_control_shows_intent_or_core_down(client, auth, fake_client) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/control").json() == {"core_connected": True, "intent": None}
    fake_client.valid = Intent(
        version=7, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.MIN_PV)}
    )
    body = client.get("/api/control").json()
    assert body["core_connected"] is True
    assert body["intent"]["version"] == 7
    assert body["intent"]["vehicles"]["tesla"]["mode"] == "min_pv"
    fake_client.error = CoreUnavailableError("weg")
    assert client.get("/api/control").json() == {"core_connected": False, "intent": None}


def test_live_needs_admin_password_and_confirmation(client, auth, fake_client) -> None:
    body = LIVE_BODY
    operator = login_as(client, auth, "operator")
    assert client.put("/api/control/actuators", json=body, headers=operator).status_code == 403
    admin = login_as(client, auth, "admin", username="chef")
    wrong = client.put(
        "/api/control/actuators", json=body | {"password": "falsch-falsch"}, headers=admin
    )
    assert (wrong.status_code, wrong.json()["detail"]) == (403, "Passwort falsch.")
    missing = client.put("/api/control/actuators", json=body | {"password": None}, headers=admin)
    assert (missing.status_code, missing.json()["detail"]) == (403, "Passwort falsch.")
    unconfirmed = client.put(
        "/api/control/actuators", json=body | {"confirmed": False}, headers=admin
    )
    assert (unconfirmed.status_code, unconfirmed.json()["detail"]) == (
        422,
        "Bitte bestätige Prüfprotokoll Teil 2.",
    )
    assert fake_client.sent == []
    ok = client.put("/api/control/actuators", json=body, headers=admin)
    assert ok.status_code == 200
    assert ok.json()["intent"]["actuators"] == {"wallbox.evcs": "live"}
    assert len(fake_client.sent) == 1


def test_dry_run_needs_no_password(client, auth, fake_client) -> None:
    admin = login_as(client, auth, "admin", username="chef")
    body = {"modes": {"wallbox.evcs": "dry_run"}, "password": None, "confirmed": False}
    response = client.put("/api/control/actuators", json=body, headers=admin)
    assert response.status_code == 200
    assert response.json()["intent"]["actuators"] == {"wallbox.evcs": "dry_run"}
    assert len(fake_client.sent) == 1


def test_only_actuators_changing_to_live_count(client, auth, fake_client) -> None:
    fake_client.valid = Intent(version=3, actuators={"wallbox.evcs": ActuatorMode.LIVE})
    admin = login_as(client, auth, "admin", username="chef")
    # EVCS ist schon live: sie bleibt es ohne Passwort, Victron geht auf Dry-Run
    body = {
        "modes": {"wallbox.evcs": "live", "victron": "dry_run"},
        "password": None,
        "confirmed": False,
    }
    assert client.put("/api/control/actuators", json=body, headers=admin).status_code == 200
    # Victron geht neu live: jetzt zählt sie
    body = {
        "modes": {"wallbox.evcs": "live", "victron": "live"},
        "password": None,
        "confirmed": False,
    }
    assert client.put("/api/control/actuators", json=body, headers=admin).status_code == 403


def _intents(fake_client: FakeCoreClient, *seen: Intent) -> None:
    """``intent()`` liefert nacheinander ``seen``, danach immer das letzte (gleichzeitige
    Änderung zwischen der Prüfung der Route und dem Holen im ``IntentService``)."""
    queue = list(seen)

    async def intent() -> Intent | None:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    fake_client.intent = intent  # type: ignore[method-assign]
    fake_client.valid = seen[-1]


def test_concurrent_dry_run_is_not_undone_without_password(client, auth, fake_client) -> None:
    # Bei der Prüfung ist EVCS live, beim Ableiten hat ein anderer sie auf Dry-Run gestellt
    _intents(
        fake_client,
        Intent(version=3, actuators={"wallbox.evcs": ActuatorMode.LIVE}),
        Intent(version=4, actuators={"wallbox.evcs": ActuatorMode.DRY_RUN}),
    )
    admin = login_as(client, auth, "admin", username="chef")
    body = {"modes": {"wallbox.evcs": "live"}, "password": None, "confirmed": False}
    response = client.put("/api/control/actuators", json=body, headers=admin)
    assert (response.status_code, response.json()["detail"]) == (403, "Passwort falsch.")
    assert fake_client.sent == []
    assert fake_client.valid.actuators == {"wallbox.evcs": ActuatorMode.DRY_RUN}


def test_conflict_retry_checks_the_live_gate_again(client, auth, fake_client) -> None:
    fake_client.valid = Intent(version=3, actuators={"wallbox.evcs": ActuatorMode.LIVE})

    def dry_run_meanwhile() -> CoreReply:
        # zwischen Holen und Senden stellt jemand EVCS auf Dry-Run: der core antwortet 409
        version = fake_client.sent[-1].version + 1
        fake_client.valid = Intent(
            version=version, actuators={"wallbox.evcs": ActuatorMode.DRY_RUN}
        )
        return CoreReply(409, {"result": "rejected", "version": version, "details": []})

    admin = login_as(client, auth, "admin", username="chef")
    fake_client.replies = [dry_run_meanwhile]
    body = {"modes": {"wallbox.evcs": "live"}, "password": None, "confirmed": False}
    response = client.put("/api/control/actuators", json=body, headers=admin)
    assert (response.status_code, response.json()["detail"]) == (403, "Passwort falsch.")
    assert len(fake_client.sent) == 1  # der zweite Versuch geht nicht hinaus
    assert fake_client.valid.actuators == {"wallbox.evcs": ActuatorMode.DRY_RUN}

    # Mit Passwort und Häkchen nur für Victron fehlt für EVCS die Bestätigung
    fake_client.valid = Intent(
        version=fake_client.valid.version + 1, actuators={"wallbox.evcs": ActuatorMode.LIVE}
    )
    fake_client.replies = [dry_run_meanwhile]
    body = {"modes": {"wallbox.evcs": "live", "victron": "live"}} | {
        "password": PASSWORD,
        "confirmed": True,
    }
    response = client.put("/api/control/actuators", json=body, headers=admin)
    assert (response.status_code, response.json()["detail"]) == (
        422,
        "Bitte bestätige Prüfprotokoll Teil 2.",
    )
    assert len(fake_client.sent) == 2
    assert fake_client.valid.actuators == {"wallbox.evcs": ActuatorMode.DRY_RUN}


def test_empty_modes_give_422(client, auth, fake_client) -> None:
    admin = login_as(client, auth, "admin", username="chef")
    response = client.put("/api/control/actuators", json={"modes": {}}, headers=admin)
    assert (response.status_code, response.json()["detail"]) == (422, "Keine Aktoren gewählt.")
    assert fake_client.sent == []


def test_live_without_live_allowed_is_clamped(client, auth, fake_client) -> None:
    hint = "Victron: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."
    fake_client.replies = [
        CoreReply(200, {"result": "clamped", "version": 12, "details": [hint]}),
    ]
    admin = login_as(client, auth, "admin", username="chef")
    response = client.put("/api/control/actuators", json=LIVE_BODY, headers=admin)
    assert response.status_code == 200
    body = response.json()
    assert (body["result"], body["version"], body["details"]) == ("clamped", 12, [hint])


def test_core_down_gives_503(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    fake_client.error = CoreUnavailableError("weg")
    response = client.put("/api/control/regulation", json={"on": True}, headers=operator)
    assert (response.status_code, response.json()["detail"]) == (503, "core nicht erreichbar.")
    admin = login_as(client, auth, "admin", username="chef")
    assert client.put("/api/control/actuators", json=LIVE_BODY, headers=admin).status_code == 503
    ack = client.post("/api/control/locks/wallbox.evcs/ack", headers=admin)
    assert (ack.status_code, ack.json()["detail"]) == (503, "core nicht erreichbar.")


def test_rejection_gives_422_with_the_sentences(client, auth, fake_client) -> None:
    fake_client.replies = [
        CoreReply(422, {"result": "rejected", "version": 0, "details": ["Erster.", "Zweiter."]})
    ]
    operator = login_as(client, auth, "operator")
    response = client.put("/api/control/regulation", json={"on": True}, headers=operator)
    assert (response.status_code, response.json()["detail"]) == (422, "Erster. Zweiter.")


def test_second_conflict_gives_409(client, auth, fake_client) -> None:
    conflict = CoreReply(409, {"result": "rejected", "version": 5, "details": []})
    fake_client.replies = [conflict, conflict]
    operator = login_as(client, auth, "operator")
    response = client.put("/api/control/regulation", json={"on": True}, headers=operator)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Die Absicht wurde gleichzeitig geändert. Bitte erneut versuchen.",
    )


def test_unknown_vehicle_gives_404(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put("/api/control/vehicles/vw/mode", json={"mode": "now"}, headers=operator)
    assert (response.status_code, response.json()["detail"]) == (404, "Unbekanntes Fahrzeug „vw“.")
    unknown = client.put(
        "/api/control/wallboxes/xyz/vehicle", json={"vehicle": None}, headers=operator
    )
    assert (unknown.status_code, unknown.json()["detail"]) == (404, "Unbekannte Wallbox „xyz“.")
    assert fake_client.sent == []


def test_ack_passes_status_and_detail(client, auth, fake_client, audit) -> None:
    admin = login_as(client, auth, "admin", username="chef")
    ok = client.post("/api/control/locks/wallbox.evcs/ack", headers=admin)
    assert ok.status_code == 202
    assert ok.json() == {"detail": "Quittiert. Die Sperre von EVCS löst sich im nächsten Zyklus."}
    cause = "Ursache besteht weiter: DESS ist aktiv."
    fake_client.ack_reply = CoreReply(409, {"detail": cause})
    refused = client.post("/api/control/locks/wallbox.evcs/ack", headers=admin)
    assert (refused.status_code, refused.json()) == (409, {"detail": cause})
    fake_client.ack_reply = CoreReply(404, {"detail": "Unbekannter Aktor „x“."})
    assert client.post("/api/control/locks/x/ack", headers=admin).status_code == 404
    assert fake_client.acked == ["wallbox.evcs", "wallbox.evcs", "x"]
    entries = [e for e in audit.recent() if e.action == "lock.acknowledge"]
    assert [(e.actor, e.source, e.target) for e in entries] == [
        ("chef", "ui", "x"),
        ("chef", "ui", "wallbox.evcs"),
        ("chef", "ui", "wallbox.evcs"),
    ]
    assert entries[1].details == {"status": 409, "detail": cause}


def test_ack_quotes_the_actuator_for_the_core(client, auth, fake_client) -> None:
    admin = login_as(client, auth, "admin", username="chef")
    client.post(f"/api/control/locks/{quote('a b', safe='')}/ack", headers=admin)
    assert fake_client.acked == ["a b"]


def test_changes_are_audited_once(client, auth, audit) -> None:
    operator = login_as(client, auth, "operator", username="bediener")
    client.put("/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=operator)
    entries = [e for e in audit.recent() if e.action.startswith("intent.")]
    assert [(e.actor, e.source, e.action, e.target) for e in entries] == [
        ("bediener", "ui", "intent.mode", "tesla")
    ]


def _fill(store: DecisionStore) -> None:
    store.add(
        [
            decision(
                id=f"d{i}",
                ts=T_APP - timedelta(minutes=i),
                actuator="vehicle.tesla" if i % 2 else "wallbox.evcs",
                dry_run=i % 3 == 0,
                result=DecisionResult.DRY_RUN if i % 3 == 0 else DecisionResult.OK,
            )
            for i in range(10)
        ]
    )


def test_decisions_query_parameters(client, auth, store) -> None:
    login_as(client, auth, "viewer")
    _fill(store)
    response = client.get("/api/decisions?actuator=vehicle.tesla&dry_run=false&limit=2")
    assert response.status_code == 200
    body = response.json()
    # tesla = ungerade i, ohne Dry-Run (i durch 3 teilbar): 1, 5, 7 – neueste zuerst
    assert [d["id"] for d in body["decisions"]] == ["d1", "d5"]
    assert body["more"] is True
    assert all(isinstance(d["cursor"], int) for d in body["decisions"])
    assert body["decisions"][0]["actuator"] == "vehicle.tesla"
    older = client.get(
        f"/api/decisions?actuator=vehicle.tesla&dry_run=false&limit=2"
        f"&before={body['decisions'][-1]['cursor']}"
    ).json()
    assert [d["id"] for d in older["decisions"]] == ["d7"]
    assert older["more"] is False
    assert client.get("/api/decisions?limit=501").status_code == 422
    assert client.get("/api/decisions?limit=0").status_code == 422


def test_decisions_filters_by_time_result_and_text(client, auth, store) -> None:
    login_as(client, auth, "viewer")
    store.add(
        [
            decision(id="a", ts=T_APP, text="Strom 8 A"),
            decision(id="b", ts=T_APP - timedelta(hours=2), result=DecisionResult.FOREIGN),
        ]
    )
    since = quote((T_APP - timedelta(hours=1)).isoformat(), safe="")
    assert [d["id"] for d in client.get(f"/api/decisions?since={since}").json()["decisions"]] == [
        "a"
    ]
    until = quote((T_APP - timedelta(hours=1)).isoformat(), safe="")
    assert [d["id"] for d in client.get(f"/api/decisions?until={until}").json()["decisions"]] == [
        "b"
    ]
    assert [d["id"] for d in client.get("/api/decisions?result=foreign").json()["decisions"]] == [
        "b"
    ]
    assert [d["id"] for d in client.get("/api/decisions?text=strom").json()["decisions"]] == ["a"]
    assert client.get("/api/decisions?result=kaputt").status_code == 422
    assert client.get("/api/decisions?since=2026-10-09T10:00:00").status_code == 422  # ohne Zone


def test_decisions_need_login(client) -> None:
    assert client.get("/api/decisions").status_code == 401
    assert client.get("/api/decisions/latest").status_code == 401


def test_latest_is_ordered_by_health_with_unknown_last(client, auth, store, live) -> None:
    login_as(client, auth, "viewer")
    store.add(
        [
            decision(id="1", ts=T_APP - timedelta(minutes=1), actuator="wallbox.evcs"),
            decision(id="2", ts=T_APP - timedelta(minutes=2), actuator="vehicle.tesla"),
            decision(id="3", ts=T_APP - timedelta(minutes=3), actuator="victron"),
            decision(id="4", ts=T_APP - timedelta(minutes=4), actuator="vehicle.unbekannt"),
            decision(id="5", ts=T_APP - timedelta(minutes=5), actuator="vehicle.tesla"),
            decision(id="6", ts=T_APP - timedelta(hours=25), actuator="vehicle.egolf"),  # zu alt
        ]
    )
    # ohne Health: Reihenfolge des Stores (neueste zuerst)
    plain = client.get("/api/decisions/latest").json()["decisions"]
    assert [d["id"] for d in plain] == ["1", "2", "3", "4"]
    live.health = {
        "actuators": [{"id": "victron"}, {"id": "vehicle.tesla"}, {"id": "wallbox.evcs"}]
    }
    ordered = client.get("/api/decisions/latest").json()["decisions"]
    assert [d["actuator"] for d in ordered] == [
        "victron",
        "vehicle.tesla",
        "wallbox.evcs",
        "vehicle.unbekannt",
    ]
    assert [d["id"] for d in ordered] == ["3", "2", "1", "4"]


def test_support_route_needs_operator(client, auth) -> None:
    viewer = login_as(client, auth, "viewer")
    body = {"allowed": True, "soc_pct": 60.0}
    assert client.put("/api/control/support", json=body, headers=viewer).status_code == 403
    operator = login_as(client, auth, "operator")
    assert client.put("/api/control/support", json=body).status_code == 403  # ohne CSRF-Token
    response = client.put("/api/control/support", json=body, headers=operator)
    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == {"result", "version", "details", "intent"}
    assert answer["intent"]["support"] == {"allowed": True, "soc_pct": 60.0}


def test_support_route_audits_and_keeps_the_threshold_open(client, auth, audit) -> None:
    operator = login_as(client, auth, "operator", username="anna")
    response = client.put(
        "/api/control/support", json={"allowed": False, "soc_pct": None}, headers=operator
    )
    assert response.json()["intent"]["support"] == {"allowed": False, "soc_pct": None}
    entry = audit.recent()[0]
    assert (entry.actor, entry.source, entry.action) == ("anna", "ui", "control.support")
    assert entry.details is not None and entry.details["to"] == "gesperrt"


def test_support_route_rejects_a_threshold_outside_the_range(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    low = client.put(
        "/api/control/support", json={"allowed": True, "soc_pct": 10.0}, headers=operator
    )
    assert low.status_code == 422
    assert low.json()["detail"] == "Unterstützungsschwelle 10 % liegt unter der USV-Reserve (20 %)."
    high = client.put(
        "/api/control/support", json={"allowed": True, "soc_pct": 101.0}, headers=operator
    )
    assert high.status_code == 422
    assert fake_client.sent == []


def test_support_body_is_strict(client, auth) -> None:
    operator = login_as(client, auth, "operator")
    for body in (
        {"allowed": True, "soc_pct": 60.0, "x": 1},
        {"allowed": "ja", "soc_pct": 60.0},
        {"allowed": True, "soc_pct": "60"},
        {"allowed": True},
    ):
        response = client.put(
            "/api/control/support",
            content=json.dumps(body),
            headers={**operator, "content-type": "application/json"},
        )
        assert response.status_code == 422, body


def test_support_route_rejects_a_threshold_that_is_not_a_number(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/support",
        content='{"allowed": true, "soc_pct": NaN}',
        headers={**operator, "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert fake_client.sent == []


def test_boost_cost_is_open_to_viewers_but_needs_login(client, auth) -> None:
    assert client.get("/api/control/boost-cost").status_code == 401
    login_as(client, auth, "viewer")
    assert client.get("/api/control/boost-cost").status_code == 200


def test_boost_cost_without_a_power_rate(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/control/boost-cost").json() == {
        "applies": False,
        "month_peak_kw": None,
        "vehicles": [],
    }


def test_boost_cost_uses_the_month_peak_of_the_ledger(
    client, auth, settings_service, ledger
) -> None:
    set_tariff(settings_service, with_power_price(value_eur=48.0, tier_kw=100.0))
    ingest(ledger, "2026-10-09T08:00:00+00:00", {"grid>house": 2000.0})  # 8 kW in 15 min
    login_as(client, auth, "viewer")
    body = client.get("/api/control/boost-cost").json()
    assert body["applies"] is True and body["month_peak_kw"] == pytest.approx(8.0)
    tesla = next(item for item in body["vehicles"] if item["id"] == "tesla")
    assert tesla["extra_kw"] == pytest.approx(11.04)
    assert tesla["cost_eur"] == pytest.approx(52.99)


def test_reader_cannot_set_target(client, auth, fake_client) -> None:
    viewer = login_as(client, auth, "viewer")
    body = {"target_soc_pct": 80, "departure": "07:00"}
    denied = client.put("/api/control/vehicles/tesla/target", json=body, headers=viewer)
    assert denied.status_code == 403
    assert fake_client.sent == []


def test_operator_sets_target_and_departure(client, auth, fake_client, audit) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": 85, "departure": "06:30"},
        headers=operator,
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"result", "version", "details", "intent"}
    assert (body["result"], body["details"]) == ("accepted", [])
    tesla = body["intent"]["vehicles"]["tesla"]
    assert (tesla["target_soc_pct"], tesla["departure"]) == (85, "06:30")
    assert fake_client.sent[-1].vehicles["tesla"].target_soc_pct == 85
    entry = audit.recent()[0]
    assert (entry.action, entry.target) == ("intent.target", "tesla")
    assert entry.details is not None and entry.details["to"] == "Ziel 85 %, Abfahrt 06:30"


def test_target_accepts_one_field_and_null(client, auth) -> None:
    operator = login_as(client, auth, "operator")
    url = "/api/control/vehicles/tesla/target"
    first = client.put(url, json={"target_soc_pct": 90, "departure": "05:00"}, headers=operator)
    assert first.status_code == 200
    only_departure = client.put(
        url, json={"target_soc_pct": None, "departure": "08:00"}, headers=operator
    )
    tesla = only_departure.json()["intent"]["vehicles"]["tesla"]
    assert (tesla["target_soc_pct"], tesla["departure"]) == (90, "08:00")


def test_target_with_departure_date(client, auth, fake_client, audit) -> None:
    operator = login_as(client, auth, "operator")
    url = "/api/control/vehicles/tesla/target"
    dated = client.put(
        url,
        json={"target_soc_pct": 80, "departure": "07:15", "departure_on": "2026-11-23"},
        headers=operator,
    )
    assert dated.status_code == 200
    assert dated.json()["intent"]["vehicles"]["tesla"]["departure_on"] == "2026-11-23"
    assert fake_client.sent[-1].vehicles["tesla"].departure_on == date(2026, 11, 23)
    entry = audit.recent()[0]
    assert entry.details is not None and entry.details["to"] == "Ziel 80 %, Abfahrt Mo 23.11. 07:15"
    # Ohne departure_on (etwa ältere Clients) gilt die Abfahrt wieder täglich
    daily = client.put(url, json={"target_soc_pct": None, "departure": "07:15"}, headers=operator)
    assert daily.json()["intent"]["vehicles"]["tesla"]["departure_on"] is None


def test_target_date_without_time_is_rejected(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": 80, "departure": None, "departure_on": "2026-11-23"},
        headers=operator,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ein Abfahrtstag verlangt eine Abfahrtszeit."
    assert fake_client.sent == []


def test_target_with_a_bad_date_is_rejected(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": 80, "departure": "07:00", "departure_on": "morgen"},
        headers=operator,
    )
    assert response.status_code == 422
    assert fake_client.sent == []


def test_target_out_of_range_is_rejected(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": 15, "departure": None},
        headers=operator,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ziel-SOC „15“ liegt außerhalb von 20–100 %."
    assert fake_client.sent == []


def test_bad_departure_is_rejected(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": None, "departure": "7 Uhr"},
        headers=operator,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Abfahrt „7 Uhr“ ist keine Uhrzeit (HH:MM)."
    assert fake_client.sent == []


def test_target_of_an_unknown_vehicle_is_404(client, auth) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/zoe/target",
        json={"target_soc_pct": 80, "departure": "07:00"},
        headers=operator,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Unbekanntes Fahrzeug „zoe“."


def test_unknown_vehicle_with_bad_target_is_404(client, auth, fake_client) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/zoe/target",
        json={"target_soc_pct": 15, "departure": "7 Uhr"},
        headers=operator,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Unbekanntes Fahrzeug „zoe“."
    assert fake_client.sent == []


def test_empty_target_request_is_rejected(client, auth, fake_client, audit) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/target",
        json={"target_soc_pct": None, "departure": None},
        headers=operator,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ziel oder Abfahrt angeben."
    assert fake_client.sent == []
    assert [e for e in audit.recent() if e.action.startswith("intent.")] == []


# --- Automatik für Ziel und Abfahrt (Plan 5b, Präzisierung 12) -------------------------------


def test_automation_switch_needs_operate_role(client, auth, audit) -> None:
    viewer = login_as(client, auth, "viewer")
    denied = client.put("/api/control/vehicles/tesla/automation", json={"on": True}, headers=viewer)
    assert denied.status_code == 403
    assert not [e for e in audit.recent() if e.action == "control.automation"]


def test_automation_switch_needs_login(client) -> None:
    response = client.put("/api/control/vehicles/tesla/automation", json={"on": True})
    assert response.status_code == 401


def test_operator_switches_the_automation_and_gets_its_state(
    client, auth, settings_service, automation
) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/automation", json={"on": True}, headers=operator
    )
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["enabled", "state", "text", "paused_until", "last_set_at"]
    assert body["enabled"] is True and body["state"] != "off"
    assert (body["paused_until"], body["last_set_at"]) == (None, None)
    assert settings_service.current().settings.learning.driving.automation == {"tesla": True}
    off = client.put("/api/control/vehicles/tesla/automation", json={"on": False}, headers=operator)
    assert off.json() == {
        "enabled": False,
        "state": "off",
        "text": "Automatik aus.",
        "paused_until": None,
        "last_set_at": None,
    }
    assert settings_service.current().settings.learning.driving.automation == {"tesla": False}


def test_automation_switch_is_audited(client, auth, audit) -> None:
    operator = login_as(client, auth, "operator", username="bedienen")
    client.put("/api/control/vehicles/tesla/automation", json={"on": True}, headers=operator)
    client.put("/api/control/vehicles/tesla/automation", json={"on": False}, headers=operator)
    entries = [e for e in audit.recent() if e.action == "control.automation"]
    assert len(entries) == 2  # kein zweiter Eintrag aus der Route
    off, on = entries  # neueste zuerst
    assert (on.actor, on.source, on.target) == ("bedienen", "ui", "tesla")
    assert (on.details or {})["to"] == "Automatik an"
    assert (off.details or {})["to"] == "Automatik aus"


def test_unknown_vehicle_is_404(client, auth, audit) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/unbekannt/automation", json={"on": True}, headers=operator
    )
    assert response.status_code == 404
    assert "unbekannt" in response.json()["detail"]
    assert not [e for e in audit.recent() if e.action == "control.automation"]


def test_automation_body_forbids_extra_fields(client, auth) -> None:
    operator = login_as(client, auth, "operator")
    response = client.put(
        "/api/control/vehicles/tesla/automation", json={"on": True, "x": 1}, headers=operator
    )
    assert response.status_code == 422
