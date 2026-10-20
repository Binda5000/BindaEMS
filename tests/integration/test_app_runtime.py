import asyncio
import json
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import insert, select
from structlog.testing import capture_logs
from tests.app_helpers import (
    PASSWORD,
    SLOT_FLOWS_MSG,
    T_APP,
    StopScheduler,
    ingest,
    record_then_stop,
)
from tests.helpers import stop
from tests.plan_helpers import sample_plan, set_planning, until
from tests.unit.shared.test_decisions import decision

from bindaems.app.control.decisions import DecisionQuery
from bindaems.app.control.intent import CoreDown
from bindaems.app.db.schema import charging_session, planned_charge
from bindaems.app.forecast.openmeteo import OPEN_METEO_URL
from bindaems.app.ha.entities import build_inputs
from bindaems.app.runtime import AppRuntime, StatsInputs
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import Intent, SupportIntent
from bindaems.shared.decisions import decision_to_json
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import ManualClock

TOKEN = "x" * 32


def make_runtime(
    cfg: Config, tmp_path: Path, ha_token: str | None = None, *, with_mqtt: bool = True
) -> AppRuntime:
    """Laufzeit ohne HTTP-Server; core und MQTT zeigen auf Port 1 (scheitern sofort).
    ``with_mqtt`` falsch: Home Assistant ohne MQTT (nur Benachrichtigungen mit Token)."""
    assert cfg.homeassistant is not None and cfg.homeassistant.mqtt is not None
    app = cfg.app.model_copy(
        update={
            "data_dir": tmp_path,
            "backup_dir": tmp_path / "backup",
            "core_url": "http://127.0.0.1:1",
        }
    )
    mqtt = cfg.homeassistant.mqtt.model_copy(update={"host": "127.0.0.1", "port": 1})
    homeassistant = cfg.homeassistant.model_copy(update={"mqtt": mqtt if with_mqtt else None})
    local = cfg.model_copy(update={"app": app, "homeassistant": homeassistant})
    return AppRuntime(
        local,
        Secrets(
            internal_token=SecretStr(TOKEN),
            ha_token=SecretStr(ha_token) if ha_token is not None else None,
        ),
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
        "load-forecast",
        "load-model",
        "planning",
        "ledger",
        "influx",
        "ha",
        "ha-notify",
    ]
    notify = next(c for c in body["components"] if c["name"] == "ha-notify")
    assert (notify["ok"], notify["message"]) == (
        False,
        "Benachrichtigungen aus: BINDAEMS_HA_TOKEN fehlt.",
    )
    assert "Admin „chris“ hat keine Zwei-Faktor-Anmeldung (TOTP)." in body["warnings"]
    for path in (
        "/api/settings",
        "/api/consumers",
        "/api/prices",
        "/api/prices/now",
        "/api/forecast/pv",
        "/api/forecast/load",
        "/api/history/catalog",
        "/api/ledger/days?from=2026-10-09&to=2026-10-09",
        "/api/limits",
        "/api/state",
    ):
        assert client.get(path).status_code == 200, path


def test_runtime_wires_the_cost_basis(runtime, cfg: Config, tmp_path) -> None:
    # die echte Verdrahtung statt des nachgebauten Hilfsbaus der Unit-Tests: die Abrechnung
    # markiert den Einstandspreis, Sessions und Statistik lesen denselben
    slot = timedelta(minutes=15)
    assert runtime.charging_sessions._costing is runtime.costing
    assert runtime.stats._sessions is runtime.charging_sessions
    ingest(runtime.ledger, T_APP - 2 * slot, {"pv>battery": 2000.0})
    ingest(runtime.ledger, T_APP - slot, {"battery>wb:twc": 1000.0})
    assert runtime.costing.catch_up() == 2
    wear = runtime.settings.current().settings.planning.wear_ct
    costs = runtime.costing.costs(T_APP - 2 * slot, T_APP)
    assert costs[T_APP - slot].discharge_ct == wear and costs[T_APP - slot].stock_kwh == 1.0
    ingest(runtime.ledger, T_APP, {"pv>battery": 500.0})  # bis zur Nachführung nur markiert
    restarted = make_runtime(cfg, tmp_path)  # Neustart vor der Nachführung
    try:
        assert restarted.costing.catch_up() == 1
        assert restarted.costing.costs(T_APP, T_APP + slot)[T_APP].stock_kwh == 1.5
    finally:
        restarted.engine.dispose()


def test_without_token_the_rejections_are_only_logged(runtime) -> None:
    assert runtime.ha_notifier is None and runtime.alarm_notifications is None
    assert runtime.warning_notifications is None
    assert type(runtime.ha_publisher._commands).__name__ == "HaCommandHandler"
    assert type(runtime.ha_publisher._commands._rejections).__name__ == "LogRejections"


def test_with_token_the_notifier_takes_over_the_rejections(cfg: Config, tmp_path) -> None:
    rt = make_runtime(cfg, tmp_path, ha_token="h" * 20)
    try:
        assert rt.ha_notifier is not None and rt.alarm_notifications is not None
        assert rt.warning_notifications is not None
        assert rt.ha_publisher._commands._rejections is rt.ha_notifier
        client = TestClient(rt.app, base_url="https://testserver")
        rt.auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
        client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
        names = [c["name"] for c in client.get("/api/system").json()["components"]]
        assert "ha-notify" not in names
    finally:
        rt.engine.dispose()


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


async def test_maintenance_cleans_up_old_plans(runtime, monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(runtime.plan_store, "cleanup", lambda now: calls.append(now) or (2, 3))
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=0))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await runtime._maintenance_loop()
    assert calls == [T_APP]
    entry = next(e for e in logs if e["event"] == "Alte Pläne aufgeräumt")
    assert (entry["deleted"], entry["trimmed"]) == (2, 3)


async def test_maintenance_survives_a_failed_plan_cleanup(runtime, monkeypatch) -> None:
    def broken(now):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.plan_store, "cleanup", broken)
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=0))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await runtime._maintenance_loop()
    assert "Pflege der Pläne fehlgeschlagen" in [e["event"] for e in logs]


def test_runtime_wires_the_planning(runtime) -> None:
    assert runtime.planning.current() is None and runtime.planning.pending() is None
    current = runtime.settings.current()
    runtime.settings.update(
        current.settings.model_copy(
            update={"planning": current.settings.planning.model_copy(update={"margin_ct": 3.0})}
        ),
        base_version=current.version,
        actor="test",
        source="ui",
    )
    assert runtime.planning.pending() == "settings"


async def test_runtime_wires_the_plan_sender(runtime, monkeypatch) -> None:
    # nach jeder Verbindung: erst Health anfordern, dann gleicht der Sender den Plan ab
    assert runtime.live_feed.connect_handlers == [
        runtime.collector.catch_up,
        runtime._core_connected,
    ]
    # nach jeder Abfrage von Health gleicht der Sender ab (Gesamtreview I1)
    assert runtime.live_feed.health_handlers == [
        runtime.plan_sender.wake,
        runtime.charging.wake,
        runtime._planned_wake.set,
    ]
    calls: list[str] = []
    monkeypatch.setattr(runtime.live_feed, "refresh_health", lambda: calls.append("health"))
    monkeypatch.setattr(runtime.plan_sender, "wake", lambda: calls.append("wake"))
    await runtime._core_connected()
    assert calls == ["health", "wake"]


