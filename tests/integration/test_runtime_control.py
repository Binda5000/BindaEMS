"""Laufzeit mit Betriebszuständen, Absicht, Sperren, Eingangsprüfung und Heartbeat (Plan 2a)."""

from typing import Any

import pytest
from structlog.testing import capture_logs
from tests.helpers import run_until
from tests.unit.core.adapters.conftest import FakeTransport, P

from bindaems.core.adapters.victron_mqtt import VictronMqttAdapter
from bindaems.core.control.intent import INTENT_FILE_PROBLEM
from bindaems.shared.config import Config
from bindaems.shared.control import ActuatorMode, Intent
from bindaems.shared.domain import Alarm, SignalKind

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
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 2)
    rt.submit_intent(Intent(version=2))
    run(rt, clock, 1)
    assert (sent, rt.health().watchdog["heartbeat"]) == ([1, 0], "released")


async def test_shutdown_sends_no_release(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(
        state=GOOD_STATE, configure=allow_evcs_live, watchdog_send=lambda n: sent.append(n) or True
    )
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
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
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
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
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))

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
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
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
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 1)
    watchdog = rt.health().watchdog
    assert (watchdog["required"], watchdog["heartbeat"]) == (True, "off")
