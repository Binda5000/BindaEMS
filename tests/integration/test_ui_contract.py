"""Antworten der API in den Formen, die das UI erwartet (ui/src/lib/api/contract/).

Neu schreiben: UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py
"""

import asyncio
import copy
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.ui_history import DAYS, FIRST_DAY, LAST_DAY
from tests.ui_world import DEMO_PASSWORD, DEMO_TOTP_SECRET, T_DEMO, World, build_world

from bindaems.app.charging.planned import PlannedSummary
from bindaems.app.db.schema import assignment_log
from bindaems.app.ledger.checks import NOTE_ZERO
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots

CONTRACT = Path("ui/src/lib/api/contract")
GET_ENDPOINTS = {
    "me": "/api/auth/me",
    "state": "/api/state",
    "system": "/api/system",
    "settings": "/api/settings",
    "settings-versions": "/api/settings/versions",
    "limits": "/api/limits",
    "consumers": "/api/consumers",
    "consumer-candidates": "/api/consumers/candidates",
    "history-catalog": "/api/history/catalog",
    "history": "/api/history?series=grid,soc.battery&from=2026-10-09T06:00:00Z"
    "&to=2026-10-09T08:00:00Z",
    "prices": "/api/prices",
    "prices-now": "/api/prices/now",
    "forecast": "/api/forecast/pv",
    "forecast-load": "/api/forecast/load",
    "forecast-quality": "/api/forecast/quality?days=14",
    "forecast-quality-daily": "/api/forecast/quality/daily?days=28",
    "learning-load": "/api/learning/load",
    "learning-candidates": "/api/learning/candidates",
    "learning-driving": "/api/learning/driving",
    "ledger-days": "/api/ledger/days?from=2026-09-24&to=2026-10-09",
    "ledger-checks": "/api/ledger/checks?from=2026-10-02&to=2026-10-08",
    "stats-day": "/api/stats?period=day&date=2026-10-08",
    "stats-month": "/api/stats?period=month&date=2026-10-09",
    "stats-year": "/api/stats?period=year&date=2026-10-09",
    "charging-sessions": "/api/charging/sessions?from=2026-09-25&to=2026-10-09",
    "users": "/api/users",
    "audit": "/api/audit",
    "control": "/api/control",
    "boost-cost": "/api/control/boost-cost",
    "decisions": "/api/decisions",
    "decisions-latest": "/api/decisions/latest",
    "plan": "/api/plan",
    "plan-review": "/api/plan/review",
}
OTHER = {
    "live-hello",
    "live-state",
    "live-core",
    "live-alarm",
    "health",
    "totp-setup",
    "reprice",
    "error-422",
    "control-reply",
    "plan-grid-charge",
    "control-support",
    "control-automation",
}


WALLBOX_DAYS = {
    date(2026, 9, 27),
    date(2026, 9, 29),
    date(2026, 10, 3),
    date(2026, 10, 5),
    date(2026, 10, 7),
}


def local_range(first: date, last: date) -> tuple[datetime, datetime]:
    return local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT


def collect_responses(world: World) -> dict[str, object]:
    """Antworten aller Vertragsdateien, angemeldet als ``admin``."""
    client = TestClient(world.app.app, base_url="https://testserver")
    login = client.post("/api/auth/login", json={"username": "admin", "password": DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    csrf = {"X-CSRF-Token": client.cookies["bindaems_csrf"]}
    actual: dict[str, object] = {}
    for name, path in GET_ENDPOINTS.items():
        response = client.get(path)
        assert response.status_code == 200, (name, response.text)
        actual[name] = response.json()

    cookie = f"bindaems_session={client.cookies['bindaems_session']}"
    with client.websocket_connect("/api/live", headers={"cookie": cookie}) as ws:
        actual["live-hello"] = ws.receive_json()
    actual["live-state"] = json.loads(json.dumps({"type": "state", "data": world.app.live.state}))

    current: Any = copy.deepcopy(actual["settings"])
    for component in current["settings"]["tariff"]["components"]:
        if component["id"] == "grid_usage":
            component["value_ct"] = "abc"
    body = {"base_version": current["version"], "settings": current["settings"]}
    rejected = client.put("/api/settings", json=body, headers=csrf)
    assert rejected.status_code == 422, rejected.text
    actual["error-422"] = rejected.json()

    health = client.get("/health")
    assert health.status_code == 200, health.text
    actual["health"] = health.json()
    setup = client.post("/api/auth/totp/setup", json={"password": DEMO_PASSWORD}, headers=csrf)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]  # bei jedem Lauf neu: für den Vertrag zählt die Form
    actual["totp-setup"] = {
        "secret": DEMO_TOTP_SECRET,
        "uri": setup.json()["uri"].replace(secret, DEMO_TOTP_SECRET),
    }
    days = {"from": "2026-10-09", "to": "2026-10-09"}
    repriced = client.post("/api/ledger/reprice", json=days, headers=csrf)
    assert repriced.status_code == 200, repriced.text
    actual["reprice"] = repriced.json()
    changed = client.put("/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=csrf)
    assert changed.status_code == 200, changed.text
    actual["control-reply"] = changed.json()
    charge = client.put("/api/plan/grid-charge", json={"on": True}, headers=csrf)
    assert charge.status_code == 200, charge.text
    actual["plan-grid-charge"] = charge.json()
    support = client.put(
        "/api/control/support", json={"allowed": False, "soc_pct": 60}, headers=csrf
    )
    assert support.status_code == 200, support.text
    actual["control-support"] = support.json()
    automation = client.put(
        "/api/control/vehicles/egolf/automation", json={"on": True}, headers=csrf
    )
    assert automation.status_code == 200, automation.text
    actual["control-automation"] = automation.json()
    return actual


async def live_messages(world: World) -> dict[str, object]:
    """``alarm`` und ``core`` so, wie sie über /api/live kommen; je Alarmstufe ein Alarm."""
    alarms = [
        Alarm(f"demo.{severity.value}", severity, f"Beispiel ({severity.value})", T_DEMO)
        for severity in Severity
    ]
    async with world.core.subscribe() as core_stream, world.app.live.subscribe() as ui_stream:
        world.core._update_alarms(alarms)  # Serialisierung des core
        await world.app.live_feed._dispatch(await asyncio.wait_for(anext(core_stream), 2))
        alarm = await asyncio.wait_for(anext(ui_stream), 2)  # von der app weitergereicht
        world.app.live_feed._disconnected()
        core = await asyncio.wait_for(anext(ui_stream), 2)
        world.app.live_feed._connected()
    return {"live-alarm": alarm, "live-core": core}


def matches_contract(name: str, body: object) -> bool:
    """Vergleicht mit der Vertragsdatei; mit ``UPDATE_UI_CONTRACT=1`` wird sie neu geschrieben."""
    path = CONTRACT / f"{name}.json"
    # Reihenfolge wie in der Antwort: sie ist Teil des Vertrags (z. B. die Fahrzeuge), daher
    # Vergleich als Text statt als Objekt
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_UI_CONTRACT") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    return path.is_file() and path.read_text(encoding="utf-8") == text


async def test_api_responses_match_ui_contract(cfg, tmp_path) -> None:
    world = await build_world(cfg, tmp_path)
    try:
        actual = await asyncio.to_thread(collect_responses, world)  # GET, WebSocket, POST, 422
        actual.update(await live_messages(world))
    finally:
        world.close()
    mismatched = [name for name, body in actual.items() if not matches_contract(name, body)]
    assert sorted(actual) == sorted(GET_ENDPOINTS.keys() | OTHER)
    assert mismatched == []


async def test_demo_world_is_deterministic(cfg, tmp_path) -> None:
    # Sonst wechselten die Vertragsdateien bei jedem Lauf
    bodies = []
    for name in ("a", "b"):
        world = await build_world(cfg, tmp_path / name)
        try:
            body = await asyncio.to_thread(collect_responses, world)
            body.update(await live_messages(world))
            bodies.append(body)
        finally:
            world.close()
    assert bodies[0] == bodies[1]


async def test_demo_world_has_statistics_data(cfg, tmp_path) -> None:
    # Grundlage der Statistik-Seite: 14 Tage Abrechnung, Sessions, Abgleich, Verbraucher je Tag
    world = await build_world(cfg, tmp_path)
    try:
        app = world.app
        last = FIRST_DAY + timedelta(days=DAYS - 1)
        days = app.ledger.days(FIRST_DAY, last)
        assert [day.slots for day in days] == [96] * DAYS
        assert [day.estimated_slots for day in days if day.estimated_slots] == [2]
        assert all(day.cost_eur is not None and day.revenue_eur is not None for day in days)
        assert all(day.wallbox_kwh for day in days if day.date in WALLBOX_DAYS)

        sessions = app.charging_sessions.sessions(FIRST_DAY, date(2026, 10, 9))
        by_wallbox = {key: [s for s in sessions if s.wallbox == key] for key in ("evcs", "twc")}
        assert [s.end is None for s in by_wallbox["evcs"]] == [False, False, False]
        assert [s.end is None for s in by_wallbox["twc"]] == [True, False, False]
        assert {s.vehicle for s in sessions} == {"egolf", "tesla", "guest", None}  # None: Unbekannt
        assert all(s.split.kwh > 0 for s in sessions)
        flows = {
            name
            for slot in app.ledger.slots(*local_range(FIRST_DAY, last))
            for name in slot.flows_wh
        }
        assert {"pv>wb:twc", "battery>wb:twc", "grid>wb:twc", "pv>wb:evcs"} <= flows

        # Belegung je Wallbox: erste Sicht, jede Änderung, auch der Wechsel der Art
        with app.engine.connect() as conn:
            log = conn.execute(
                select(assignment_log.c.wallbox, assignment_log.c.occupant, assignment_log.c.kind)
                .where(assignment_log.c.ts < T_DEMO - timedelta(hours=1))  # nur der Verlauf
                .order_by(assignment_log.c.ts, assignment_log.c.id)
            ).all()
        entries = {
            key: [(row.occupant, row.kind) for row in log if row.wallbox == key]
            for key in ("evcs", "twc")
        }
        assert entries["evcs"][:3] == [("egolf", "default"), ("egolf", "detected"), ("", "")]
        assert ("guest", "default") in entries["evcs"]
        assert ("", "unclear") in entries["evcs"]  # die Session ohne Fahrzeug
        assert entries["twc"][:3] == [("", ""), ("tesla", "detected"), ("", "")]

        # geplante Ladungen: je eine erreicht, nicht machbar, vorher abgesteckt und geändert, eine
        # offen
        assert app.planned_charges.summary(FIRST_DAY, date(2026, 10, 9)) == PlannedSummary(
            reached=1, feasible=1, missed=0, infeasible=1, early=1, unknown=0, changed=1
        )

        checks = app.ledger_checks.checks(last - timedelta(days=6), last)
        assert {check.day for check in checks} == {last - timedelta(days=n) for n in range(7)}
        assert {check.day.isoformat() for check in checks if check.flagged} == {"2026-10-04"}
        # an jedem Tag Netzbezug: der Abgleich vergleicht echte Werte statt „Zähler zeigt 0 kWh“
        assert [c.day for c in checks if c.signal == "import" and not c.ledger_kwh] == []
        assert sum(c.note == NOTE_ZERO for c in checks) < len(checks) / 2

        # geplantes Laden (Plan 4b): der Tesla „Geplant“ im Plan mit angenommenem Ladestand, der
        # e-Golf mit geschätztem
        health = {entry["id"]: entry for entry in app.live.health["vehicles"]}
        assert health["tesla"]["mode"] == "planned" and health["tesla"]["plugged"] is True
        assert health["tesla"]["soc_source"] == "assumed"
        assert health["egolf"]["soc_source"] == "estimated"
        current = app.planning.current()
        assert current is not None
        assert [
            (car.key, car.reachable, car.finish_at is not None) for car in current.vehicles
        ] == [("tesla", True, True)]

        # eine Monatsspitze für Plan, Boost-Mehrkosten und Statistik
        peak = app.ledger.month_peak(T_DEMO.astimezone(LOCAL_TZ).date())
        plan = app.planning.current()
        assert peak is not None and plan is not None and plan.month_peak_kw is not None
        assert round(plan.month_peak_kw, 2) == round(peak.kw, 2)
        costs = app.costing.costs(*local_range(FIRST_DAY, last))
        assert len(costs) == 96 * DAYS and any(cost.avg_ct for cost in costs.values())
        stored = app.consumer_days.energy(last - timedelta(days=6), last)
        assert {kwh is not None for kwh, _ in stored.values()} == {True}
    finally:
        world.close()


async def test_demo_world_has_a_loaded_model(cfg, tmp_path) -> None:
    # Grundlage der Seite „Prognosen“: ein aktives Modell mit Backtest, Schattenbetrieb an 12 Tagen
    # und zwei gewählten Sensoren, von denen das Modell einen nutzt
    world = await build_world(cfg, tmp_path)
    try:
        status = world.app.load_model.status()
        assert status["activation"]["active"] and status["activation"]["basis"] == "shadow"
        assert status["activation"]["days"] == 12 and status["activation"]["reason"] == "better"
        assert status["model"]["samples"] == 5376
        assert len(status["backtest"]["weeks"]) == 9  # drei Wochen zu drei Vorlaufklassen
        assert [(x["entity_id"], x["in_model"]) for x in status["sensors"]] == [
            ("sensor.aussentemperatur", True),
            ("sensor.wohnzimmer_temperatur", False),
        ]
        assert status["training"]["outcome"] == "trained" and status["status"]["ok"]
        latest = world.app.load_forecast.latest()
        assert latest is not None and latest.method == "model"
        last = LAST_DAY
        rows = world.app.forecast_quality.daily(last - timedelta(days=11), last)
        assert {row.kind for row in rows} == {"load", "load_profile", "load_model"}
        assert len({row.day for row in rows}) == 12
    finally:
        world.close()


async def test_demo_world_has_driving_behaviour(cfg, tmp_path) -> None:
    # Grundlage der Karten „Fahrverhalten“ und „Anwesenheit“: ein Pendler (Automatik an) und ein
    # Auto mit wenig Daten (Konfidenz niedrig, Automatik aus), beide über die echte Route
    world = await build_world(cfg, tmp_path)
    try:

        def fetch() -> dict[str, Any]:
            client = TestClient(world.app.app, base_url="https://testserver")
            login = client.post(
                "/api/auth/login", json={"username": "admin", "password": DEMO_PASSWORD}
            )
            assert login.status_code == 200, login.text
            return client.get("/api/learning/driving").json()

        body = await asyncio.to_thread(fetch)
    finally:
        world.close()
    assert body["status"]["text"] == "Fahrverhalten berechnet am 09.10. 03:40."
    tesla, egolf = body["vehicles"]
    commute = tesla["weekdays"][:5]
    assert {(w["departure"], w["departure_confidence"]) for w in commute} == {("07:15", "high")}
    assert [tesla["weekdays"][5]["trips"], tesla["weekdays"][6]["trips"]] == [0, 0]
    assert tesla["automation"]["enabled"] is True and tesla["automation"]["state"] == "paused"
    assert tesla["quality"]["departure_hit_pct"] == 89 and len(tesla["presence"]) == 192
    assert max(item["p"] for item in tesla["presence"]) > 0.8
    assert egolf["days"] < 7 and egolf["source"] == "sessions"
    low = {w["departure_confidence"] for w in egolf["weekdays"] if w["departure"] is not None}
    assert low == {"low"}
    assert egolf["automation"] == {
        "enabled": False,
        "state": "off",
        "text": "Automatik aus.",
        "paused_until": None,
        "last_set_at": None,
    }


async def test_reprice_keeps_the_demo_costs(cfg, tmp_path) -> None:
    # Die Demo-Welt ist in den E2E-Tests geteilt: „Neu bewerten“ (Einstellungen) darf die Kosten
    # der Statistik nicht verlieren; die Preise der Demo liegen deshalb auch im Preisspeicher
    world = await build_world(cfg, tmp_path)
    try:
        app = world.app
        first, last = local_range(FIRST_DAY, date(2026, 10, 20))  # bis zum echten Datum der CI
        months = (date(2026, 9, 25), date(2026, 10, 9))
        before = [app.stats.period("month", day)["money"] for day in months]
        prices = {slot.slot_start: slot.import_price_ct for slot in app.ledger.slots(first, last)}
        assert None not in prices.values()
        app.ledger.reprice(FIRST_DAY, date(2026, 10, 20), actor="test", source="ui")
        app.costing.catch_up()  # wie die Nachführung des Einstandspreises im Betrieb
        after = {slot.slot_start: slot.import_price_ct for slot in app.ledger.slots(first, last)}
        assert after.keys() == prices.keys()
        assert all(after[k] == pytest.approx(v, abs=1e-9) for k, v in prices.items())
        money = [app.stats.period("month", day)["money"] for day in months]
        for old, new in zip(before, money, strict=True):
            assert old["total_eur"] is not None
            assert new == pytest.approx(old)
    finally:
        world.close()


def test_no_stale_contract_files() -> None:
    names = {path.stem for path in CONTRACT.glob("*.json")}
    assert names == GET_ENDPOINTS.keys() | OTHER


def test_contract_comparison_respects_key_order(tmp_path, monkeypatch) -> None:
    # Die Reihenfolge ist Teil des Vertrags: das UI zeigt z. B. die Fahrzeuge so, wie sie kommen
    monkeypatch.setitem(globals(), "CONTRACT", tmp_path)
    monkeypatch.delenv("UPDATE_UI_CONTRACT", raising=False)
    (tmp_path / "x.json").write_text(json.dumps({"a": 1, "b": 2}, indent=2) + "\n")
    assert matches_contract("x", {"a": 1, "b": 2})
    assert not matches_contract("x", {"b": 2, "a": 1})