def test_runtime_records_planned_charges_with_the_open_sessions(runtime) -> None:
    # nach Health: die geplante Ladung gehört zur offenen Session an der Wallbox des Fahrzeugs;
    # die Statistik zählt sie über denselben Dienst
    assert runtime.stats._planned is runtime.planned_charges
    add_session(runtime, T_APP - timedelta(hours=1), None, "tesla", "twc")
    departure = T_APP + timedelta(hours=8)
    runtime.live.connected = True
    runtime.live.health = {
        "intent": {"version": 1, "vehicles": {"tesla": {"mode": "planned", "target_soc_pct": 80}}},
        "intent_version": 1,
        "vehicles": [
            {
                "id": "tesla",
                "mode": "planned",
                "wallbox": "twc",
                "plugged": True,
                "soc_pct": 40.0,
                "soc_source": "measured",
                "charge_limit_pct": None,
                "planned": {
                    "state": "surplus",
                    "target_soc_pct": 80,
                    "departure_at": departure.isoformat(),
                    "need_kwh": 30.0,
                },
            }
        ],
    }
    runtime.record_planned()
    with runtime.engine.connect() as conn:
        [row] = conn.execute(select(planned_charge)).all()
        session_id = conn.execute(select(charging_session.c.id)).scalar_one()
    assert (row.session_id, row.departure_at, row.outcome) == (session_id, departure, "open")
    runtime.live.connected = False  # ohne Verbindung keine Beobachtung
    runtime.live.health = {"vehicles": []}
    runtime.record_planned()
    with runtime.engine.connect() as conn:
        assert conn.execute(select(planned_charge.c.outcome)).scalars().all() == ["open"]


def test_planning_takes_the_support_threshold_of_the_intent(runtime, cfg: Config) -> None:
    assert runtime._support_soc_pct() == cfg.control.support_soc_pct  # noch keine Absicht
    runtime.intents._cached = Intent(version=2)
    assert runtime._support_soc_pct() == cfg.control.support_soc_pct  # Absicht ohne Schwelle
    runtime.intents._cached = Intent(version=3, support=SupportIntent(soc_pct=85.0))
    assert runtime._support_soc_pct() == 85.0
    assert runtime.planning._support_pct() == 85.0


def spy_nightly(runtime, monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        runtime.forecast_quality, "update", lambda day: calls.append(("quality", day)) or 0
    )
    monkeypatch.setattr(
        runtime.forecast, "recalibrate", lambda ledger: calls.append(("recalibrate", ledger))
    )
    monkeypatch.setattr(
        runtime.forecast_archive, "cleanup", lambda before: calls.append(("cleanup", before)) or 0
    )
    return calls


async def test_forecast_nightly_runs_at_start_and_at_0230_local(runtime, monkeypatch) -> None:
    calls = spy_nightly(runtime, monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", record_then_stop(sleeps, after=1))
    with pytest.raises(StopScheduler):
        await runtime._forecast_nightly_loop()
    # beim Start: Güte der letzten 3 Tage (heute, gestern, vorgestern), dann Kalibrierung, Pflege
    assert calls == [
        ("quality", date(2026, 10, 9)),
        ("quality", date(2026, 10, 8)),
        ("quality", date(2026, 10, 7)),
        ("recalibrate", runtime.ledger),
        ("cleanup", T_APP - timedelta(days=60)),
    ]
    assert sleeps == [16.5 * 3600]  # 10:00 Ortszeit → 02:30 am nächsten Tag


async def test_forecast_nightly_after_start_rates_yesterday_and_the_day_before(
    runtime, monkeypatch
) -> None:
    calls = spy_nightly(runtime, monkeypatch)
    await runtime._forecast_nightly((1, 2))
    assert calls[:3] == [
        ("quality", date(2026, 10, 8)),
        ("quality", date(2026, 10, 7)),
        ("recalibrate", runtime.ledger),
    ]


async def test_forecast_nightly_survives_errors(runtime, monkeypatch) -> None:
    removed: list[object] = []
    updated: list[date] = []

    def broken(ledger):
        raise RuntimeError("Datenbank gesperrt")

    def broken_quality(day):
        updated.append(day)
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.forecast_quality, "update", broken_quality)
    monkeypatch.setattr(runtime.forecast, "recalibrate", broken)
    monkeypatch.setattr(
        runtime.forecast_archive, "cleanup", lambda before: removed.append(before) or 0
    )
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=2))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await runtime._forecast_nightly_loop()
    events = [e["event"] for e in logs]
    assert "Kennzahlen der Prognosegüte fehlgeschlagen" in events
    assert "Kalibrierung der PV-Prognose fehlgeschlagen" in events
    assert len(updated) == 3 + 2  # jeder Tag für sich, auch nach einem Fehler
    assert len(removed) == 2  # die Pflege läuft trotzdem, die Schleife auch nach dem Fehler


def spy_ledger_checks(runtime, monkeypatch) -> list[tuple[date, date]]:
    calls: list[tuple[date, date]] = []

    async def run(first: date, last: date) -> int:
        calls.append((first, last))
        return (last - first).days + 1

    monkeypatch.setattr(runtime.ledger_checks, "run", run)
    return calls


def spy_gap_estimator(
    runtime, monkeypatch, order: list[str] | None = None, *, backfilled: bool = True
) -> list[tuple]:
    """Ersetzt die Nachrechnung; ``backfilled``: der Nachtrag hat die letzten 60 Tage gelesen."""
    calls: list[tuple] = []
    if backfilled:
        runtime._backfill_covered = (T_APP - timedelta(days=60), T_APP)

    async def run(start, end) -> int:
        calls.append((start, end))
        if order is not None:
            order.append("estimate")
        return 3

    monkeypatch.setattr(runtime.gap_estimator, "run", run)
    return calls


async def test_ledger_nightly_checks_35_days_at_start_then_7_days_at_0245(
    runtime, monkeypatch
) -> None:
    calls = spy_ledger_checks(runtime, monkeypatch)
    spy_gap_estimator(runtime, monkeypatch)
    runtime._ledger_backfilled.set()
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", record_then_stop(sleeps, after=1))
    with pytest.raises(StopScheduler):
        await runtime._ledger_nightly_loop()
    # T_APP ist der 09.10.2026, 10:00 Ortszeit: die letzten 35 vollständigen Tage bis gestern
    assert calls == [(date(2026, 9, 4), date(2026, 10, 8))]
    assert sleeps == [16.75 * 3600]  # 10:00 Ortszeit → 02:45 am nächsten Tag
    calls.clear()
    await runtime._ledger_nightly(7)
    assert calls == [(date(2026, 10, 2), date(2026, 10, 8))]


