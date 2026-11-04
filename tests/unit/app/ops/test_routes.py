import copy
from datetime import date, timedelta

import pytest
from tests.app_helpers import login_as
from tests.unit.app.ops.test_check import _health
from tests.unit.shared.test_decisions import decision

from bindaems.app.control.decisions import DecisionStore
from bindaems.app.control.intent import CoreDown
from bindaems.app.core_link import LiveState
from bindaems.app.history.influx import InfluxQueryError
from bindaems.app.ops.routes import ops_router
from bindaems.app.status import ComponentStatus
from bindaems.shared.control import Intent
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


class FakeIntents:
    """Absicht wie der ``IntentService``: ``cached`` und ``current``."""

    def __init__(self) -> None:
        self.intent: Intent | None = Intent(version=3, control=True)
        self.known: Intent | None = None
        self.fetched = 0

    def cached(self) -> Intent | None:
        return self.known

    async def current(self) -> Intent | None:
        self.fetched += 1
        if self.intent is None:
            raise CoreDown
        return self.intent


@pytest.fixture
def intents() -> FakeIntents:
    return FakeIntents()


@pytest.fixture
def client(
    make_client,
    live,
    guard,
    store,
    clock,
    findings,
    make_sender,
    intents,
    settings_service,
    audit,
    cfg,
):
    def ok_source() -> ComponentStatus:
        return ComponentStatus("prices", True, "Preise bis 10.10. 23:45")

    return make_client(
        ops_router(
            live=live,
            guard=guard,
            sources=[ok_source],
            decisions=store,
            clock=clock,
            findings=findings,
            report=make_sender(),
            settings=settings_service,
            intents=intents,
            audit=audit,
            cfg=cfg,
        )
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


async def test_findings_route_groups_by_day(client, auth, findings, influx) -> None:
    # heute ist der 9. 10. 2026: gestern gerechnet, vorgestern nicht
    influx.error = InfluxQueryError("InfluxDB nicht erreichbar")
    await findings.run_day(date(2026, 10, 8))
    assert client.get("/api/ops/findings").status_code == 401
    login_as(client, auth, "viewer")
    body = client.get("/api/ops/findings?days=3").json()
    assert body == {
        "days": [
            {
                "day": "2026-10-08",
                "computed": True,
                "findings": [
                    {
                        "rule": "not_checked",
                        "level": "info",
                        "start": None,
                        "end": None,
                        "text": "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.",
                        "link": None,
                        "data": {},
                    }
                ],
            },
            {"day": "2026-10-07", "computed": False, "findings": []},
            {"day": "2026-10-06", "computed": False, "findings": []},
        ],
        "report": {
            "day": "2026-10-08",
            "text": (
                "Donnerstag 8.10.: Netzbezug 14,2 kWh (geplant 9,8), "
                "davon Netzladen 4,4 kWh (geplant 0,0). Kosten 3,12 €, Ersparnis 1,85 €. "
                "Monatsspitze 9,1 kW. 1 Auffälligkeit: "
                "Akku-SOC nicht geprüft: InfluxDB nicht erreichbar. "
                "Betriebscheck jetzt: alles in Ordnung."
            ),
        },
    }
    days = client.get("/api/ops/findings").json()["days"]
    assert [d["day"] for d in days][::13] == ["2026-10-08", "2026-09-25"]  # 14 Tage
    assert len(days) == 14


def test_findings_route_rejects_days_over_60(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/ops/findings?days=61").status_code == 422
    assert client.get("/api/ops/findings?days=0").status_code == 422
    assert client.get("/api/ops/findings?days=60").status_code == 200


async def test_findings_route_survives_a_failing_report(
    make_client,
    live,
    guard,
    store,
    clock,
    findings,
    make_sender,
    auth,
    settings_service,
    audit,
    cfg,
) -> None:
    def broken(day):
        raise RuntimeError("Abrechnung weg")

    app = make_client(
        ops_router(
            live=live,
            guard=guard,
            sources=[],
            decisions=store,
            clock=clock,
            findings=findings,
            report=make_sender(summary=broken),
            settings=settings_service,
            intents=FakeIntents(),
            audit=audit,
            cfg=cfg,
        )
    )
    login_as(app, auth, "viewer")
    response = app.get("/api/ops/findings?days=2")
    assert response.status_code == 200
    body = response.json()
    assert body["report"] is None
    assert [d["day"] for d in body["days"]] == ["2026-10-08", "2026-10-07"]


def test_switches_route_requires_login(client) -> None:
    assert client.get("/api/ops/switches").status_code == 401


def test_switches_route_lists_switches_with_last_change(
    client, auth, intents, settings_service, audit
) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/ops/switches").json()
    by_id = {item["id"]: item for item in body["switches"]}
    assert by_id["control"]["value"] == "an"
    assert by_id["control"]["changed"] is True
    assert by_id["grid_charge"]["since"] is None
    assert by_id["actuator.victron"]["label"] == "Victron"
    assert "default_mode.twc" in by_id
    assert set(by_id["report"]) == {
        "id",
        "label",
        "group",
        "value",
        "factory",
        "changed",
        "since",
        "actor",
        "source",
        "link",
    }


def test_switches_route_shows_who_changed(client, auth, settings_service, audit) -> None:
    current = settings_service.current()
    planning = current.settings.planning.model_copy(update={"grid_charge_allowed": True})
    changed = current.settings.model_copy(update={"planning": planning})
    settings_service.update(changed, base_version=current.version, actor="anna", source="ui")
    login_as(client, auth, "viewer")
    by_id = {i["id"]: i for i in client.get("/api/ops/switches").json()["switches"]}
    assert by_id["grid_charge"]["value"] == "an"
    assert by_id["grid_charge"]["actor"] == "anna"
    assert by_id["grid_charge"]["since"] is not None


def test_switches_route_without_core_marks_intent_unknown(client, auth, intents) -> None:
    intents.intent = None
    login_as(client, auth, "viewer")
    by_id = {i["id"]: i for i in client.get("/api/ops/switches").json()["switches"]}
    assert by_id["control"]["value"] == "unbekannt"
    assert by_id["control"]["changed"] is False


def test_switches_route_prefers_the_cached_intent(client, auth, intents) -> None:
    intents.known = Intent(version=5, control=False)
    login_as(client, auth, "viewer")
    by_id = {i["id"]: i for i in client.get("/api/ops/switches").json()["switches"]}
    assert by_id["control"]["value"] == "aus"
    assert intents.fetched == 0
