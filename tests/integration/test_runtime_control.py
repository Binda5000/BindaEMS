"""Laufzeit mit Betriebszuständen, Absicht, Sperren, Eingangsprüfung und Heartbeat (Plan 2a),
dazu Aktoren, sicherer Zustand und Entscheidungslog (Plan 2b)."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
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
from bindaems.core.control.actuator import Confirmation
from bindaems.core.control.intent import INTENT_FILE_PROBLEM
from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.runtime import CoreRuntime
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


async def test_heartbeat_only_with_live_watchdog_actuator(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE, configure=allow_evcs_live, watchdog_send=lambda n: sent.append(n) or True
    )
    run(rt, clock, 15)
    assert sent == []
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 21)
    assert sent == [1, 2, 3]
    health = rt.health()
    assert (health.mode, health.watchdog["heartbeat"]) == ("OBSERVE", "active")
    assert "Watchdog am Cerbo ist nicht scharf." in health.mode_reasons
    rt.store.update("watchdog.state", 1, source="victron", kind=SignalKind.STATE)
    run(rt, clock, 1)
    assert rt.health().mode == "CONTROL"


async def test_release_when_live_dropped(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE, configure=allow_evcs_live, watchdog_send=lambda n: sent.append(n) or True
    )
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 2)
    rt.submit_intent(Intent(version=2))
    run(rt, clock, 1)
    assert (sent, rt.health().watchdog["heartbeat"]) == ([1, 0], "released")


async def test_shutdown_sends_no_release(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE, configure=allow_evcs_live, watchdog_send=lambda n: sent.append(n) or True
    )
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 2)
    await rt.shutdown()
    assert sent == [1]


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
    }
    assert health.watchdog == {
        "state": "unknown",
        "trip_count": None,
        "last_trip": None,
        "last_trip_failures": None,
        "version": None,
        "expected_version": "1.0.0",
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
    "watchdog.version": "1.0.0",
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
    rt, _, clock = runtime_env(
        state=GOOD_STATE | WATCHDOG_ARMED,
        configure=allow_evcs_live,
        watchdog_send=lambda n: True,
    )
    rt.submit_intent(Intent(version=2, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 1)
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
    }
    assert health.watchdog == {
        "state": "armed",
        "trip_count": 2,
        "last_trip": "2025-10-09T08:53:20+00:00",
        "last_trip_failures": 0,
        "version": "1.0.0",
        "expected_version": "1.0.0",
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
    rt, _, _ = runtime_env(
        configure=allow_evcs_live, watchdog_send=lambda n: sent.append(n) or True
    )
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))

    def broken(*args: object) -> list[Alarm]:
        raise RuntimeError("Testfehler beim Aktualisieren der Alarme")

    with monkeypatch.context() as patch:
        patch.setattr("bindaems.core.runtime.watchdog_alarms", broken)
        with pytest.raises(RuntimeError):
            rt.cycle_once()
    assert sent == []
    rt.cycle_once()
    assert sent == [1]


async def test_heartbeat_goes_through_the_victron_adapter(runtime_env, cfg) -> None:
    rt, _, _ = runtime_env(state=GOOD_STATE, configure=allow_evcs_live)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
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
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_evcs_live)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 1)
    watchdog = rt.health().watchdog
    assert (watchdog["required"], watchdog["heartbeat"]) == (True, "off")


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
    run(rt, clock, 33)  # Heartbeats bei 0, 10, 20 und 30 s; die Uhr steht auf 33 s
    sent = plant.heartbeats()
    state |= {"system.active_in_source": 240}
    clock.advance(7)  # bei 40 s ist der nächste Heartbeat fällig
    with pytest.raises(RuntimeError, match="Testfehler im Aktor"):
        rt.cycle_once()
    assert plant.heartbeats() == sent == [1, 2, 3, 4]  # kein Heartbeat aus diesem Zyklus
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
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
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