async def test_ledger_nightly_estimates_the_last_7_days_before_the_check(
    runtime, monkeypatch
) -> None:
    order: list[str] = []
    estimates = spy_gap_estimator(runtime, monkeypatch, order)
    checks = spy_ledger_checks(runtime, monkeypatch)
    run_checks = runtime.ledger_checks.run

    async def run(first: date, last: date) -> int:
        order.append("check")
        return await run_checks(first, last)

    monkeypatch.setattr(runtime.ledger_checks, "run", run)
    await runtime._ledger_nightly(35)
    # vom Beginn des 02.10. bis zum Beginn des 09.10. Ortszeit (Sommerzeit: UTC+2), auch beim Start
    assert estimates == [
        (datetime(2026, 10, 1, 22, 0, tzinfo=UTC), datetime(2026, 10, 8, 22, 0, tzinfo=UTC))
    ]
    assert order == ["estimate", "check"]
    assert checks == [(date(2026, 9, 4), date(2026, 10, 8))]


async def test_first_backfill_covers_the_whole_estimate_window(runtime, monkeypatch) -> None:
    # B6: war die app länger aus, begann die Schätzung um Mitternacht vor 7 Tagen, der Nachtrag
    # erst 7 × 24 h vor „jetzt“; dazwischen entstanden Schätzungen, obwohl echte Daten da waren
    backfills: list[tuple[datetime, datetime]] = []

    async def backfill(reader, start, end) -> int:
        backfills.append((start, end))
        return 0

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    with pytest.raises(StopScheduler):
        await runtime._ledger_loop()
    monkeypatch.undo()
    estimates = spy_gap_estimator(runtime, monkeypatch, backfilled=False)
    spy_ledger_checks(runtime, monkeypatch)
    await runtime._ledger_nightly(7)
    [(estimate_start, estimate_end)] = estimates
    [(backfill_start, backfill_end)] = backfills
    assert backfill_start <= estimate_start and estimate_end <= backfill_end
    # Mitternacht des 02.10. Ortszeit (Sommerzeit: UTC+2)
    assert backfill_start == datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


async def test_estimate_stays_inside_the_backfilled_range(runtime, monkeypatch) -> None:
    estimates = spy_gap_estimator(runtime, monkeypatch, backfilled=False)
    spy_ledger_checks(runtime, monkeypatch)
    runtime._backfill_covered = (datetime(2026, 10, 5, 12, 0, tzinfo=UTC), T_APP)
    await runtime._ledger_nightly(7)
    assert estimates == [
        (datetime(2026, 10, 5, 12, 0, tzinfo=UTC), datetime(2026, 10, 8, 22, 0, tzinfo=UTC))
    ]


async def test_estimate_waits_for_the_first_successful_backfill(runtime, monkeypatch) -> None:
    estimates = spy_gap_estimator(runtime, monkeypatch, backfilled=False)
    checks = spy_ledger_checks(runtime, monkeypatch)
    with capture_logs() as logs:
        await runtime._ledger_nightly(7)
    assert estimates == [] and len(checks) == 1  # der Abgleich läuft trotzdem
    assert "Nachrechnung der Lücken wartet auf den ersten Nachtrag der Abrechnung" in [
        e["event"] for e in logs
    ]


def test_backfilled_range_grows_and_restarts_after_a_hole(runtime) -> None:
    hour = timedelta(hours=1)
    runtime._note_backfill(T_APP - 7 * 24 * hour, T_APP)
    runtime._note_backfill(T_APP - 5 * hour, T_APP + hour)  # stündlich, überlappt
    assert runtime._backfill_covered == (T_APP - 7 * 24 * hour, T_APP + hour)
    runtime._note_backfill(T_APP + 4 * hour, T_APP + 10 * hour)  # Lücke: Nachtrag scheiterte
    assert runtime._backfill_covered == (T_APP + 4 * hour, T_APP + 10 * hour)


async def test_backfill_after_failed_hours_starts_where_the_coverage_ended(
    runtime, monkeypatch
) -> None:
    # N3: nach 7 gescheiterten Stunden begann der nächste Nachtrag bei „jetzt − 6 h“; die Stunden
    # dazwischen las kein Nachtrag mehr, und die Nachrechnung schätzte sie nicht (B6)
    from bindaems.app.history.influx import InfluxQueryError

    starts: list[datetime] = []

    async def backfill(reader, start, end) -> int:
        starts.append(start)
        if 1 <= len(starts) - 1 <= 7:
            raise InfluxQueryError("InfluxDB nicht erreichbar")
        return 0

    async def sleep(seconds: float) -> None:
        runtime._clock.advance(seconds)
        if len(starts) >= 10:
            raise StopScheduler

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(StopScheduler):
        await runtime._ledger_loop()
    hour = timedelta(hours=1)
    assert starts[8] == T_APP  # Ende der Abdeckung, nicht T_APP + 8 h − 6 h
    assert starts[9] == T_APP + 3 * hour  # wieder lückenlos: die letzten 6 h
    assert runtime._backfill_covered == (datetime(2026, 10, 1, 22, 0, tzinfo=UTC), T_APP + 9 * hour)


async def test_backfill_after_a_long_outage_starts_at_the_estimate_window(
    runtime, monkeypatch
) -> None:
    from bindaems.app.history.influx import InfluxQueryError

    starts: list[datetime] = []
    failed_hours = 10 * 24

    async def backfill(reader, start, end) -> int:
        starts.append(start)
        if 1 <= len(starts) - 1 <= failed_hours:
            raise InfluxQueryError("InfluxDB nicht erreichbar")
        return 0

    async def sleep(seconds: float) -> None:
        runtime._clock.advance(seconds)
        if len(starts) >= failed_hours + 2:
            raise StopScheduler

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(StopScheduler):
        await runtime._ledger_loop()
    # 10 Tage später (19.10. 10:00 Ortszeit): Mitternacht des 12.10. Ortszeit, nicht T_APP
    assert starts[-1] == datetime(2026, 10, 11, 22, 0, tzinfo=UTC)


async def test_failed_backfill_covers_nothing(runtime, monkeypatch) -> None:
    from bindaems.app.history.influx import InfluxQueryError

    async def backfill(reader, start, end) -> int:
        raise InfluxQueryError("InfluxDB nicht erreichbar")

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    with pytest.raises(StopScheduler):
        await runtime._ledger_loop()
    assert runtime._backfill_covered is None


async def test_ledger_nightly_catches_up_the_costing_after_new_estimates(
    runtime, monkeypatch
) -> None:
    spy_gap_estimator(runtime, monkeypatch)
    spy_ledger_checks(runtime, monkeypatch)
    runtime._costing_wake.clear()
    await runtime._ledger_nightly(7)
    assert runtime._costing_wake.is_set()  # weckt die Nachführung (``_costing_loop``)


