"""Laufzeit mit Betriebszuständen, Absicht, Sperren, Eingangsprüfung und Heartbeat (Plan 2a),
dazu Aktoren, sicherer Zustand, Entscheidungslog, Übergabe, Heartbeat-Regel und fremde Änderungen
(Plan 2b)."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.helpers import T0, FakeSink, run_until, snap
from tests.integration.conftest import TOKEN
from tests.integration.plant import Plant
from tests.unit.core.adapters.conftest import FakeTransport, P

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.actuators.victron import VictronActuator
from bindaems.core.adapters.victron_mqtt import VictronMqttAdapter
from bindaems.core.api.app import create_api
from bindaems.core.control.actuator import Confirmation
from bindaems.core.control.intent import INTENT_FILE_PROBLEM
from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.runtime import CoreRuntime
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import ActuatorMode, CommandKind, Intent
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.domain import Alarm, Severity, SignalKind
from bindaems.shared.timeutil import ManualClock

GOOD_STATE = {
    "vebus.phases": 3,
    "ess.hub4_mode": 1,
    "ess.batterylife_state": 10,
    "dess.mode": 0,
    "ess.schedule.0.day": -7,
    "ess.min_soc_pct": 20,
    "wallbox.evcs.gx_mode": 0,
}
DESS = "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern."
# Watchdog am Cerbo wartet und meldet das konfigurierte Timeout: Der core darf ihn scharf machen
WATCHDOG_WAITING = {"watchdog.state": 0, "watchdog.timeout_s": 90}


def run(rt, clock, seconds: int) -> None:
    for _ in range(seconds):
        rt.cycle_once()
        clock.advance(1)


def record_broadcast(rt) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    publish = rt.broadcast.publish

    def both(message: dict[str, Any]) -> None:
        messages.append(message)
        publish(message)

    rt.broadcast.publish = both
    return messages


def allow_evcs_live(cfg: Config) -> Config:
    evcs = cfg.wallboxes["evcs"].model_copy(update={"live_allowed": True})
    return cfg.model_copy(update={"wallboxes": cfg.wallboxes | {"evcs": evcs}})


def allow_tesla_live(cfg: Config) -> Config:
    tesla = cfg.vehicles["tesla"].model_copy(update={"live_allowed": True})
    return cfg.model_copy(update={"vehicles": cfg.vehicles | {"tesla": tesla}})


def live_evcs(runtime_env, *, plant: Plant, state: dict, **kw: Any):
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
        **kw,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    return rt, clock


async def test_starts_in_start_then_observe(runtime_env) -> None:
    rt, _, _ = runtime_env()
    assert rt.health().mode == "START"
    rt.cycle_once()
    assert (rt.health().mode, rt.health().mode_reasons) == ("OBSERVE", ["Regelbetrieb ist aus."])


async def test_control_after_30s_fresh_data(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    messages = record_broadcast(rt)
    assert rt.submit_intent(Intent(version=1, control=True)).result == "accepted"
    run(rt, clock, 32)
    assert rt.health().mode == "CONTROL"
    assert [m["data"]["mode"] for m in messages if m["type"] == "mode_change"] == [
        "OBSERVE",
        "CONTROL",
    ]


async def test_dess_locks_victron_but_control_runs(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"dess.mode": 1})
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    health = rt.health()
    assert health.mode == "DEGRADED"
    assert f"Victron (ESS) gesperrt: {DESS}" in health.mode_reasons
    locked = {a["id"]: a["locked"] for a in health.actuators}
    assert locked["victron"] == [DESS] and locked["wallbox.evcs"] == []


async def test_safe_when_cerbo_data_stale(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    rt.store.set_connected("victron", False)
    rt.cycle_once()
    health = rt.health()
    assert health.mode == "SAFE"
    assert health.mode_reasons[0].startswith("Cerbo-Daten veraltet: grid.l1.power_w, ")
    assert any(a["id"] == "core.safe" for a in health.alarms)


async def test_degraded_when_wallbox_unreachable(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.store.register_source("evcs", 5.0)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    assert rt.health().mode_reasons == ["EVCS „evcs“ nicht erreichbar."]


async def test_heartbeat_starts_when_the_gate_waits_only_for_the_watchdog(runtime_env) -> None:
    plant = Plant(watchdog_state=1)  # meldet „scharf“, für diesen core erst mit seinem Echo
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 30)  # Zyklen bei 0 bis 29 s: Messwerte noch nicht 30 s frisch
    assert plant.heartbeats() == []
    run(rt, clock, 1)  # bei 30 s wartet das Gate nur noch auf den Watchdog: Zähler 1
    health = rt.health()
    assert plant.heartbeats() == [1]
    assert (health.mode, health.watchdog["heartbeat"]) == ("OBSERVE", "active")
    assert health.mode_reasons == ["Watchdog am Cerbo ist nicht scharf."]
    run(rt, clock, 1)  # Echo 1: scharf für diesen core
    assert rt.health().mode == "CONTROL"


async def test_release_when_live_dropped(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus
    run(rt, clock, 1)  # Übergabe: Sicherwert gesendet
    assert plant.heartbeats() == [1]  # noch keine Freigabe
    run(rt, clock, 1)  # zurückgelesen: übergeben, im selben Zyklus die Freigabe
    assert (plant.heartbeats(), rt.health().watchdog["heartbeat"]) == ([1, 0], "released")


async def test_shutdown_sends_no_release(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 33)
    await rt.shutdown()
    assert plant.heartbeats() == [1]


async def test_intent_survives_restart(runtime_env) -> None:
    first, _, _ = runtime_env()
    first.submit_intent(Intent(version=4, control=True))
    second, _, _ = runtime_env()
    assert second.intent() == Intent(version=4, control=True)


async def test_health_lists_actuators_and_watchdog(runtime_env) -> None:
    rt, _, _ = runtime_env(state=GOOD_STATE)
    rt.cycle_once()
    health = rt.health()
    assert [a["id"] for a in health.actuators] == ["victron", "wallbox.evcs", "vehicle.tesla"]
    assert health.actuators[1] == {
        "id": "wallbox.evcs",
        "label": "EVCS „evcs“",
        "requested": "dry_run",
        "effective": "dry_run",
        "live_allowed": False,
        "reasons": [],
        "locked": [],
        "controllable": True,
        "capability_reasons": [],
        "pending": [],
        "owned": False,
    }
    assert health.watchdog == {
        "state": "unknown",
        "trip_count": None,
        "last_trip": None,
        "last_trip_failures": None,
        "version": None,
        "expected_version": WATCHDOG_VERSION,
        "heartbeat": "off",
        "required": False,
    }
    assert health.inputs == {"ok": True, "problems": []}


async def test_alarm_since_is_stable(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"dess.mode": 1})
    run(rt, clock, 12)
    first = {a["id"]: a["since"] for a in rt.health().alarms}
    run(rt, clock, 10)
    assert {a["id"]: a["since"] for a in rt.health().alarms}["competitor.dess"] == first[
        "competitor.dess"
    ]


# --- Verdrahtung, Texte und Reihenfolgen ------------------------------------------------------

WATCHDOG_ARMED = {
    "watchdog.state": 1,
    "watchdog.trip_count": 2,
    "watchdog.last_trip_time": 1760000000,  # 09.10.2025, 08:53:20 UTC
    "watchdog.last_trip_failures": 0,
    "watchdog.version": WATCHDOG_VERSION,
}


def locked(rt) -> dict[str, list[str]]:
    return {a["id"]: a["locked"] for a in rt.health().actuators}


async def test_gate_opens_when_data_is_exactly_30s_fresh(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 30)  # Zyklen bei 0 bis 29 s
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "OBSERVE",
        ["Kritische Messwerte noch nicht 30 s aktuell."],
    )
    run(rt, clock, 1)  # bei 30 s
    assert rt.health().mode == "CONTROL"


async def test_gate_needs_three_phases(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"vebus.phases": 1})
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "OBSERVE",
        ["3 Phasen mit je einem Gerät nicht bestätigt."],
    )


async def test_mode_change_is_streamed_before_the_state_and_logged(runtime_env) -> None:
    rt, _, _ = runtime_env()
    messages = record_broadcast(rt)
    with capture_logs() as logs:
        rt.cycle_once()
        rt.cycle_once()
    assert [m["type"] for m in messages] == ["mode_change", "state", "state"]
    assert messages[0]["data"] == {
        "mode": "OBSERVE",
        "previous": "START",
        "reasons": ["Regelbetrieb ist aus."],
        "ts": "2026-10-08T10:00:00+00:00",
    }
    assert rt.health().mode_since == "2026-10-08T10:00:00+00:00"
    changes = [e for e in logs if e["event"] == "Betriebszustand gewechselt"]
    assert [(e["log_level"], e["mode"], e["previous"], e["reasons"]) for e in changes] == [
        ("info", "OBSERVE", "START", ["Regelbetrieb ist aus."])
    ]


async def test_island_operation_is_safe_with_alarm(runtime_env) -> None:
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    assert rt.health().mode == "CONTROL"
    state |= {"system.active_in_source": 240, "vebus.phases": 1}  # Inselbetrieb, 1 Phase
    run(rt, clock, 1)
    health = rt.health()
    reasons = ["Netzausfall (Inselbetrieb).", "3 Phasen mit je einem Gerät nicht bestätigt."]
    assert (health.mode, health.mode_reasons) == ("SAFE", reasons)
    assert health.mode_since == "2026-10-08T10:00:31+00:00"
    assert [a for a in health.alarms if a["id"] == "core.safe"] == [
        {
            "id": "core.safe",
            "severity": "error",
            "message": f"Sicherer Zustand: {'; '.join(reasons)}",
            "since": "2026-10-08T10:00:31+00:00",
        }
    ]


async def test_grid_source_is_no_island(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"system.active_in_source": 1})
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    assert rt.health().mode == "CONTROL"


async def test_degraded_reasons_in_order(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"dess.mode": 1, "ess.schedule.0.day": 1})
    for source in ("evcs", "twc", "tessie:tesla"):
        rt.store.register_source(source, 5.0)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    assert rt.health().mode_reasons == [
        "EVCS „evcs“ nicht erreichbar.",
        "Wall Connector „twc“ nicht erreichbar.",
        "Tessie für „Tesla Model 3“ nicht erreichbar.",
        f"Victron (ESS) gesperrt: {DESS}; Victron-Ladefenster 0 ist aktiv.",
    ]
    rt.store.set_connected("twc", True)
    run(rt, clock, 1)
    assert "Wall Connector „twc“ nicht erreichbar." not in rt.health().mode_reasons


async def test_locks_follow_the_catalog_order(runtime_env) -> None:
    state = dict(GOOD_STATE | {"wallbox.evcs.gx_mode": 1})
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 1)  # Prüfung bei 0 s: nur die EVCS ist gesperrt
    state["dess.mode"] = 1
    run(rt, clock, 30)  # Prüfung bei 10 s: Victron kommt dazu; das Gate öffnet bei 30 s
    evcs = "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."
    assert rt.health().mode_reasons == [
        f"Victron (ESS) gesperrt: {DESS}",
        f"EVCS „evcs“ gesperrt: {evcs}",
    ]
    assert locked(rt) == {"victron": [DESS], "wallbox.evcs": [evcs], "vehicle.tesla": []}


async def test_lock_released_60s_after_the_cause_is_gone(runtime_env) -> None:
    state = dict(GOOD_STATE | {"dess.mode": 1})
    rt, _, clock = runtime_env(state=state)
    run(rt, clock, 1)  # Prüfung bei 0 s: Victron gesperrt
    state["dess.mode"] = 0
    run(rt, clock, 69)  # Prüfung bei 10 s ohne Ursache; Zyklen bis 69 s, die Uhr steht auf 70 s
    assert locked(rt)["victron"] == [DESS]  # health zeigt den Stand des letzten Zyklus
    run(rt, clock, 1)  # Zyklus bei 70 s: 60 s ohne Ursache
    assert locked(rt)["victron"] == []


async def test_alarm_since_restarts_after_the_alarm_was_gone(runtime_env) -> None:
    state = dict(GOOD_STATE | {"dess.mode": 1})
    rt, _, clock = runtime_env(state=state)
    run(rt, clock, 1)  # Alarm seit 0 s
    state["dess.mode"] = 0
    run(rt, clock, 10)  # Prüfung bei 10 s: Alarm weg
    assert "competitor.dess" not in {a["id"] for a in rt.health().alarms}
    state["dess.mode"] = 1
    run(rt, clock, 10)  # Prüfung bei 20 s: Alarm wieder da
    since = {a["id"]: a["since"] for a in rt.health().alarms}
    assert since["competitor.dess"] == "2026-10-08T10:00:20+00:00"


async def test_unreadable_intent_file_raises_alarm_until_a_new_intent(
    runtime_env, tmp_path
) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "intent.json").write_text("{kaputt")
    rt, _, clock = runtime_env()
    run(rt, clock, 1)
    assert [a for a in rt.health().alarms if a["id"] == "intent.file"] == [
        {
            "id": "intent.file",
            "severity": "warning",
            "message": INTENT_FILE_PROBLEM,
            "since": "2026-10-08T10:00:00+00:00",
        }
    ]
    rt.submit_intent(Intent(version=1))
    run(rt, clock, 1)
    assert "intent.file" not in {a["id"] for a in rt.health().alarms}


async def test_health_reports_intent_live_actuator_and_watchdog(runtime_env) -> None:
    plant = Plant(watchdog_state=1)  # scharf mit dem Echo des eigenen Zählers
    rt, _, clock = runtime_env(
        state=GOOD_STATE | WATCHDOG_ARMED,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=2, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 32)  # Zähler 1 bei 30 s, sein Echo bei 31 s
    health = rt.health()
    assert health.intent == {"version": 2, "control": True, "actuators": {"wallbox.evcs": "live"}}
    assert health.actuators[1] == {
        "id": "wallbox.evcs",
        "label": "EVCS „evcs“",
        "requested": "live",
        "effective": "live",
        "live_allowed": True,
        "reasons": [],
        "locked": [],
        "controllable": True,
        "capability_reasons": [],
        "pending": [],
        "owned": False,
    }
    assert health.watchdog == {
        "state": "armed",
        "trip_count": 2,
        "last_trip": "2025-10-09T08:53:20+00:00",
        "last_trip_failures": 0,
        "version": WATCHDOG_VERSION,
        "expected_version": WATCHDOG_VERSION,
        "heartbeat": "active",
        "required": True,
    }


async def test_unarmed_watchdog_keeps_live_request_in_dry_run(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_evcs_live)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 1)
    evcs = rt.health().actuators[1]
    assert (evcs["requested"], evcs["effective"], evcs["reasons"]) == (
        "live",
        "dry_run",
        ["Watchdog am Cerbo ist nicht scharf."],
    )
    unavailable = [a for a in rt.health().alarms if a["id"] == "watchdog.unavailable"]
    assert [a["severity"] for a in unavailable] == ["error"]


async def test_inputs_report_missing_and_implausible_values(runtime_env) -> None:
    rt, _, _ = runtime_env(omit=["battery.power_w"], extra={"grid.l1.voltage_v": 300.0})
    rt.cycle_once()
    assert rt.health().inputs == {
        "ok": False,
        "problems": [
            "Messwert fehlt oder ist veraltet: battery.power_w.",
            "Netzspannung L1 unplausibel: 300.0 V",
        ],
    }


async def test_inputs_are_checked_in_every_cycle(runtime_env) -> None:
    values = {"battery.soc_pct": 50.0}
    rt, _, clock = runtime_env(extra=values)
    run(rt, clock, 1)
    values["battery.soc_pct"] = 60.0
    run(rt, clock, 1)  # bei 1 s, ohne Selbstprüfung
    assert rt.health().inputs == {"ok": False, "problems": ["Sprung im Akku-SOC: 50 % → 60 %."]}


async def test_billing_counter_alarm_is_no_input_problem(runtime_env) -> None:
    frozen = {"grid.energy_import_kwh": 1000.0, "grid.energy_export_kwh": 500.0}
    rt, _, clock = runtime_env(start="2026-10-08T09:59:55+00:00", extra=frozen)
    for _ in range(182):  # wie test_frozen_grid_counter_raises_alarm_after_full_slot
        rt.cycle_once()
        clock.advance(5)
    health = rt.health()
    assert "plaus.grid_counter" in {a["id"] for a in health.alarms}
    assert health.inputs == {"ok": True, "problems": []}


async def test_failed_cycle_sends_no_heartbeat(runtime_env, monkeypatch) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE | WATCHDOG_WAITING,
        configure=allow_evcs_live,
        watchdog_send=lambda n: sent.append(n) or True,
    )
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 30)  # bei 30 s wartet das Gate nur noch auf den Watchdog

    def broken(*args: object, **kwargs: object) -> list[Alarm]:
        raise RuntimeError("Testfehler beim Aktualisieren der Alarme")

    with monkeypatch.context() as patch:
        patch.setattr("bindaems.core.runtime.watchdog_alarms", broken)
        with pytest.raises(RuntimeError):
            rt.cycle_once()
    assert sent == []
    rt.cycle_once()
    assert sent == [1]


async def test_heartbeat_goes_through_the_victron_adapter(runtime_env, cfg) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | WATCHDOG_WAITING, configure=allow_evcs_live)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 30)  # bei 30 s wartet das Gate nur noch auf den Watchdog
    transport = FakeTransport([rt.cycle_once])  # ein Zyklus während der MQTT-Sitzung
    mqtt = cfg.victron.mqtt.model_copy(update={"portal_id": P})
    victron = VictronMqttAdapter(
        cfg.victron.model_copy(update={"mqtt": mqtt}), None, rt.store, lambda: transport
    )
    rt.adapters.append(victron)
    heartbeat = (f"W/{P}/bindaems/0/Heartbeat", b'{"value": 1}')
    await run_until(victron, lambda: heartbeat in transport.published)
    assert rt.health().watchdog["heartbeat"] == "active"


async def test_no_heartbeat_without_victron_adapter(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | WATCHDOG_WAITING, configure=allow_evcs_live)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 31)  # ab 30 s wartet das Gate nur noch auf den Watchdog: Der Zähler ist fällig
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "OBSERVE",
        ["Watchdog am Cerbo ist nicht scharf."],
    )
    # Ohne Victron-Adapter nimmt niemand den Zähler an: Er gilt nicht als gesendet.
    assert (health.watchdog["required"], health.watchdog["heartbeat"]) == (True, "off")


# --- Aktoren, sicherer Zustand und Entscheidungslog (Plan 2b) --------------------------------


async def test_island_sends_safe_values_to_live_evcs(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    state |= {"system.active_in_source": 240}  # Inselbetrieb
    run(rt, clock, 3)
    assert ("evcharger/40/SetCurrent", 6) in plant.writes
    assert ("evcharger/40/StartStop", 0) in plant.writes
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    assert {(d.kind, d.result) for d in evcs} == {
        (CommandKind.CURRENT, DecisionResult.OK),
        (CommandKind.CHARGING, DecisionResult.OK),
    }


async def test_dry_run_safe_state_is_logged_once(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(state=state, mqtt_write=plant.write, before_cycle=plant.before_cycle)
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True))  # alles im Dry-Run
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 30)
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    assert [(d.kind, d.result) for d in evcs] == [
        (CommandKind.CURRENT, DecisionResult.DRY_RUN),
        (CommandKind.CHARGING, DecisionResult.DRY_RUN),
    ]
    assert not any(path.startswith("evcharger/") for path, _ in plant.writes)


async def test_decisions_are_streamed(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    messages = record_broadcast(rt)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)
    evcs = [
        m["data"]
        for m in messages
        if m["type"] == "decision" and m["data"]["actuator"] == "wallbox.evcs"
    ]
    assert [d["result"] for d in evcs] == ["ok", "ok"]


async def test_decisions_over_the_api_are_those_of_the_stream(runtime_env) -> None:
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state)
    messages = record_broadcast(rt)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    state |= {"system.active_in_source": 240}
    island = clock.now()
    run(rt, clock, 1)
    streamed = [m["data"] for m in messages if m["type"] == "decision"]
    assert len(streamed) == 6
    client = TestClient(create_api(rt, SecretStr(TOKEN)))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    # Die sechs Entscheidungen eines Zyklus haben dieselbe Zeit und bleiben bei limit=1 beisammen.
    page = client.get("/v1/decisions", params={"since": T0.isoformat(), "limit": 1}, headers=auth)
    assert (page.status_code, page.json()) == (200, {"decisions": streamed, "more": False})
    # Wer ab der Zeit der letzten Entscheidung weiterliest, bekommt keine doppelt.
    rest = client.get("/v1/decisions", params={"since": island.isoformat()}, headers=auth)
    assert (rest.status_code, rest.json()) == (200, {"decisions": [], "more": False})


async def test_health_lists_capabilities(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    run(rt, clock, 1)
    evcs = next(a for a in rt.health().actuators if a["id"] == "wallbox.evcs")
    assert (evcs["controllable"], evcs["pending"]) == (True, [])


# Verdrahtung der Aktoren


def actuator_health(rt, actuator: str) -> dict[str, Any]:
    return next(a for a in rt.health().actuators if a["id"] == actuator)


async def test_actuators_follow_the_catalog(runtime_env) -> None:
    rt, _, _ = runtime_env()
    assert list(rt.actuators) == list(rt.limits.actuators)
    victron, evcs, tesla = rt.actuators.values()
    assert isinstance(victron, VictronActuator)
    assert isinstance(evcs, EvcsActuator) and isinstance(tesla, TeslaActuator)


async def test_evcs_without_its_evcharger_instance_is_not_controllable(runtime_env) -> None:
    def without_instance(cfg: Config) -> Config:
        instances = cfg.victron.instances.model_copy(update={"evcharger": {}})
        victron = cfg.victron.model_copy(update={"instances": instances})
        return cfg.model_copy(update={"victron": victron})

    rt, _, clock = runtime_env(state=GOOD_STATE, configure=without_instance)
    run(rt, clock, 1)
    evcs = actuator_health(rt, "wallbox.evcs")
    assert (evcs["controllable"], evcs["capability_reasons"]) == (
        False,
        ["Keine evcharger-Instanz in victron.instances.evcharger."],
    )


async def test_evcs_and_victron_follow_the_cerbo_connection(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.store.set_connected("victron", False)
    run(rt, clock, 1)
    assert actuator_health(rt, "victron")["capability_reasons"] == ["Cerbo nicht verbunden."]
    assert "Cerbo nicht verbunden." in actuator_health(rt, "wallbox.evcs")["capability_reasons"]


def tesla_health(cfg: Config, tmp_path: Path, token: str | None) -> dict[str, Any]:
    """Der Tesla nach einem Zyklus: Tessie verbunden, Fahrzeug online, zu Hause, angesteckt."""
    core = cfg.core.model_copy(update={"data_dir": tmp_path / "core"})
    secrets = Secrets(
        internal_token=SecretStr(TOKEN),
        tessie_token=SecretStr(token) if token is not None else None,
    )
    rt = CoreRuntime(
        cfg.model_copy(update={"core": core}),
        secrets,
        clock=ManualClock(T0),
        safety_clock=ManualClock(T0),
        adapters=[],
        sink=FakeSink(),
        serve_api=False,
    )
    rt.store.register_source("tessie:tesla", 900.0)
    rt.store.set_connected("tessie:tesla", True)
    for name in ("online", "at_home", "plugged"):
        rt.store.update(f"vehicle.tesla.{name}", True, source="tessie:tesla", kind=SignalKind.STATE)
    rt.cycle_once()
    return actuator_health(rt, "vehicle.tesla")


async def test_tesla_is_reachable_only_with_a_tessie_token(cfg, tmp_path) -> None:
    without = tesla_health(cfg, tmp_path / "ohne", None)
    assert (without["controllable"], without["capability_reasons"]) == (
        False,
        ["Tessie nicht erreichbar."],
    )
    with_token = tesla_health(cfg, tmp_path / "mit", "t")
    assert (with_token["controllable"], with_token["capability_reasons"]) == (True, [])


# Uhren, Spool und Dateien der Safety-Schicht


async def test_deadlines_run_on_the_safety_clock_and_decisions_on_the_wall_clock(
    runtime_env,
) -> None:
    safety = ManualClock(T0 - timedelta(days=1))  # läuft anders als die Wanduhr
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
        safety_clock=safety,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    for _ in range(33):
        rt.cycle_once()
        clock.advance(1)
        safety.advance(1)
    state |= {"system.active_in_source": 240}
    rt.cycle_once()  # SAFE: Strom und Laden aus gesendet, Frist 10 s
    clock.advance(60)  # nur die Wanduhr läuft: Die Frist ist noch offen
    rt.cycle_once()
    assert actuator_health(rt, "wallbox.evcs")["pending"] == ["current", "charging"]
    safety.advance(10)
    rt.cycle_once()
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    assert [(d.kind, d.result) for d in evcs] == [
        (CommandKind.CURRENT, DecisionResult.TIMEOUT),
        (CommandKind.CHARGING, DecisionResult.TIMEOUT),
    ]
    assert [d.ts for d in evcs] == [clock.now(), clock.now()]


async def test_all_decisions_of_a_cycle_share_its_time(runtime_env) -> None:
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    state |= {"system.active_in_source": 240}
    island = clock.now()
    run(rt, clock, 1)
    decisions = rt.decisions(T0, 100)[0]
    assert [(d.actuator, d.kind) for d in decisions] == [
        ("victron", CommandKind.GRID_SETPOINT),
        ("victron", CommandKind.MAX_DISCHARGE),
        ("wallbox.evcs", CommandKind.CURRENT),
        ("wallbox.evcs", CommandKind.CHARGING),
        ("vehicle.tesla", CommandKind.CURRENT),
        ("vehicle.tesla", CommandKind.CHARGING),
    ]
    assert {d.ts for d in decisions} == {island}
    assert (
        decisions[2].text == "EVCS „evcs“ (sicherer Zustand): Strom 6 A – Dry-Run, nicht gesendet."
    )
    assert decisions[3].text == "EVCS „evcs“ (Netzausfall): Laden aus – Dry-Run, nicht gesendet."
    assert decisions[2].inputs == {
        "soc_pct": 50.0,
        "battery_w": 600.0,
        "grid_l1_a": pytest.approx(1000.0 / 230.0),
        "grid_l2_a": pytest.approx(1000.0 / 230.0),
        "grid_l3_a": pytest.approx(1000.0 / 230.0),
    }


async def test_locked_actuators_get_no_safe_values(runtime_env) -> None:
    state = GOOD_STATE | {"dess.mode": 1}  # Dynamic ESS sperrt Victron
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    assert rt.health().mode == "DEGRADED"
    state |= {"system.active_in_source": 240}
    run(rt, clock, 1)
    assert rt.health().mode == "SAFE"
    assert {d.actuator for d in rt.decisions(T0, 100)[0]} == {"wallbox.evcs", "vehicle.tesla"}


async def test_decisions_reach_the_spool_even_when_the_cycle_fails(
    runtime_env, monkeypatch
) -> None:
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    state |= {"system.active_in_source": 240}

    def broken(*args: object, **kwargs: object) -> list[Alarm]:
        raise RuntimeError("Testfehler nach den Befehlen")

    with monkeypatch.context() as patch:
        patch.setattr("bindaems.core.runtime.watchdog_alarms", broken)
        with pytest.raises(RuntimeError, match="nach den Befehlen"):
            rt.cycle_once()
    decisions = rt.decisions(T0, 100)[0]
    assert {d.actuator for d in decisions} == {"victron", "wallbox.evcs", "vehicle.tesla"}
    assert {d.result for d in decisions} == {DecisionResult.DRY_RUN}


async def test_an_actuator_error_fails_the_cycle_but_keeps_its_decisions(runtime_env) -> None:
    plant = Plant(watchdog_state=1)

    def write(path: str, value: float | int | None) -> bool:
        if path.startswith("evcharger/"):
            raise RuntimeError("Testfehler im Aktor")
        return plant.write(path, value)

    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(
        state=state, configure=allow_evcs_live, mqtt_write=write, before_cycle=plant.before_cycle
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)  # Heartbeat bei 30 s (Scharfschalten); die Uhr steht auf 33 s
    sent = plant.heartbeats()
    state |= {"system.active_in_source": 240}
    clock.advance(7)  # bei 40 s ist der nächste Heartbeat fällig
    with pytest.raises(RuntimeError, match="Testfehler im Aktor"):
        rt.cycle_once()
    assert plant.heartbeats() == sent == [1]  # kein Heartbeat aus diesem Zyklus
    # Victron kam vor der EVCS an die Reihe: seine Entscheidungen sind im Spool
    assert {d.actuator for d in rt.decisions(T0, 100)[0]} == {"victron"}


async def test_old_decisions_are_pruned_at_start_and_every_hour(runtime_env, tmp_path) -> None:
    directory = tmp_path / "core" / "decisions"
    directory.mkdir(parents=True)
    old = directory / "decisions-2026-09-01.jsonl"
    old.write_text("")
    rt, _, clock = runtime_env()
    run(rt, clock, 1)
    assert not old.exists()  # beim Start
    old.write_text("")
    clock.advance(3598)
    run(rt, clock, 1)  # bei 3599 s: noch keine Stunde
    assert old.exists()
    run(rt, clock, 1)  # bei 3600 s
    assert not old.exists()


async def test_prune_counts_the_hour_on_the_safety_clock_and_the_age_on_the_wall_clock(
    runtime_env, tmp_path
) -> None:
    safety = ManualClock(T0 - timedelta(days=30))  # weit hinter der Wanduhr
    directory = tmp_path / "core" / "decisions"
    directory.mkdir(parents=True)
    old = directory / "decisions-2026-09-30.jsonl"  # nach der Wanduhr älter als 7 Tage
    old.write_text("")
    rt, _, clock = runtime_env(safety_clock=safety)
    run(rt, clock, 1)
    assert not old.exists()
    old.write_text("")
    clock.advance(7200)  # nur die Wanduhr springt: auf der anderen Uhr keine Stunde
    run(rt, clock, 1)
    assert old.exists()
    safety.advance(3600)
    run(rt, clock, 1)
    assert not old.exists()


async def test_a_failing_prune_is_logged_and_the_cycle_goes_on(runtime_env, monkeypatch) -> None:
    rt, _, clock = runtime_env()

    def broken(now: object) -> None:
        raise RuntimeError("Testfehler beim Aufräumen")

    monkeypatch.setattr(rt.decision_spool, "prune", broken)
    with capture_logs() as logs:
        run(rt, clock, 1)
    assert rt.health().mode == "OBSERVE"
    errors = [e["event"] for e in logs if e["log_level"] == "error"]
    assert errors == ["Entscheidungslog nicht aufgeräumt"]


async def test_rate_limits_and_write_budget_live_in_the_data_dir(runtime_env, tmp_path) -> None:
    rate_file = tmp_path / "core" / "rate_limits.json"
    entries = [  # 20 Tesla-Befehle in der Stunde vor dem Start: das Stundenlimit ist erreicht
        {
            "actuator": "vehicle.tesla",
            "kind": "current",
            "ts": (T0 - timedelta(minutes=m)).isoformat(),
        }
        for m in range(30, 10, -1)
    ]
    write_json_atomic(rate_file, {"version": 1, "entries": entries})
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 1)
    tesla = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "vehicle.tesla"]
    assert tesla[0].codes == ("safe_state", "urgent.bypass")  # geladen: über dem Limit
    assert len(read_json(rate_file)["entries"]) == 22  # dazu Strom und Laden aus
    assert len(read_json(tmp_path / "core" / "write_budget.json")["writes"]) == 1  # Entladegrenze


async def test_safety_layer_alarms_are_reported(runtime_env, tmp_path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "write_budget.json").write_text("{kaputt")
    rt, _, clock = runtime_env()
    run(rt, clock, 1)
    assert "safety.write_budget_file" in {a["id"] for a in rt.health().alarms}


async def test_actuator_alarms_get_the_safety_clock(runtime_env, monkeypatch) -> None:
    safety = ManualClock(T0 - timedelta(hours=2))
    rt, _, clock = runtime_env(safety_clock=safety)
    seen: list[object] = []

    def alarms(now: Any) -> list[Alarm]:
        seen.append(now)
        return [Alarm("actuator.unavailable.vehicle.tesla", Severity.WARNING, "Test", now)]

    monkeypatch.setattr(rt.actuators["vehicle.tesla"], "alarms", alarms)
    run(rt, clock, 1)
    assert seen == [safety.now()]
    assert "actuator.unavailable.vehicle.tesla" in {a["id"] for a in rt.health().alarms}


async def test_failed_safe_value_raises_alarm_until_observe_ends_the_incident(
    runtime_env,
) -> None:
    # Der Cerbo nimmt die Aufträge nicht an: Die EVCS bekommt keinen Wert und ist nicht owned,
    # mit Regelbetrieb aus gibt es also nichts zu übergeben.
    plant = Plant(watchdog_state=1, accept=False)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 31)  # Versuche bei 33, 43 und 53 s; bei 63 s gescheitert
    writes = [w for w in plant.writes if w[0] == "evcharger/40/SetCurrent"]
    assert len(writes) == 3
    failed = [a for a in rt.health().alarms if a["id"] == "actuator.failed.wallbox.evcs"]
    assert [(a["severity"], a["message"]) for a in failed] == [
        (
            "error",
            "EVCS „evcs“: Sicherwert für „current“, „charging“ nicht bestätigt. "
            "Der Watchdog am Cerbo übernimmt.",
        )
    ]
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus: OBSERVE beendet den Vorfall
    run(rt, clock, 1)
    assert rt.health().mode == "OBSERVE"
    assert "actuator.failed.wallbox.evcs" not in {a["id"] for a in rt.health().alarms}


async def test_shutdown_ends_tessie_commands_before_the_client_closes(
    runtime_env, respx_mock, monkeypatch
) -> None:
    never = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        await never.wait()  # Tessie antwortet nie
        return httpx.Response(200, json={"result": True})

    respx_mock.post("https://api.tessie.com/5YJ3E7EB0MF000000/command/set_charging_amps").mock(
        side_effect=hang
    )
    rt, _, _ = runtime_env()
    car = rt.actuators["vehicle.tesla"]
    at_close: list[tuple[Confirmation, str]] = []
    real_aclose = httpx.AsyncClient.aclose

    async def aclose(self: httpx.AsyncClient) -> None:
        at_close.append(car.confirm(CommandKind.CURRENT, 10.0, snap({})))
        await real_aclose(self)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", aclose)
    assert car.send(CommandKind.CURRENT, 10.0) is None  # läuft im Hintergrund
    await asyncio.sleep(0.01)
    await rt.shutdown()
    assert at_close == [(Confirmation.FAILED, "Befehl an Tessie abgebrochen.")]
    assert asyncio.all_tasks() == {asyncio.current_task()}


# --- Übergabe, Heartbeat-Regel und fremde Änderungen (Plan 2b) ------------------------------


async def test_restart_within_timeout_resumes_control(runtime_env) -> None:
    plant = Plant(watchdog_state=1, echo=1234)  # scharf, Zähler des früheren Laufs
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 29)
    assert plant.heartbeats() == [] and rt.health().mode == "OBSERVE"
    run(rt, clock, 3)  # Gate bis auf den Watchdog offen: Zähler 1, Echo, CONTROL
    assert plant.heartbeats() == [1] and rt.health().mode == "CONTROL"


async def test_restart_stuck_in_observe_lets_watchdog_trip(runtime_env) -> None:
    plant = Plant(watchdog_state=1, echo=1234)
    state = {k: v for k, v in GOOD_STATE.items() if k != "vebus.phases"}  # Phasen nicht bestätigt
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 100)
    assert rt.health().mode == "OBSERVE" and plant.heartbeats() == []


async def test_control_off_hands_back_then_observe(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(
        Intent(version=2, control=False, actuators={"wallbox.evcs": ActuatorMode.LIVE})
    )
    readback_at = zero_at = None  # Zyklus, in dem die EVCS 6 A meldet bzw. die 0 hinausgeht
    for cycle in range(4):
        run(rt, clock, 1)
        if cycle == 0:  # Übergabe: 6 A gesendet, die EVCS meldet sie erst im nächsten Zyklus
            health = rt.health()
            assert (health.mode, health.mode_reasons) == (
                "DEGRADED",
                ["Regelbetrieb wird beendet: Übergabe der Aktoren läuft."],
            )
            assert ("evcharger/40/SetCurrent", 6) in plant.writes
        if readback_at is None and rt.store.snapshot().ok("wallbox.evcs.gx_set_current_a") == 6:
            readback_at = cycle
        if zero_at is None and 0 in plant.heartbeats():
            zero_at = cycle
    assert rt.health().mode == "OBSERVE"
    assert plant.heartbeats()[-1] == 0  # Freigabe erst nach der Übergabe
    # Erst die Sicherwerte, dann das Rücklesen, erst danach die 0: keine Freigabe in einem Zyklus
    # vor dem, in dem die Sicherwerte zurückgelesen sind.
    assert readback_at == 1 and zero_at is not None and zero_at >= readback_at


async def test_failed_handback_stops_heartbeat(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2, control=True))  # EVCS auf Dry-Run
    run(rt, clock, 45)
    writes = [w for w in plant.writes if w[0] == "evcharger/40/SetCurrent"]
    assert len(writes) == 3
    sent_before = len(plant.heartbeats())
    run(rt, clock, 30)
    assert len(plant.heartbeats()) == sent_before  # kein Heartbeat, keine Freigabe
    assert any(a["id"] == "actuator.failed.wallbox.evcs" for a in rt.health().alarms)


async def test_foreign_change_locks_the_evcs(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)  # SAFE: 6 A geschrieben und bestätigt, jetzt erwartet
    # Netz wieder da: Der Zustandsspeicher behält den letzten Wert eines Signals, darum steht hier
    # die Netzquelle, statt das Signal aus ``state`` zu nehmen.
    state |= {"system.active_in_source": 1}
    state |= {"wallbox.evcs.gx_set_current_a": 10}
    plant.follow = False
    run(rt, clock, 40)
    locked = {a["id"]: a["locked"] for a in rt.health().actuators}
    assert locked["wallbox.evcs"] == ["EVCS „evcs“: Strom von außen geändert (10 A statt 6 A)."]


# Nur ein gesendeter Wert macht owned; ein Fehler kostet nur den Heartbeat seines Zyklus


async def test_a_refused_write_costs_one_heartbeat_and_owns_nothing(runtime_env) -> None:
    plant = Plant(watchdog_state=1, accept=False)  # der Cerbo nimmt keinen EVCS-Auftrag an
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 40)  # Zähler 1 bei 30 s, der nächste ist bei 40 s fällig
    state |= {"system.active_in_source": 240}
    run(rt, clock, 1)  # SAFE bei 40 s: Die Sicherwerte werden nicht gesendet
    assert ("evcharger/40/SetCurrent", 6) in plant.writes
    assert plant.heartbeats() == [1]  # der Zyklus war nicht fehlerfrei
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False  # kein Wert am Gerät
    run(rt, clock, 1)  # der nächste fehlerfreie Zyklus holt den Zähler nach
    assert plant.heartbeats() == [1, 2]


async def test_failures_of_an_aborted_cycle_do_not_count_for_the_next(
    runtime_env, monkeypatch
) -> None:
    plant = Plant(watchdog_state=1, accept=False)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 40)
    state |= {"system.active_in_source": 240}

    def broken(*args: object, **kwargs: object) -> list[Alarm]:
        raise RuntimeError("Testfehler nach den Befehlen")

    with monkeypatch.context() as patch:
        patch.setattr("bindaems.core.runtime.watchdog_alarms", broken)
        with pytest.raises(RuntimeError, match="nach den Befehlen"):
            rt.cycle_once()  # bei 40 s: Sicherwerte nicht gesendet, dann bricht der Zyklus ab
    rt.cycle_once()  # noch bei 40 s und ohne neuen Fehler: Der fällige Zähler geht hinaus
    assert plant.heartbeats() == [1, 2]


async def test_tesla_failures_do_not_stop_the_heartbeat(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(
        state=state,
        configure=lambda cfg: allow_tesla_live(allow_evcs_live(cfg)),
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )  # ohne Tessie-Token ist der Tesla nie steuerbar
    plant.attach(rt.store)
    live = {"wallbox.evcs": ActuatorMode.LIVE, "vehicle.tesla": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=1, control=True, actuators=live))
    run(rt, clock, 40)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 41)  # Tesla: Versuche bei 40, 50 und 60 s ungesendet, bei 70 s gescheitert
    tesla = [d.result for d in rt.decisions(T0, 100)[0] if d.actuator == "vehicle.tesla"]
    assert tesla == [DecisionResult.NOT_SENT] * 6  # Strom und Laden aus, je dreimal
    assert "actuator.failed.vehicle.tesla" in {a["id"] for a in rt.health().alarms}
    assert plant.heartbeats() == [1, 2, 3, 4, 5, 6]  # bei 30, 40, 50, 60, 70 und 80 s


# Zuständig für den Watchdog: Bedarf oder Victron/EVCS owned


async def test_core_never_responsible_leaves_a_tripped_watchdog_alone(runtime_env) -> None:
    plant = Plant(watchdog_state=2)  # ausgelöst von der Probe oder dem Teil-2-Werkzeug
    rt, _, clock = runtime_env(
        state=GOOD_STATE, mqtt_write=plant.write, before_cycle=plant.before_cycle
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True))  # Regelbetrieb, alle Aktoren im Dry-Run
    run(rt, clock, 70)
    assert plant.heartbeats() == []  # auch nach 60 s „ausgelöst“ keine Freigabe


async def test_restart_with_control_off_waits_for_the_watchdog_to_hand_back(runtime_env) -> None:
    first, _, _ = runtime_env(configure=allow_evcs_live)
    first.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    plant = Plant(watchdog_state=1, echo=1234)  # scharf, Zähler des früheren Laufs
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}  # Wert des früheren Laufs
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True  # gespeicherte Absicht: live
    run(rt, clock, 100)  # OBSERVE: Der core schreibt nicht, also kein Heartbeat und keine 0
    assert (rt.health().mode, plant.heartbeats()) == ("OBSERVE", [])
    plant.watchdog_state = 2  # der Watchdog löst aus und setzt den sicheren Strom
    state |= {"wallbox.evcs.gx_set_current_a": 6}
    run(rt, clock, 1)  # bei 100 s übergeben: ab diesem Zyklus kein Bedarf mehr
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    run(rt, clock, 59)
    assert plant.heartbeats() == []
    run(rt, clock, 1)  # bei 160 s seit 60 s ausgelöst, ohne Bedarf: Aufräumen mit der 0
    assert plant.heartbeats() == [0]


async def test_released_watchdog_armed_by_someone_else_stays_armed(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 33)
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus; die EVCS hält keine Werte des EMS
    run(rt, clock, 1)
    assert plant.heartbeats() == [1, 0]
    plant.echo = 1001  # die Probe macht den Watchdog mit eigenen Zählern scharf
    run(rt, clock, 30)
    assert plant.heartbeats() == [1, 0]  # keine erneute Freigabe


# Erwartungen der Erkennung fremder Änderungen

TESSIE_COMMAND = "https://api.tessie.com/5YJ3E7EB0MF000000/command"


async def test_tesla_current_is_watched_only_once_the_cache_shows_it(
    runtime_env, respx_mock
) -> None:
    for command in ("set_charging_amps", "stop_charging"):
        respx_mock.post(f"{TESSIE_COMMAND}/{command}").respond(200, json={"result": True})
    car = {"online": True, "at_home": True, "plugged": True, "charging_state": "Charging"}
    state = GOOD_STATE | {f"vehicle.tesla.{name}": value for name, value in car.items()}
    state |= {"vehicle.tesla.charge_amps": 16}
    rt, _, clock = runtime_env(state=state, configure=allow_tesla_live, tessie_token="t")
    rt.store.register_source("tessie:tesla", 900.0)
    rt.store.set_connected("tessie:tesla", True)
    rt.submit_intent(
        Intent(version=1, control=True, actuators={"vehicle.tesla": ActuatorMode.LIVE})
    )
    run(rt, clock, 31)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 1)  # SAFE bei 31 s: Strom 5 A und Laden aus an Tessie
    await asyncio.sleep(0.01)  # Tessie bestätigt beide
    state |= {"system.active_in_source": 1}  # Netz wieder da
    run(rt, clock, 30)  # der Cache zeigt noch 16 A: drei Prüfungen, aber noch keine Erwartung
    assert locked(rt)["vehicle.tesla"] == []
    state |= {"vehicle.tesla.charge_amps": 5}
    run(rt, clock, 1)  # der Cache zeigt 5 A: ab jetzt erwartet
    state |= {"vehicle.tesla.charge_amps": 10}
    run(rt, clock, 30)
    assert locked(rt)["vehicle.tesla"] == [
        "Tesla Model 3: Strom von außen geändert (10 A statt 5 A)."
    ]


async def test_a_timed_out_write_ends_the_watch(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt vorerst nichts
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 0}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    # an Stelle eines Werts der Regelung (2c), den die EVCS bestätigt hat
    rt.ownership.note_sent("wallbox.evcs")
    rt.foreign.expect("wallbox.evcs", CommandKind.CURRENT, 16.0)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 31)  # Sicherwert 6 A bei 33, 43 und 53 s, jedes Mal ohne Bestätigung
    state |= {"wallbox.evcs.gx_set_current_a": 6}  # die EVCS übernimmt ihn verspätet
    run(rt, clock, 40)  # vier Prüfungen: kein Vergleich mehr mit den 16 A von vorher
    assert "competitor.foreign.wallbox.evcs" not in {a["id"] for a in rt.health().alarms}
    assert locked(rt)["wallbox.evcs"] == []


async def test_a_write_that_was_not_sent_keeps_the_watch(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)  # SAFE: 6 A geschrieben und bestätigt, jetzt erwartet
    state |= {"wallbox.evcs.gx_set_current_a": 10}
    plant.follow = plant.accept = False  # von außen 10 A, und der Cerbo nimmt nichts mehr an
    run(rt, clock, 40)  # die Versuche mit 6 A bleiben ungesendet, die Erwartung bleibt
    assert locked(rt)["wallbox.evcs"] == ["EVCS „evcs“: Strom von außen geändert (10 A statt 6 A)."]
    assert "competitor.foreign.wallbox.evcs" in {a["id"] for a in rt.health().alarms}
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False  # gesperrt: nicht mehr owned


async def test_a_value_confirmed_after_a_lock_is_not_watched(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 39)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 1)  # SAFE bei 39 s: 6 A gesendet
    plant.follow = False  # die EVCS meldet den Wert erst bei 41 s
    state |= {"wallbox.evcs.gx_mode": 1}  # Prüfung bei 40 s: nicht manuell, gesperrt
    run(rt, clock, 1)
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    plant.follow = True
    run(rt, clock, 1)  # bestätigt, aber nicht mehr owned: keine Erwartung
    state |= {"wallbox.evcs.gx_set_current_a": 10}  # das andere System regelt die EVCS
    plant.follow = False
    run(rt, clock, 40)
    assert "competitor.foreign.wallbox.evcs" not in {a["id"] for a in rt.health().alarms}


# Übergabe: vom Watchdog vollendet, Tesla ohne Watchdog, Reihenfolge der Ziele


async def test_watchdog_safe_values_complete_a_failed_handback(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus: Übergabe
    run(rt, clock, 45)  # Versuche bei 33, 43 und 53 s; ab 63 s gescheitert, kein Zähler mehr
    sent = plant.heartbeats()
    assert rt.health().mode == "DEGRADED"
    plant.watchdog_state = 2  # der Watchdog löst aus und setzt den sicheren Strom
    state |= {"wallbox.evcs.gx_set_current_a": 6}
    run(rt, clock, 1)  # zurückgelesen: übergeben, also kein Zähler an ihn, sondern die Freigabe
    assert plant.heartbeats() == [*sent, 0]
    assert rt.health().mode == "OBSERVE"


async def test_failed_tesla_handback_stays_open_until_its_value_matches(runtime_env) -> None:
    # ohne Tessie-Token ist der Tesla nie steuerbar: Jeder Versuch bleibt ungesendet
    state = dict(GOOD_STATE)
    rt, _, clock = runtime_env(state=state, configure=allow_tesla_live)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)
    rt.ownership.note_sent("vehicle.tesla")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus: Übergabe des Tesla
    run(rt, clock, 120)  # Versuche bei 31, 41 und 51 s; ab 61 s gescheitert
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "DEGRADED",
        ["Regelbetrieb wird beendet: Übergabe der Aktoren läuft."],
    )
    failed = [a["message"] for a in health.alarms if a["id"] == "actuator.failed.vehicle.tesla"]
    assert failed == ["Tesla Model 3: Sicherwert für „current“ nicht bestätigt."]
    assert actuator_health(rt, "vehicle.tesla")["owned"] is True
    tesla = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "vehicle.tesla"]
    assert [d.result for d in tesla] == [DecisionResult.NOT_SENT] * 3
    state |= {"vehicle.tesla.charge_amps": 5}  # das Fahrzeug zeigt den sicheren Strom
    run(rt, clock, 1)  # übergeben
    health = rt.health()
    assert (health.mode, health.mode_reasons) == ("OBSERVE", ["Regelbetrieb ist aus."])
    assert "actuator.failed.vehicle.tesla" not in {a["id"] for a in health.alarms}
    assert actuator_health(rt, "vehicle.tesla")["owned"] is False


async def test_safe_state_targets_come_before_the_handback(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2, control=True))  # EVCS auf Dry-Run: Übergabe ...
    state |= {"system.active_in_source": 240}  # ... und zugleich Inselbetrieb: SAFE
    run(rt, clock, 2)
    current = [
        d
        for d in rt.decisions(T0, 100)[0]
        if (d.actuator, d.kind) == ("wallbox.evcs", CommandKind.CURRENT)
    ]
    assert [(d.result, d.codes[0]) for d in current] == [(DecisionResult.OK, "safe_state")]


# Uhr und Timeout des Watchdogs


async def test_heartbeat_runs_on_the_safety_clock(runtime_env) -> None:
    safety = ManualClock(T0 - timedelta(days=1))  # läuft anders als die Wanduhr
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE), safety_clock=safety)
    for _ in range(32):
        rt.cycle_once()
        clock.advance(1)
        safety.advance(1)
    assert plant.heartbeats() == [1]  # bei 30 s
    clock.advance(10)  # nur die Wanduhr: Auf der Uhr der Safety-Schicht ist kein Intervall um
    rt.cycle_once()
    assert plant.heartbeats() == [1]
    safety.advance(10)
    rt.cycle_once()
    assert plant.heartbeats() == [1, 2]


async def test_wrong_watchdog_timeout_raises_alarm_and_leaves_it_unarmed(runtime_env) -> None:
    def expect_60s(cfg: Config) -> Config:
        cfg = allow_evcs_live(cfg)
        watchdog = cfg.victron.watchdog.model_copy(update={"timeout_s": 60.0})
        return cfg.model_copy(
            update={"victron": cfg.victron.model_copy(update={"watchdog": watchdog})}
        )

    plant = Plant(watchdog_state=1)  # meldet 90 s
    rt, _, clock = runtime_env(
        state=GOOD_STATE,
        configure=expect_60s,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 40)  # ab 30 s wartet das Gate nur noch auf den Watchdog
    health = rt.health()
    assert [a["message"] for a in health.alarms if a["id"] == "watchdog.timeout"] == [
        "Watchdog-Timeout 90 s am Cerbo, erwartet 60 s; "
        "config.ini neu erzeugen und mit install.sh übertragen."
    ]
    # mit anderem Timeout macht der core ihn nicht scharf: kein Zähler, er löst aus
    assert (health.mode, plant.heartbeats()) == ("OBSERVE", [])
    assert actuator_health(rt, "wallbox.evcs")["effective"] == "dry_run"


async def test_unknown_watchdog_timeout_is_not_armed(runtime_env) -> None:
    plant = Plant(watchdog_state=1, timeout_s=None)  # scharf, aber ohne gemeldetes Timeout
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 40)
    assert (rt.health().mode, plant.heartbeats()) == ("OBSERVE", [])


async def test_unknown_watchdog_state_is_not_armed(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE | {"watchdog.timeout_s": 90},  # Timeout wie konfiguriert, kein Zustand
        configure=allow_evcs_live,
        watchdog_send=lambda n: sent.append(n) or True,
    )
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 40)
    assert (rt.health().mode, sent) == ("OBSERVE", [])
    assert "watchdog.unavailable" in {a["id"] for a in rt.health().alarms}


# Werte des EMS bei Sperren: Nur ein mitregelndes System nimmt sie dem core (Spec 7.9)

EVCS_MODE_UNKNOWN = "EVCS-Modus nicht verfügbar."


def stored_live_evcs(runtime_env, plant: Plant, state: dict[str, Any]) -> tuple[CoreRuntime, Any]:
    """Neustart mit gespeicherter Absicht „Regelbetrieb an, EVCS live“: Die EVCS ist owned."""
    first, _, _ = runtime_env(configure=allow_evcs_live)
    live = Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE})
    first.submit_intent(live)
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    return rt, clock


async def test_unreadable_checks_of_the_first_cycle_keep_the_values_of_the_last_run(
    runtime_env,
) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}  # 16 A vom letzten Lauf
    rt, clock = stored_live_evcs(runtime_env, plant, state)
    # Der erste Zyklus läuft wie in Produktion, bevor ein Adapter Daten geliefert hat: Der Aufruf
    # an der Klasse umgeht den Hook der Fixture, der vor jedem Zyklus die Messwerte einspeist.
    CoreRuntime.cycle_once(rt)
    clock.advance(1)
    # Die Prüfpunkte sind nicht lesbar und sperren; ein anderes System belegt das nicht.
    assert locked(rt)["wallbox.evcs"] == [EVCS_MODE_UNKNOWN]
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    run(rt, clock, 100)  # ab 1 s mit Daten: Zähler ab 31 s, die Sperre ist bei 70 s frei
    assert rt.health().mode == "CONTROL" and locked(rt)["wallbox.evcs"] == []
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    sent = len(plant.heartbeats())
    plant.follow = False  # die EVCS übernimmt den Sicherwert vorerst nicht
    rt.submit_intent(Intent(version=2, control=True))  # EVCS auf Dry-Run: Übergabe
    run(rt, clock, 5)
    assert ("evcharger/40/SetCurrent", 6) in plant.writes
    assert 0 not in plant.heartbeats()[sent:]  # keine Freigabe über den 16 A des letzten Laufs
    state["wallbox.evcs.gx_set_current_a"] = 6  # jetzt zeigt die EVCS den Sicherwert
    run(rt, clock, 1)
    assert plant.heartbeats()[-1] == 0  # erst nach dem Rücklesen
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False


async def test_a_check_that_turns_unknown_keeps_the_actuator_owned(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = stored_live_evcs(runtime_env, plant, state)
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    state["wallbox.evcs.gx_mode"] = None  # Modbus-Verlust: der Modus ist nicht lesbar
    run(rt, clock, 10)  # Prüfung bei 40 s
    assert locked(rt)["wallbox.evcs"] == [EVCS_MODE_UNKNOWN]
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"wallbox.evcs.gx_mode": 1}, "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."),
        (
            {"wallbox.evcs.gx_set_current_a": 10},
            "EVCS „evcs“: Strom von außen geändert (10 A statt 6 A).",
        ),
    ],
)
async def test_a_competing_system_still_takes_the_actuator_at_once(
    runtime_env, change: dict[str, int], reason: str
) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = stored_live_evcs(runtime_env, plant, state)
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)  # SAFE: 6 A geschrieben und bestätigt, jetzt erwartet
    state |= {"system.active_in_source": 1}
    plant.follow = False
    state |= change
    for _ in range(40):  # bis zur Prüfung, die das andere System erkennt
        run(rt, clock, 1)
        if locked(rt)["wallbox.evcs"]:
            break
    assert locked(rt)["wallbox.evcs"] == [reason]
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False  # im selben Zyklus


# Ein Strom unter dem Sicherwert bleibt stehen: Der sichere Zustand erhöht nie einen Strom


def evcs_safe_at_ten(cfg: Config) -> Config:
    """EVCS live freigegeben, Sicherwert 10 A (die Konfiguration erlaubt min_a ≤ safe_a)."""
    cfg = allow_evcs_live(cfg)
    evcs = cfg.wallboxes["evcs"].model_copy(update={"safe_a": 10})
    return cfg.model_copy(update={"wallboxes": cfg.wallboxes | {"evcs": evcs}})


async def test_a_current_below_the_safe_value_stays_in_island_operation(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 8, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(
        state=state,
        configure=evcs_safe_at_ten,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    sent = plant.heartbeats()
    state |= {"system.active_in_source": 240}  # Inselbetrieb: SAFE
    run(rt, clock, 60)
    assert rt.health().mode == "SAFE"
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    # nur „Laden aus“; die 8 A bleiben, keine Ablehnung „nicht Richtung sicherer Zustand“
    assert [(d.kind, d.result) for d in evcs] == [(CommandKind.CHARGING, DecisionResult.OK)]
    assert not any("urgent.ignored" in d.codes for d in rt.decisions(T0, 100)[0])
    assert ("evcharger/40/SetCurrent", 10) not in plant.writes
    assert not [a for a in rt.health().alarms if a["id"].startswith("actuator.failed.")]
    # Der Heartbeat läuft weiter (bei 40 bis 90 s): Der Watchdog wird nicht aufgegeben.
    assert plant.heartbeats() == [*sent, *range(sent[-1] + 1, sent[-1] + 7)]


# Wirksam live nur im Regelbetrieb, außer bei der Übergabe

VICTRON_PATHS = {
    "hub4/0/Overrides/Setpoint",
    "settings/0/Settings/CGwacs/MaxDischargePower",
    "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit",
}


def allow_all_live(cfg: Config) -> Config:
    cfg = allow_tesla_live(allow_evcs_live(cfg))
    return cfg.model_copy(update={"victron": cfg.victron.model_copy(update={"live_allowed": True})})


async def test_control_off_writes_only_the_handback_in_island_operation(runtime_env) -> None:
    plant = Plant(
        watchdog_state=1, follow=False
    )  # die EVCS übernimmt nichts: Übergabe bleibt offen
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(
        state=state,
        configure=allow_all_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    live = {"victron": ActuatorMode.LIVE, "wallbox.evcs": ActuatorMode.LIVE}
    live |= {"vehicle.tesla": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=1, control=True, actuators=live))
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2, control=False, actuators=live))  # Regelbetrieb aus
    run(rt, clock, 1)
    assert rt.health().mode == "DEGRADED"  # Übergabe der EVCS läuft
    state |= {"system.active_in_source": 240}  # Inselbetrieb
    run(rt, clock, 30)
    health = rt.health()
    assert (health.mode, health.mode_reasons[-1]) == (
        "SAFE",
        "Regelbetrieb wird beendet: Übergabe der Aktoren läuft.",
    )
    assert not any(path in VICTRON_PATHS for path, _ in plant.writes)  # kein MaxDischargePower
    assert actuator_health(rt, "victron")["owned"] is False  # keine neue Übergabe
    tesla = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "vehicle.tesla"]
    assert [(d.kind, d.result) for d in tesla] == [
        (CommandKind.CURRENT, DecisionResult.DRY_RUN),
        (CommandKind.CHARGING, DecisionResult.DRY_RUN),
    ]  # kein Live-Befehl, die Dry-Run-Entscheidung einmal je Vorfall
    for actuator in ("victron", "vehicle.tesla"):
        mode = actuator_health(rt, actuator)
        assert (mode["requested"], mode["effective"], mode["reasons"]) == (
            "live",
            "dry_run",
            ["Regelbetrieb ist aus."],
        )
    evcs = actuator_health(rt, "wallbox.evcs")
    assert (evcs["effective"], evcs["reasons"]) == (
        "live",
        ["Übergabe: Sicherwerte werden gesetzt."],
    )
    assert ("evcharger/40/SetCurrent", 6) in plant.writes  # geschrieben wird nur die Übergabe
