import asyncio
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.app_helpers import PASSWORD, T_APP, StopScheduler, record_then_stop
from tests.plan_helpers import sample_plan, set_planning, until
from tests.unit.shared.test_decisions import decision

from bindaems.app.control.decisions import DecisionQuery
from bindaems.app.control.intent import CoreDown
from bindaems.app.ha.entities import build_inputs
from bindaems.app.runtime import AppRuntime
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import Intent, SupportIntent
from bindaems.shared.decisions import decision_to_json
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import ManualClock

TOKEN = "x" * 32


def make_runtime(cfg: Config, tmp_path: Path, ha_token: str | None = None) -> AppRuntime:
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


def test_without_token_the_rejections_are_only_logged(runtime) -> None:
    assert runtime.ha_notifier is None and runtime.alarm_notifications is None
    assert type(runtime.ha_publisher._commands).__name__ == "HaCommandHandler"
    assert type(runtime.ha_publisher._commands._rejections).__name__ == "LogRejections"


def test_with_token_the_notifier_takes_over_the_rejections(cfg: Config, tmp_path) -> None:
    rt = make_runtime(cfg, tmp_path, ha_token="h" * 20)
    try:
        assert rt.ha_notifier is not None and rt.alarm_notifications is not None
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
    calls: list[str] = []
    monkeypatch.setattr(runtime.live_feed, "refresh_health", lambda: calls.append("health"))
    monkeypatch.setattr(runtime.plan_sender, "wake", lambda: calls.append("wake"))
    await runtime._core_connected()
    assert calls == ["health", "wake"]


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
    assert "plan-sender" in {job.get_name() for job in runtime._tasks}
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