async def test_ledger_nightly_checks_even_if_the_estimate_fails(runtime, monkeypatch) -> None:
    from bindaems.app.history.influx import InfluxQueryError

    errors: list[Exception] = [
        InfluxQueryError("InfluxDB nicht erreichbar"),
        RuntimeError("Datenbank gesperrt"),
    ]

    async def run(start, end) -> int:
        raise errors.pop(0)

    monkeypatch.setattr(runtime.gap_estimator, "run", run)
    runtime._backfill_covered = (T_APP - timedelta(days=60), T_APP)
    checks = spy_ledger_checks(runtime, monkeypatch)
    with capture_logs() as logs:
        await runtime._ledger_nightly(7)
        await runtime._ledger_nightly(7)
    events = [e["event"] for e in logs]
    assert "Nachrechnung der Lücken nicht möglich" in events
    assert "Nachrechnung der Lücken fehlgeschlagen" in events
    assert len(checks) == 2  # der Abgleich läuft nach jedem Fehler


async def test_ledger_nightly_survives_errors(runtime, monkeypatch) -> None:
    from bindaems.app.history.influx import InfluxQueryError

    errors: list[Exception] = [
        InfluxQueryError("InfluxDB nicht erreichbar"),
        RuntimeError("Datenbank gesperrt"),
    ]

    async def run(first: date, last: date) -> int:
        raise errors.pop(0)

    monkeypatch.setattr(runtime.ledger_checks, "run", run)
    spy_gap_estimator(runtime, monkeypatch)
    runtime._ledger_backfilled.set()
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=2))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await runtime._ledger_nightly_loop()
    events = [e["event"] for e in logs]
    assert events == [
        "Abgleich mit den Zählern nicht möglich",
        "Abgleich mit den Zählern fehlgeschlagen",
    ]  # die Schleife läuft nach jedem Fehler weiter


async def test_ledger_nightly_start_run_waits_for_the_first_backfill(runtime, monkeypatch) -> None:
    # sonst prüft der Start Tage, die der Nachtrag der Abrechnung noch füllt (veraltete Zeilen)
    calls = spy_ledger_checks(runtime, monkeypatch)
    estimates = spy_gap_estimator(runtime, monkeypatch)
    task = asyncio.create_task(runtime._ledger_nightly_loop())
    try:
        await asyncio.sleep(0.05)
        assert calls == [] and estimates == []  # der Nachtrag ist noch nicht fertig
        runtime._ledger_backfilled.set()
        await until(lambda: calls != [])
        assert len(estimates) == 1
    finally:
        task.cancel()
        await asyncio.wait([task])


async def test_ledger_loop_releases_the_nightly_run_after_the_backfill(
    runtime, monkeypatch
) -> None:
    order: list[str] = []
    gate = asyncio.Event()

    async def backfill(reader, start, end) -> int:
        await gate.wait()
        order.append("backfill")
        return 0

    async def run(first: date, last: date) -> int:
        order.append("check")
        return 1

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(runtime.ledger_checks, "run", run)
    spy_gap_estimator(runtime, monkeypatch)
    ledger_task = asyncio.create_task(runtime._ledger_loop())
    nightly_task = asyncio.create_task(runtime._ledger_nightly_loop())
    try:
        await asyncio.sleep(0.05)
        assert order == []
        gate.set()
        await until(lambda: "check" in order)
        assert order == ["backfill", "check"]  # erst der Nachtrag, dann der Abgleich
    finally:
        for task in (ledger_task, nightly_task):
            task.cancel()
        await asyncio.wait([ledger_task, nightly_task])


@pytest.mark.parametrize("failure", [RuntimeError("Datenbank gesperrt"), "influx"])
async def test_failed_first_backfill_still_releases_the_nightly_run(
    runtime, monkeypatch, failure
) -> None:
    from bindaems.app.history.influx import InfluxQueryError

    error = InfluxQueryError("InfluxDB nicht erreichbar") if failure == "influx" else failure

    async def backfill(reader, start, end) -> int:
        raise error

    monkeypatch.setattr(runtime.ledger, "backfill", backfill)
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    assert not runtime._ledger_backfilled.is_set()
    with pytest.raises(StopScheduler):
        await runtime._ledger_loop()
    assert runtime._ledger_backfilled.is_set()  # sonst bliebe der Abgleich beim Start aus


async def test_ledger_nightly_start_run_does_not_wait_forever(runtime, monkeypatch) -> None:
    calls = spy_ledger_checks(runtime, monkeypatch)
    spy_gap_estimator(runtime, monkeypatch)
    monkeypatch.setattr("bindaems.app.runtime.LEDGER_BACKFILL_WAIT_S", 0.05)
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await asyncio.wait_for(runtime._ledger_nightly_loop(), timeout=1)
    assert len(calls) == 1
    assert "Nachtrag der Abrechnung dauert zu lange, der Abgleich beginnt ohne ihn" in [
        e["event"] for e in logs
    ]


async def test_a_long_costing_catch_up_does_not_hold_up_the_live_stream(
    runtime, monkeypatch
) -> None:
    # B3: die Nachführung des Einstandspreises läuft im eigenen Job, nicht in der Handler-Kette
    release, started = threading.Event(), threading.Event()

    def slow_catch_up() -> int:
        started.set()
        release.wait(5)
        return 0

    monkeypatch.setattr(runtime.costing, "catch_up", slow_catch_up)
    seen: list[dict] = []

    async def next_handler(data: dict) -> None:
        seen.append(data)

    runtime.live_feed._handlers.append(next_handler)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        await until(started.is_set)  # die Nachführung beim Start läuft und hängt
        message = {"type": "slot_flows", "data": SLOT_FLOWS_MSG}
        await asyncio.wait_for(runtime.live_feed._dispatch(message), timeout=1)
        assert seen == [SLOT_FLOWS_MSG]
        assert len(runtime.ledger.slots(T_APP, T_APP + timedelta(minutes=15))) == 1
    finally:
        release.set()
        costing_task.cancel()
        await asyncio.wait([costing_task])


async def test_costing_job_catches_up_when_a_slot_arrives(runtime, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(runtime.costing, "catch_up", lambda: calls.append(1) or 0)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        await until(lambda: calls == [1])  # beim Start
        await runtime._on_slot_flows(SLOT_FLOWS_MSG)
        await until(lambda: calls == [1, 1])
    finally:
        costing_task.cancel()
        await asyncio.wait([costing_task])


async def test_costing_job_catches_up_after_reprice_and_wear_change(runtime, monkeypatch) -> None:
    # N3: beide kommen aus einem Worker-Thread (Route, Einstellungen) und markierten nur „dirty“;
    # nachgeführt wurde erst beim nächsten Slot oder Lesen
    ingest(runtime.ledger, T_APP, {"grid>battery": 1000.0})
    calls: list[int] = []
    monkeypatch.setattr(runtime.costing, "catch_up", lambda: calls.append(1) or 0)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        await until(lambda: calls == [1])  # beim Start
        day = T_APP.date()
        assert await asyncio.to_thread(runtime.ledger.reprice, day, day, actor="t", source="ui")
        await until(lambda: calls == [1, 1], max_wait_s=1.0)
        planning = runtime.settings.current().settings.planning
        wear = planning.model_copy(update={"wear_ct": planning.wear_ct + 1.0})
        await asyncio.to_thread(set_planning, runtime.settings, wear)
        await until(lambda: calls == [1, 1, 1], max_wait_s=1.0)
    finally:
        costing_task.cancel()
        await asyncio.wait([costing_task])


async def test_shutdown_waits_for_the_running_costing_thread(runtime, monkeypatch) -> None:
    # N3: der abgebrochene Job ließ ``catch_up`` im Thread weiterlaufen, während ``dispose`` lief
    ingest(runtime.ledger, T_APP, {"pv>battery": 1000.0})
    entered, release = threading.Event(), threading.Event()
    order: list[str] = []
    real_slots = runtime.ledger.slots

    def slow_slots(start, end):
        entered.set()
        assert release.wait(5)
        order.append("Block gerechnet")
        return real_slots(start, end)

    real_dispose = runtime.engine.dispose

    def dispose() -> None:
        order.append("dispose")
        real_dispose()

    monkeypatch.setattr(runtime.ledger, "slots", slow_slots)
    monkeypatch.setattr(runtime.engine, "dispose", dispose)
    runtime._tasks = [asyncio.create_task(runtime._costing_loop())]
    await until(entered.is_set)
    threading.Timer(0.2, release.set).start()
    await runtime.shutdown()
    assert order == ["Block gerechnet", "dispose"]


async def test_shutdown_closes_the_load_model_before_dispose(runtime, monkeypatch) -> None:
    order: list[str] = []

    async def close(timeout_s: float) -> bool:
        order.append(f"close {timeout_s:g}")
        return False  # rechnet noch: wird gewarnt

    real_dispose = runtime.engine.dispose

    def dispose() -> None:
        order.append("dispose")
        real_dispose()

    monkeypatch.setattr(runtime.load_model, "close", close)
    monkeypatch.setattr(runtime.engine, "dispose", dispose)
    with capture_logs() as logs:
        await runtime.shutdown()
    assert order == ["close 5", "dispose"]
    assert "Training des Lastmodells läuft beim Beenden noch" in [e["event"] for e in logs]


async def test_costing_job_survives_a_failing_catch_up(runtime, monkeypatch) -> None:
    calls: list[int] = []

    def broken() -> int:
        calls.append(1)
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.costing, "catch_up", broken)
    monkeypatch.setattr("bindaems.app.runtime.COSTING_RETRY_S", 0.05)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        with capture_logs() as logs:
            await until(lambda: len(calls) == 1)
            runtime._wake_costing()
            await until(lambda: len(calls) == 2)
        assert "Einstandspreis des Akkus nicht berechenbar" in [e["event"] for e in logs]
    finally:
        costing_task.cancel()
        await asyncio.wait([costing_task])


async def test_costing_wakes_from_a_thread_before_and_after_the_job_started(
    runtime, monkeypatch
) -> None:
    # Fix-Runde 2: vor dem Start setzt ein Weckruf nur das Flag; danach geht jeder aus einem
    # fremden Thread über ``call_soon_threadsafe`` an den Loop des Jobs
    calls: list[int] = []
    monkeypatch.setattr(runtime.costing, "catch_up", lambda: calls.append(1) or 0)
    runtime._costing_wake.clear()
    await asyncio.to_thread(runtime._wake_costing)  # Job läuft noch nicht
    loop = asyncio.get_running_loop()
    threadsafe: list[object] = []
    real = loop.call_soon_threadsafe

    def spy(callback, *args, **kwargs):
        threadsafe.append(callback)
        return real(callback, *args, **kwargs)

    monkeypatch.setattr(loop, "call_soon_threadsafe", spy)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        await until(lambda: calls == [1])  # der Weckruf vor dem Start ging nicht verloren
        await asyncio.to_thread(runtime._wake_costing)
        await until(lambda: calls == [1, 1], max_wait_s=1.0)
        assert runtime._costing_wake.set in threadsafe
    finally:
        await stop(costing_task)


async def test_permanently_failing_costing_does_not_spin(runtime, monkeypatch) -> None:
    # Fix-Runde 2: das erneute Markieren nach einem Fehler weckte über ``on_dirty`` sofort den
    # nächsten Versuch; ein bleibender Fehler drehte so Tausende Male je Sekunde
    calls: list[int] = []

    def broken(start) -> int:
        calls.append(1)
        raise RuntimeError("Datenbank schreibgeschützt")

    monkeypatch.setattr(runtime.costing, "_recompute", broken)
    runtime.costing.mark_dirty(None)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        with capture_logs():
            await until(lambda: len(calls) >= 1)
            await asyncio.sleep(0.5)
        assert len(calls) == 1  # dann 60 s Pause
    finally:
        await stop(costing_task)


class _GatedSleep:
    """``asyncio`` für ``bindaems.app.runtime`` mit einem ``sleep``, der erst endet, wenn der Test
    ``release`` setzt: Die Pause hängt so nicht an der Uhr des Testrechners."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.delays: list[float] = []

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await self.release.wait()


async def test_costing_pause_after_a_failure_keeps_a_wake_and_can_be_cancelled(
    runtime, monkeypatch
) -> None:
    gated = _GatedSleep()
    monkeypatch.setattr("bindaems.app.runtime.asyncio", gated)
    calls: list[str] = []
    real = runtime.costing._recompute

    def fails_once(start) -> int:
        calls.append("Versuch")
        if len(calls) == 1:
            raise RuntimeError("Datenbank gesperrt")
        return real(start)

    monkeypatch.setattr(runtime.costing, "_recompute", fails_once)
    runtime.costing.mark_dirty(None)
    costing_task = asyncio.create_task(runtime._costing_loop())
    try:
        with capture_logs():
            await until(lambda: gated.delays == [60.0])  # gescheitert, jetzt in der Pause
            ingest(runtime.ledger, T_APP, {"pv>battery": 1000.0})  # Weckruf in der Pause
            await asyncio.sleep(0.1)
            assert calls == ["Versuch"]  # noch in der Pause
            gated.release.set()
            await until(lambda: len(calls) == 2)  # danach nicht verloren
        assert runtime.costing.costs(T_APP, T_APP + timedelta(minutes=15))[T_APP].stock_kwh == 1.0
    finally:
        await stop(costing_task)
    monkeypatch.setattr("bindaems.app.runtime.asyncio", asyncio)  # die echte Pause von 60 s
    monkeypatch.setattr(
        runtime.costing, "_recompute", lambda start: (_ for _ in ()).throw(OSError())
    )
    runtime.costing.mark_dirty(None)
    paused = asyncio.create_task(runtime._costing_loop())
    with capture_logs() as logs:
        await until(lambda: any(e["log_level"] == "error" for e in logs))  # gescheitert
        await asyncio.sleep(0.05)  # jetzt in der Pause
        await asyncio.wait_for(stop(paused), timeout=1)  # die Pause ist abbrechbar
    assert paused.cancelled()


async def test_first_pv_forecast_waits_for_the_calibration(runtime, monkeypatch) -> None:
    calls = spy_nightly(runtime, monkeypatch)

    async def forecast_run() -> None:
        calls.append(("forecast",))

    monkeypatch.setattr(runtime.forecast, "run", forecast_run)
    task = asyncio.create_task(runtime._forecast_run())
    await asyncio.sleep(0.05)
    assert calls == []  # noch nicht kalibriert: kein Abruf mit dem neutralen Modell
    await runtime._forecast_nightly((0, 1, 2))
    await asyncio.wait_for(task, timeout=1)
    names = [c[0] for c in calls]
    assert names.index("forecast") > names.index("recalibrate")  # erst die Kalibrierung


async def test_first_pv_forecast_does_not_wait_forever(runtime, monkeypatch) -> None:
    started: list[bool] = []

    async def forecast_run() -> None:
        started.append(True)

    monkeypatch.setattr(runtime.forecast, "run", forecast_run)
    monkeypatch.setattr("bindaems.app.runtime.FORECAST_CALIBRATION_WAIT_S", 0.05)
    with capture_logs() as logs:
        await asyncio.wait_for(runtime._forecast_run(), timeout=1)
    assert started == [True]
    assert "Kalibrierung der PV-Prognose dauert zu lange, der Abruf beginnt ohne sie" in [
        e["event"] for e in logs
    ]


async def test_failed_calibration_still_releases_the_pv_forecast(runtime, monkeypatch) -> None:
    def broken(ledger):
        raise RuntimeError("Datenbank gesperrt")

    started: list[bool] = []

    async def forecast_run() -> None:
        started.append(True)

    monkeypatch.setattr(runtime.forecast, "recalibrate", broken)
    monkeypatch.setattr(runtime.forecast, "run", forecast_run)
    await runtime._forecast_nightly(())
    await asyncio.wait_for(runtime._forecast_run(), timeout=1)
    assert started == [True]


@pytest.mark.respx(assert_all_called=False)
async def test_runtime_starts_and_stops_cleanly(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)  # kein echter Netzverkehr
    task = asyncio.create_task(runtime.run())
    await until(
        lambda: (
            runtime.pipeline.status().last_attempt is not None
            and runtime.planning.status().unavailable is not None
        )
    )
    assert {"plan-sender", "charging", "planned-charges"} <= {
        job.get_name() for job in runtime._tasks
    }
    await runtime.shutdown()
    await asyncio.wait_for(task, timeout=2)
    assert runtime.pipeline.status().last_attempt is not None  # Preise wurden versucht
    assert runtime.planning.status().unavailable == "kein SOC vom core"  # Planung lief beim Start
    assert not runtime.planning.status().running


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


@pytest.mark.respx(assert_all_called=False)
async def test_warnings_without_mqtt_get_their_ha_inputs(cfg, tmp_path, respx_mock, monkeypatch):
    # Token für Home Assistant, aber kein MQTT: Die Warnungen brauchen die Werte für HA trotzdem.
    # Ohne den Job „ha-inputs“ warteten sie ewig auf die ersten Werte, ohne sichtbaren Fehler.
    respx_mock.route().respond(503)
    runtime = make_runtime(cfg, tmp_path, ha_token="h" * 20, with_mqtt=False)
    assert runtime.ha_publisher is None and runtime.warning_notifications is not None
    checks: list[bool] = []

    async def check() -> None:
        checks.append(True)

    monkeypatch.setattr(runtime.warning_notifications, "check", check)
    task = asyncio.create_task(runtime.run())
    try:
        await until(lambda: bool(checks))
        names = {job.get_name() for job in runtime._tasks}
        assert {"ha-inputs", "ha-warnings"} <= names and "ha" not in names
        assert runtime._ha_ready.is_set()
    finally:
        await runtime.shutdown()
        await asyncio.wait_for(task, timeout=2)
        runtime.engine.dispose()


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


async def test_ha_inputs_carry_the_support_of_the_intent_or_the_config(
    runtime, monkeypatch
) -> None:
    default = runtime._cfg.control.support_soc_pct
    own = Intent(version=5, support=SupportIntent(allowed=False, soc_pct=55.0))
    inputs = runtime._build_ha_inputs(own)
    assert (inputs.support_allowed, inputs.support_soc_pct) == (False, 55.0)
    assert runtime._build_ha_inputs(Intent(version=5)).support_soc_pct == default
    runtime._ha_inputs = inputs
    # nach einem Befehl gilt gleich die neue Absicht des Dienstes, auch für die Unterstützung
    runtime.intents._cached = Intent(version=6, support=SupportIntent(allowed=True))
    shown = runtime._current_ha_inputs()
    assert (shown.support_allowed, shown.support_soc_pct) == (True, default)


async def test_ha_inputs_carry_the_plan_and_the_grid_charge_switch(runtime, monkeypatch) -> None:
    # Sensoren und Schalter des Plans lesen aus ``inputs.plan`` und ``grid_charge_allowed``.
    plan = sample_plan(T_APP.isoformat(), ["08:00", "08:15"])
    monkeypatch.setattr(runtime.planning, "current", lambda: plan)
    set_planning(runtime.settings, PlanningSettings(grid_charge_allowed=True))
    inputs = runtime._build_ha_inputs(Intent(version=1))
    assert inputs.plan == plan and inputs.plan_now == plan.slots[0]
    assert inputs.grid_charge_allowed is True
    set_planning(runtime.settings, PlanningSettings(grid_charge_allowed=False))
    assert runtime._build_ha_inputs(Intent(version=1)).grid_charge_allowed is False


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


def spy_consumer_days(runtime, monkeypatch) -> dict[str, list]:
    calls: dict[str, list] = {"store": [], "fill": []}

    async def store(day: date) -> int:
        calls["store"].append(day)
        return 2

    async def fill_missing(today: date, days: int = 31) -> int:
        calls["fill"].append((today, days))
        return 5

    monkeypatch.setattr(runtime.consumer_days, "store", store)
    monkeypatch.setattr(runtime.consumer_days, "fill_missing", fill_missing)
    return calls


async def test_ledger_nightly_fills_consumer_days_31_at_start_and_3_at_night(
    runtime, monkeypatch
) -> None:
    spy_ledger_checks(runtime, monkeypatch)
    spy_gap_estimator(runtime, monkeypatch)
    calls = spy_consumer_days(runtime, monkeypatch)
    runtime._ledger_backfilled.set()
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    with pytest.raises(StopScheduler):
        await runtime._ledger_nightly_loop()
    # beim Start: die fehlenden Tage der letzten 31 (heute ist der 09.10.2026), nachts der letzten 3
    assert calls == {"store": [], "fill": [(date(2026, 10, 9), 31)]}
    await runtime._ledger_nightly(7)
    assert calls == {"store": [], "fill": [(date(2026, 10, 9), 31), (date(2026, 10, 9), 3)]}


async def test_ledger_nightly_survives_a_failing_consumer_day_step(runtime, monkeypatch) -> None:
    checks = spy_ledger_checks(runtime, monkeypatch)
    estimates = spy_gap_estimator(runtime, monkeypatch)

    async def fill_missing(today: date, days: int = 31) -> int:
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.consumer_days, "fill_missing", fill_missing)
    with capture_logs() as logs:
        await runtime._ledger_nightly(7)
    assert "Energie der Verbraucher je Tag nicht gespeichert" in [e["event"] for e in logs]
    assert len(estimates) == len(checks) == 1


async def test_ledger_nightly_stores_consumer_days_even_if_estimate_and_check_fail(
    runtime, monkeypatch
) -> None:
    async def broken(*args) -> int:
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.gap_estimator, "run", broken)
    monkeypatch.setattr(runtime.ledger_checks, "run", broken)
    calls = spy_consumer_days(runtime, monkeypatch)
    await runtime._ledger_nightly(7)
    assert calls["fill"] == [(date(2026, 10, 9), 3)]


# --- Statistik-Sensoren für Home Assistant (Plan 4a, Task 10) -------------------------------


def spy_stats(runtime, monkeypatch) -> list[int]:
    """Zählt die Neuberechnungen der Statistik (Thread-Kennung je Aufruf)."""
    calls: list[int] = []
    original = runtime.stats.today

    def today():
        calls.append(threading.get_ident())
        return original()

    monkeypatch.setattr(runtime.stats, "today", today)
    return calls


async def until_calls(calls: list[int], count: int) -> None:
    for _ in range(300):
        if len(calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{len(calls)} statt {count} Neuberechnungen")


async def test_stats_inputs_refresh_on_new_slot_not_every_5_s(runtime, monkeypatch) -> None:
    calls = spy_stats(runtime, monkeypatch)
    task = asyncio.create_task(runtime._stats_loop())
    try:
        await until_calls(calls, 1)  # gleich beim Start
        # der 5-s-Takt der übrigen Werte rechnet die Statistik nicht neu
        for _ in range(5):
            runtime._build_ha_inputs(Intent(version=1))
            await asyncio.sleep(0.02)
        assert len(calls) == 1
        # ein neuer Slot aus einem Worker-Thread (der Strom speichert so) rechnet neu
        await asyncio.to_thread(ingest, runtime.ledger, T_APP, {"pv>house": 400.0})
        await until_calls(calls, 2)
        ingest(runtime.ledger, T_APP + timedelta(minutes=15), {"pv>house": 100.0})
        await until_calls(calls, 3)
        await asyncio.sleep(0.05)
        assert len(calls) == 3
    finally:
        await stop(task)
    assert threading.get_ident() not in calls  # die Datenbank wird im Worker-Thread gelesen


async def test_stats_inputs_refresh_every_15_minutes(runtime, monkeypatch) -> None:
    from bindaems.app import runtime as module

    assert module.STATS_S == 15 * 60.0
    monkeypatch.setattr(module, "STATS_S", 0.05)
    calls = spy_stats(runtime, monkeypatch)
    task = asyncio.create_task(runtime._stats_loop())
    try:
        await until_calls(calls, 3)  # ohne neuen Slot
    finally:
        await stop(task)


async def test_ha_inputs_carry_the_cached_statistics(runtime) -> None:
    empty = runtime._build_ha_inputs(Intent(version=1))
    assert (empty.stats, dict(empty.sessions), dict(empty.month_kwh)) == (None, {}, {})
    await asyncio.to_thread(runtime._refresh_stats)
    inputs = runtime._build_ha_inputs(Intent(version=1))
    assert inputs.stats is runtime._stats_inputs.stats and inputs.stats is not None
    assert inputs.sessions == {"tesla": None, "egolf": None}
    assert inputs.month_kwh == {"tesla": 0.0, "egolf": 0.0}


def add_session(runtime, start, end, vehicle, wallbox="evcs") -> None:
    with runtime.engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox=wallbox, vehicle=vehicle, start=start, end=end, last_seen=end or start
            )
        )


async def test_stats_inputs_pick_the_open_or_last_session_per_vehicle(runtime) -> None:
    hour = timedelta(hours=1)
    add_session(runtime, T_APP - 5 * hour, T_APP - 4 * hour, "tesla")
    add_session(runtime, T_APP - hour, None, "tesla")  # offene Session vor der beendeten
    add_session(runtime, T_APP - 3 * hour, T_APP - 2 * hour, "egolf", "twc")
    ingest(runtime.ledger, T_APP - timedelta(minutes=45), {"grid>wb:evcs": 3000.0})
    ingest(runtime.ledger, T_APP - timedelta(minutes=270), {"pv>wb:evcs": 1000.0})
    ingest(runtime.ledger, T_APP - 3 * hour, {"grid>wb:twc": 2000.0})
    await asyncio.to_thread(runtime._refresh_stats)
    cached = runtime._stats_inputs
    tesla, egolf = cached.sessions["tesla"], cached.sessions["egolf"]
    assert tesla is not None and tesla.end is None and tesla.split.kwh == pytest.approx(3.0)
    assert egolf is not None and egolf.end == T_APP - 2 * hour
    assert egolf.split.kwh == pytest.approx(2.0)
    assert cached.month_kwh == {"tesla": pytest.approx(4.0), "egolf": pytest.approx(2.0)}


async def test_stats_inputs_find_an_open_session_older_than_the_newest_ones(runtime) -> None:
    hour = timedelta(hours=1)
    add_session(runtime, T_APP - 20 * hour, None, "tesla")  # offen, älter als die sechs beendeten
    for index in range(6):
        start = T_APP - (10 - index) * hour
        add_session(runtime, start, start + timedelta(minutes=20), "tesla", "twc")
    add_session(runtime, T_APP - 30 * hour, T_APP - 29 * hour, "egolf")  # nur beendete
    await asyncio.to_thread(runtime._refresh_stats)
    tesla, egolf = runtime._stats_inputs.sessions["tesla"], runtime._stats_inputs.sessions["egolf"]
    assert tesla is not None and tesla.end is None and tesla.start == T_APP - 20 * hour
    assert egolf is not None and egolf.end == T_APP - 29 * hour


async def test_failed_stats_refresh_keeps_the_previous_values(runtime, monkeypatch) -> None:
    await asyncio.to_thread(runtime._refresh_stats)
    before = runtime._stats_inputs
    assert before.stats is not None

    def broken(*_args, **_kwargs):
        raise ValueError("kaputt")

    monkeypatch.setattr(runtime.stats, "today", broken)
    monkeypatch.setattr(runtime.charging_sessions, "by_vehicle", broken)
    with capture_logs() as logs:
        await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs == before  # die Sensoren bleiben beim letzten Wert
    events = [log["event"] for log in logs]
    assert "Ersparnis und Autarkie für Home Assistant nicht berechenbar" in events
    assert "Ladeenergie im Monat für Home Assistant nicht berechenbar" in events


async def test_failed_stats_refresh_on_a_new_day_shows_unknown(runtime, monkeypatch) -> None:
    await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs.stats is not None

    def broken(*_args, **_kwargs):
        raise ValueError("kaputt")

    monkeypatch.setattr(runtime.stats, "today", broken)
    runtime._clock.advance(timedelta(hours=1))  # derselbe Tag in Wiener Zeit: letzter Wert bleibt
    await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs.stats is not None
    runtime._clock.advance(timedelta(days=1))  # neuer Tag: gestern darf nicht mehr erscheinen
    await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs.stats is None


async def test_failed_session_lookup_keeps_the_previous_session(runtime, monkeypatch) -> None:
    add_session(runtime, T_APP - timedelta(hours=1), None, "tesla")
    await asyncio.to_thread(runtime._refresh_stats)
    before = runtime._stats_inputs.sessions["tesla"]
    assert before is not None

    def broken(*_args, **_kwargs):
        raise ValueError("kaputt")

    monkeypatch.setattr(runtime.charging_sessions, "open_session", broken)
    with capture_logs() as logs:
        await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs.sessions["tesla"] is before
    assert [log["event"] for log in logs].count(
        "Ladesession für Home Assistant nicht bestimmbar"
    ) == 2


async def test_failed_lookup_of_the_latest_session_keeps_the_previous_one(
    runtime, monkeypatch
) -> None:
    hour = timedelta(hours=1)
    add_session(runtime, T_APP - 3 * hour, T_APP - 2 * hour, "tesla")  # keine offene Session
    await asyncio.to_thread(runtime._refresh_stats)
    before = runtime._stats_inputs.sessions["tesla"]
    assert before is not None and before.end == T_APP - 2 * hour

    def broken(*_args, **_kwargs):
        raise ValueError("kaputt")

    monkeypatch.setattr(runtime.charging_sessions, "sessions", broken)  # Rückfall der Suche
    with capture_logs() as logs:
        await asyncio.to_thread(runtime._refresh_stats)
    assert runtime._stats_inputs.sessions["tesla"] is before
    assert [log["event"] for log in logs].count(
        "Ladesession für Home Assistant nicht bestimmbar"
    ) == 2  # tesla und egolf


async def test_stats_loop_survives_a_failing_refresh(runtime, monkeypatch) -> None:
    from bindaems.app import runtime as module

    monkeypatch.setattr(module, "STATS_S", 0.02)
    attempts: list[int] = []

    def refresh() -> None:
        attempts.append(1)
        raise RuntimeError("kaputt")

    monkeypatch.setattr(runtime, "_refresh_stats", refresh)
    task = asyncio.create_task(runtime._stats_loop())
    try:
        await until_calls(attempts, 3)
    finally:
        await stop(task)


async def test_stats_inputs_start_unknown(runtime) -> None:
    assert isinstance(runtime._stats_inputs, StatsInputs)
    assert runtime._stats_inputs == StatsInputs(None, {}, {})


def history_body(now: datetime, days: int = 92) -> str:
    """Antwort von Open-Meteo mit ``days`` Tagen Temperatur bis ``now`` (konstant 7 °C)."""
    count = days * 96
    first = now - timedelta(days=days) + timedelta(minutes=15)
    stamps = [int((first + i * timedelta(minutes=15)).timestamp()) for i in range(count)]
    return json.dumps(
        {
            "minutely_15_units": {"time": "unixtime", "temperature_2m": "°C"},
            "minutely_15": {"time": stamps, "temperature_2m": [7.0] * count},
        }
    )


async def test_weather_backfill_runs_once_at_start(runtime, respx_mock) -> None:
    route = respx_mock.get(OPEN_METEO_URL).respond(200, text=history_body(T_APP))
    await runtime._weather_backfill()
    assert route.call_count == 1
    assert runtime.weather.oldest() == T_APP - timedelta(days=92)
    found = runtime.weather.temperatures(T_APP - timedelta(days=1), T_APP)
    assert len(found) == 96 and set(found.values()) == {7.0}
    await runtime._weather_backfill()  # das Archiv ist nun 92 Tage alt
    assert route.call_count == 1


async def test_backfill_failure_is_logged_and_retried_next_start(runtime, respx_mock) -> None:
    route = respx_mock.get(OPEN_METEO_URL).respond(503)
    with capture_logs() as logs:
        await runtime._weather_backfill()  # wirft nicht: die app läuft weiter
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [e["event"] for e in warnings] == ["Temperatur-Historie von Open-Meteo nicht abrufbar"]
    assert "HTTP 503" in warnings[0]["error"]
    assert runtime.weather.oldest() is None
    route.respond(200, text=history_body(T_APP))
    await runtime._weather_backfill()  # nächster Start
    assert route.call_count == 2 and runtime.weather.oldest() == T_APP - timedelta(days=92)


async def test_weather_backfill_survives_network_errors(runtime, respx_mock) -> None:
    respx_mock.get(OPEN_METEO_URL).mock(side_effect=httpx.ConnectError("kein Netz"))
    with capture_logs() as logs:
        await runtime._weather_backfill()
    assert "Temperatur-Historie von Open-Meteo nicht abrufbar" in [e["event"] for e in logs]


def test_runtime_wires_the_load_model(runtime, tmp_path) -> None:
    assert runtime.model_registry._directory == tmp_path / "models" / "load"
    status = runtime.load_model.component_status()
    assert (status.name, status.ok, status.message) == ("load-model", False, "Noch kein Modell")


async def test_runtime_reissues_the_load_forecast_when_the_model_changes(
    runtime, monkeypatch
) -> None:
    assert not runtime.load_forecast._wake.is_set()
    monkeypatch.setattr(runtime.load_model, "active", lambda: True)  # etwa nach der Bewertung
    runtime.load_model._announce()
    assert runtime.load_forecast._wake.is_set()


async def test_weather_backfill_releases_the_first_training_even_after_failure(
    runtime, respx_mock
) -> None:
    respx_mock.get(OPEN_METEO_URL).respond(503)
    assert not runtime._weather_backfilled.is_set()
    await runtime._weather_backfill()
    assert runtime._weather_backfilled.is_set()


@pytest.mark.respx(assert_all_called=False)
async def test_runtime_starts_the_load_model_after_the_weather_backfill(
    runtime, respx_mock, monkeypatch
) -> None:
    respx_mock.route().respond(503)
    trained: list[bool] = []

    async def fake_train():
        trained.append(runtime._weather_backfilled.is_set())
        return None

    monkeypatch.setattr(runtime.load_model, "train", fake_train)
    task = asyncio.create_task(runtime.run())
    await until(lambda: bool(trained))
    assert "load-model" in {job.get_name() for job in runtime._tasks}
    assert trained == [True]  # erst nach der Nachholung des Wetterarchivs
    await runtime.shutdown()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.respx(assert_all_called=False)
async def test_runtime_starts_weather_backfill_as_one_shot_job(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)
    task = asyncio.create_task(runtime.run())
    await until(lambda: any(job.get_name() == "weather-backfill" for job in runtime._tasks))
    job = next(job for job in runtime._tasks if job.get_name() == "weather-backfill")
    await asyncio.wait_for(asyncio.shield(job), timeout=2)  # endet von selbst, ohne Neustart
    await runtime.shutdown()
    await asyncio.wait_for(task, timeout=2)
