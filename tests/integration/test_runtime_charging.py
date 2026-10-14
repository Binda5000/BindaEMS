"""Laufzeit mit der Laderegelung im Regelzyklus (Plan 2c-1): Entscheidungen der Regelung im
Dry-Run und live, Neustart während des Ladens, Übergabe, Health je Fahrzeug und der gemeinsame
HTTP-Client, dazu die geparkten Befunde aus 2b (Design 10): Freigabe des Watchdogs, Aufräumen des
Entscheidungslogs, Heartbeat-Zähler, sicherer Zustand mit dem Tesla und Auslösung des Watchdogs."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from tests.helpers import T0
from tests.integration.conftest import GOOD_STATE, allow_evcs_live, allow_tesla_live, live_evcs, run
from tests.integration.plant import Plant

from bindaems.core.control.charging import ChargeController
from bindaems.shared.control import ActuatorMode, ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.domain import SignalKind

# Überschuss 5000 W: 4500 W Einspeisung + 600 W Laden des Akkus (SOC 50 % ≥ 30 %) − 100 W Marge
SURPLUS = {**{f"grid.l{n}.power_w": -1500.0 for n in (1, 2, 3)}, "grid.power_w": -4500.0}
# Der e-Golf an der EVCS: angesteckt, lädt nicht; evcc hält 6 A und „Laden aus“
EGOLF_PLUGGED = GOOD_STATE | {
    "wallbox.evcs.status": 1,
    "wallbox.evcs.gx_set_current_a": 6,
    "wallbox.evcs.gx_start_stop": 0,
    "wallbox.evcs.gx_current_a": 0,
}
# Gründe des sicheren Zustands und der Übergabe: Diese Entscheidungen stammen vom Keeper.
KEEPER_CODES = frozenset({"safe_state", "handback", "grid_outage"})


def charging_decisions(rt) -> list[Decision]:
    """Die Entscheidungen der Laderegelung, ohne die des sicheren Zustands und der Übergabe."""
    decisions, _ = rt.decisions(T0 - timedelta(days=1), 1000)
    return [d for d in decisions if not set(d.codes) & KEEPER_CODES]


def actuator_health(rt, actuator: str) -> dict[str, Any]:
    return next(a for a in rt.health().actuators if a["id"] == actuator)


async def test_dry_run_decides_only_its_own_changes(runtime_env) -> None:
    state = dict(EGOLF_PLUGGED)
    rt, _, clock = runtime_env(state=state, extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32 + 125)  # CONTROL ab 30 s, Start nach 120 s mit genug Überschuss
    found = [(d.actuator, d.kind, d.requested, d.result, d.codes) for d in charging_decisions(rt)]
    assert found == [
        (
            "wallbox.evcs",
            CommandKind.CURRENT,
            10.0,
            DecisionResult.DRY_RUN,
            ("mode_pv", "pv_surplus"),
        ),
        (
            "wallbox.evcs",
            CommandKind.CHARGING,
            True,
            DecisionResult.DRY_RUN,
            ("mode_pv", "pv_surplus"),
        ),
    ]
    # ein fremder Regler verstellt den Sollstrom: keine weitere Entscheidung
    state["wallbox.evcs.gx_set_current_a"] = 14
    run(rt, clock, 30)
    assert len(charging_decisions(rt)) == 2


async def test_decision_inputs_carry_surplus_and_actual_value(runtime_env) -> None:
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED, extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32 + 125)
    current = next(d for d in charging_decisions(rt) if d.kind == CommandKind.CURRENT)
    inputs = dict(current.inputs)
    limit = inputs.pop("limit_a")
    assert limit is not None and limit > 0
    assert inputs == {
        "soc_pct": 50.0,
        "battery_w": 600.0,
        "grid_l1_a": pytest.approx(-1500.0 / 230.0),
        "grid_l2_a": pytest.approx(-1500.0 / 230.0),
        "grid_l3_a": pytest.approx(-1500.0 / 230.0),
        "surplus_w": 5000.0,
        "actual_current_a": 6.0,  # Ist-Werte von evcc
        "actual_charging": 0.0,
    }


async def test_live_evcs_follows_the_surplus(runtime_env) -> None:
    plant = Plant(watchdog_state=1)  # scharf für diesen core, sobald er sein Echo zeigt
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32)  # Zähler 1 bei 30 s, sein Echo bei 31 s: scharf, CONTROL
    assert rt.health().mode == "CONTROL"
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    run(rt, clock, 120)  # Start 120 s nach dem ersten Regelzyklus
    writes = [write for write in plant.writes if write[0].startswith("evcharger/")]
    assert writes == [("evcharger/40/SetCurrent", 10), ("evcharger/40/StartStop", 1)]
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True  # wie nach einem Sicherwert
    run(rt, clock, 1)
    found = [(d.kind, d.result) for d in charging_decisions(rt)]
    assert found == [
        (CommandKind.CURRENT, DecisionResult.OK),
        (CommandKind.CHARGING, DecisionResult.OK),
    ]


def evcs_writes(plant: Plant, since: int = 0) -> list[tuple[str, float | int | None]]:
    """Die Schreibaufträge an die EVCS ab dem Auftrag Nummer ``since``."""
    return [write for write in plant.writes[since:] if write[0].startswith("evcharger/")]


START = [("evcharger/40/SetCurrent", 10), ("evcharger/40/StartStop", 1)]


async def test_a_timed_out_charging_command_is_offered_again(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32 + 120)  # Start bei 151 s
    assert evcs_writes(plant) == START
    run(rt, clock, 10)  # bei 161 s nicht bestätigt: Die Regelung vergisst ihre Werte
    assert evcs_writes(plant) == START * 2


async def test_a_refused_charging_command_is_offered_again(runtime_env) -> None:
    plant = Plant(watchdog_state=1, accept=False)  # der Cerbo nimmt keinen EVCS-Auftrag an
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32 + 120)  # Start bei 151 s, nicht gesendet
    assert evcs_writes(plant) == START
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    run(rt, clock, 5)  # nach dem Mindestabstand von 5 s wieder angeboten
    assert evcs_writes(plant) == START * 2


async def test_arming_sends_what_the_dry_run_decided(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    rt, _, clock = runtime_env(
        state=dict(EGOLF_PLUGGED),
        extra=SURPLUS,
        configure=allow_evcs_live,
        mqtt_write=plant.write,
        before_cycle=plant.before_cycle,
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True))  # die EVCS im Dry-Run, neben evcc
    run(rt, clock, 32 + 125)  # Start bei 150 s
    found = [(d.kind, d.requested, d.result) for d in charging_decisions(rt)]
    assert found == [
        (CommandKind.CURRENT, 10.0, DecisionResult.DRY_RUN),
        (CommandKind.CHARGING, True, DecisionResult.DRY_RUN),
    ]
    live = {"wallbox.evcs": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=2, control=True, actuators=live))
    run(rt, clock, 2)  # Zähler 1, dann sein Echo: scharf, die EVCS ist wirksam live
    assert actuator_health(rt, "wallbox.evcs")["effective"] == "live"
    assert evcs_writes(plant) == START  # die Werte des Dry-Run gelten nicht als gesendet


async def test_the_safety_context_knows_the_wallbox_of_each_vehicle(
    runtime_env, monkeypatch
) -> None:
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED, extra=SURPLUS)
    seen: list[dict[str, str | None]] = []
    execute = rt.executor.execute

    def spy(command, ctx, *args):
        seen.append(dict(ctx.vehicle_wallbox))
        return execute(command, ctx, *args)

    monkeypatch.setattr(rt.executor, "execute", spy)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 31)  # erster Regelzyklus bei 30 s: „Laden aus“, die EVCS hat es schon
    assert seen  # der e-Golf hat keinen eigenen Aktor, der Tesla hängt am Wall Connector
    assert all(entry == {"vehicle.tesla": "twc"} for entry in seen)


async def test_a_sleeping_tesla_gets_a_wake_start(runtime_env) -> None:
    car = {"online": False, "at_home": True, "plugged": True, "charging_state": "Stopped"}
    # Wall Connector und EVCS melden ihre Ströme (0 A): Die Grenze ist bekannt und trägt den
    # Mindeststrom (frei 33 − 4,3 A). Ohne sie gälten die Phasen als voll, und der Start
    # unterbliebe (Design 5.5).
    wallboxes = {"wallbox.twc.vehicle_connected": True, "wallbox.evcs.current_a": 0.0} | {
        f"wallbox.twc.l{n}.current_a": 0.0 for n in (1, 2, 3)
    }
    state = GOOD_STATE | wallboxes | {f"vehicle.tesla.{name}": v for name, v in car.items()}
    rt, _, clock = runtime_env(state=state, tessie_token="t")
    rt.store.register_source("tessie:tesla", 900.0)
    rt.store.set_connected("tessie:tesla", True)
    now = {"tesla": VehicleIntent(mode=ChargeMode.NOW)}
    rt.submit_intent(Intent(version=1, control=True, vehicles=now))
    run(rt, clock, 31)
    assert actuator_health(rt, "vehicle.tesla")["controllable"] is False  # es schläft
    found = [(d.actuator, d.kind, d.requested, d.result, d.codes) for d in charging_decisions(rt)]
    assert found == [
        (
            "vehicle.tesla",
            CommandKind.CHARGING,
            True,
            DecisionResult.DRY_RUN,
            ("mode_now", "wake"),
        )
    ]
    assert rt.health().vehicles[0]["wakes_today"] == 1  # zählt auch im Dry-Run


async def test_a_wake_start_deferred_by_the_hourly_limit_is_logged(runtime_env, tmp_path) -> None:
    # Spec 7.10, Stand Plan 2c-1: Die 6 Start/Stopp der letzten Stunde sind verbraucht; die Datei
    # der Rate-Limits bringt sie wie nach einem Neustart mit. Der Weckstart wird zurückgestellt: Das
    # steht im Log (dieselbe Zurückstellung höchstens einmal je Minute), die Regelung bietet ihn in
    # jedem Zyklus wieder an, und das Weckkontingent zählt ihn nicht.
    entries = [
        {
            "actuator": "vehicle.tesla",
            "kind": "charging",
            "ts": (T0 - timedelta(minutes=50 - n)).isoformat(),
        }
        for n in range(6)
    ]
    data_dir = tmp_path / "core"
    data_dir.mkdir()
    rates = {"version": 1, "entries": entries}
    (data_dir / "rate_limits.json").write_text(json.dumps(rates), encoding="utf-8")
    car = {"online": False, "at_home": True, "plugged": True, "charging_state": "Stopped"}
    wallboxes = {"wallbox.twc.vehicle_connected": True, "wallbox.evcs.current_a": 0.0} | {
        f"wallbox.twc.l{n}.current_a": 0.0 for n in (1, 2, 3)
    }
    state = GOOD_STATE | wallboxes | {f"vehicle.tesla.{name}": v for name, v in car.items()}
    rt, _, clock = runtime_env(state=state, tessie_token="t")
    rt.store.register_source("tessie:tesla", 900.0)
    rt.store.set_connected("tessie:tesla", True)
    now = {"tesla": VehicleIntent(mode=ChargeMode.NOW)}
    rt.submit_intent(Intent(version=1, control=True, vehicles=now))
    run(rt, clock, 31)
    found = [(d.actuator, d.kind, d.requested, d.result, d.codes) for d in charging_decisions(rt)]
    assert found == [
        (
            "vehicle.tesla",
            CommandKind.CHARGING,
            True,
            DecisionResult.DEFERRED,
            ("mode_now", "wake", "rate.start_stop"),
        )
    ]
    assert charging_decisions(rt)[0].text == (
        "Tesla Model 3 (Sofort, Wecken): Laden an – zurückgestellt. "
        "Höchstens 6 Start/Stopp pro Stunde erreicht."
    )
    run(rt, clock, 60)
    assert [d.result for d in charging_decisions(rt)] == [DecisionResult.DEFERRED] * 2
    assert rt.health().vehicles[0]["wakes_today"] == 0


async def test_restart_while_charging_does_not_stop(runtime_env) -> None:  # Review Focus 1
    charging = {"wallbox.evcs.status": 2, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED | charging)  # ohne Überschuss
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32 + 290)  # CONTROL ab 30 s: noch keine 300 s ohne Überschuss
    stops = [
        d for d in charging_decisions(rt) if d.kind == CommandKind.CHARGING and d.requested is False
    ]
    assert stops == []
    run(rt, clock, 20)
    found = [d.codes for d in charging_decisions(rt) if d.kind == CommandKind.CHARGING]
    assert found == [("mode_pv", "pv_surplus")]


async def test_no_charging_commands_without_control(runtime_env) -> None:
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED, extra=SURPLUS)
    rt.submit_intent(Intent(version=1))  # Regelbetrieb aus
    run(rt, clock, 32 + 125)
    assert rt.health().mode == "OBSERVE"
    assert charging_decisions(rt) == []
    assert [v["mode"] for v in rt.health().vehicles] == [None, None]  # keine Regelung


async def test_no_charging_while_control_off_hands_back(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s, bestätigt bei 152 s: Die EVCS hält Werte des EMS
    assert rt.health().vehicles[1]["mode"] == "pv"
    # Die EVCS bleibt bei 10 A und übernimmt den Sicherwert nicht: Die Übergabe bleibt offen.
    state |= {"wallbox.evcs.gx_set_current_a": 10, "wallbox.evcs.gx_start_stop": 1}
    plant.follow = False
    rt.submit_intent(Intent(version=2))  # Regelbetrieb aus
    run(rt, clock, 5)
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "DEGRADED",
        ["Regelbetrieb wird beendet: Übergabe der Aktoren läuft."],
    )
    assert [v["mode"] for v in health.vehicles] == [None, None]  # keine Regelung


async def test_handback_gets_no_charging_commands(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s, bestätigt bei 152 s: Die EVCS hält Werte des EMS
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    # Die EVCS bleibt bei 10 A und übernimmt den Sicherwert nicht: Die Übergabe bleibt offen.
    state |= {"wallbox.evcs.gx_set_current_a": 10, "wallbox.evcs.gx_start_stop": 1}
    plant.follow = False
    since = clock.now() - timedelta(microseconds=1)
    off = {"egolf": VehicleIntent(mode=ChargeMode.OFF)}  # die Regelung will „Laden aus“
    rt.submit_intent(Intent(version=2, control=True, vehicles=off))  # EVCS im Dry-Run
    run(rt, clock, 40)  # Sicherwert bei 0, 10 und 20 s, jedes Mal ohne Bestätigung
    during = rt.decisions(since, 1000)[0]
    assert [(d.kind, d.result, d.codes[0]) for d in during] == [
        (CommandKind.CURRENT, DecisionResult.TIMEOUT, "handback"),
    ] * 3
    state["wallbox.evcs.gx_set_current_a"] = 6  # jetzt zeigt die EVCS den Sicherwert: übergeben
    since = clock.now() - timedelta(microseconds=1)
    run(rt, clock, 1)
    after = [(d.kind, d.requested, d.result, d.codes) for d in rt.decisions(since, 1000)[0]]
    assert after == [(CommandKind.CHARGING, False, DecisionResult.DRY_RUN, ("mode_off",))]


async def test_after_the_handback_the_dry_run_decides_the_wish_again(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s mit 10 A und „Laden an“, bestätigt bei 152 s
    since = clock.now() - timedelta(microseconds=1)
    rt.submit_intent(Intent(version=2, control=True))  # EVCS im Dry-Run: Übergabe
    run(rt, clock, 6)  # 6 A bei 153 s, zurückgelesen bei 154 s; Mindestabstand 5 s
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    found = [
        (d.kind, d.requested, d.result, d.codes)
        for d in rt.decisions(since, 1000)[0]
        if "handback" not in d.codes
    ]  # „Laden an“ hat die EVCS schon: UNCHANGED
    assert found == [(CommandKind.CURRENT, 10.0, DecisionResult.DRY_RUN, ("mode_pv", "pv_surplus"))]


async def test_rearming_during_a_handback_sends_the_wish_again(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s mit 10 A, bestätigt bei 152 s
    state |= {"wallbox.evcs.gx_set_current_a": 10, "wallbox.evcs.gx_start_stop": 1}
    plant.follow = False  # die EVCS übernimmt den Sicherwert erst später
    rt.submit_intent(Intent(version=2, control=True))  # EVCS im Dry-Run: Übergabe, 6 A offen
    run(rt, clock, 1)
    live = {"wallbox.evcs": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=3, control=True, actuators=live))  # wieder live, noch offen
    run(rt, clock, 1)
    state["wallbox.evcs.gx_set_current_a"] = 6  # die EVCS zeigt den Sicherwert des Keepers
    mark = len(plant.writes)
    run(rt, clock, 5)  # Mindestabstand 5 s seit den 6 A
    assert evcs_writes(plant, mark) == [("evcharger/40/SetCurrent", 10)]


async def test_after_a_foreign_takeover_the_regulation_sends_again(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s mit 10 A, bestätigt bei 152 s: ab jetzt erwartet
    state |= {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 1}
    plant.follow = False  # ein anderes System stellt 16 A ein
    mark = len(plant.writes)
    run(rt, clock, 28)  # die dritte Prüfung bei 180 s erkennt es: gesperrt, nicht mehr owned
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False
    run(rt, clock, 120)  # die Sperre fällt bei 300 s
    assert evcs_writes(plant, mark) == [("evcharger/40/SetCurrent", 10)]


async def test_an_error_in_the_regulation_costs_the_keeper_no_attempt(
    runtime_env, monkeypatch
) -> None:
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s, bestätigt bei 152 s: Die EVCS hält Werte des EMS

    def broken(self, inputs):
        raise RuntimeError("Testfehler in der Regelung")

    rt.submit_intent(Intent(version=2, control=True))  # EVCS im Dry-Run: Übergabe
    with monkeypatch.context() as patch:
        patch.setattr(ChargeController, "step", broken)
        for _ in range(35):  # länger als die drei Versuche des Keepers
            with pytest.raises(RuntimeError, match="in der Regelung"):
                rt.cycle_once()
            clock.advance(1)
    assert evcs_writes(plant) == START  # kein Sicherwert, solange der Zyklus scheitert
    run(rt, clock, 2)  # die Regelung geht wieder: 6 A gesendet, dann zurückgelesen
    assert evcs_writes(plant) == [*START, ("evcharger/40/SetCurrent", 6)]
    assert not rt.keeper.failed
    assert not [a for a in rt.health().alarms if a["id"].startswith("actuator.failed.")]
    assert actuator_health(rt, "wallbox.evcs")["owned"] is False  # übergeben


async def test_health_lists_vehicles(runtime_env) -> None:
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED, extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 1)  # OBSERVE: noch keine Regelung
    assert rt.health().vehicles[1] == {
        "id": "egolf",
        "name": "e-Golf",
        "mode": None,
        "boost_until": None,
        "wallbox": None,
        "assignment": None,
        "limit_a": None,
        "plugged": None,
        "charging": None,
        "wakes_today": 0,
        "reason": None,
    }
    run(rt, clock, 31)
    vehicles = rt.health().vehicles
    assert rt.health().mode == "CONTROL"
    assert [v["id"] for v in vehicles] == ["tesla", "egolf"]
    assert vehicles[1] == {
        "id": "egolf",
        "name": "e-Golf",
        "mode": "pv",
        "boost_until": None,
        "wallbox": "evcs",
        "assignment": "default",
        "limit_a": pytest.approx(19.0 + 1500.0 / 230.0),  # Importziel 19 A, 6,5 A Einspeisung
        "plugged": True,
        "charging": False,
        "wakes_today": 0,
        "reason": "mode_pv",  # „Laden aus“ beim Übernehmen, die EVCS hat es schon: UNCHANGED
    }
    assert (vehicles[0]["wallbox"], vehicles[0]["limit_a"]) == ("twc", None)  # nicht geregelt


async def test_a_given_http_client_is_the_shared_one(runtime_env) -> None:
    requests: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    rt, _, _ = runtime_env(http_client=client, tessie_token="t")
    assert rt.actuators["vehicle.tesla"].send(CommandKind.CURRENT, 10.0) is None
    await asyncio.wait(asyncio.all_tasks() - {asyncio.current_task()})  # der Befehl im Hintergrund
    assert requests == ["/5YJ3E7EB0MF000000/command/set_charging_amps"]
    await rt.shutdown()
    assert client.is_closed  # der gemeinsame Client schließt mit der Laufzeit


# --- Geparkte Befunde aus 2b (Design 10) -------------------------------------------------------


def hand_over_with_an_open_write(runtime_env, monkeypatch, key: tuple[str, CommandKind]):
    """Regelbetrieb aus, die EVCS ist übergeben, aber für ``key`` meldet der Ausführer 20 s lang
    einen offenen Schreibvorgang. Zurück kommen die Anlage, die Laufzeit und die Uhr.

    Wie ``test_control_off_hands_back_then_observe`` (2b), nur mit der Regelung: Sie senkt die EVCS
    bei 31 s auf 6 A und hält so Werte des EMS. Mit der neuen Absicht zeigt die EVCS den Sicherwert
    schon, die Übergabe ist im ersten Zyklus fertig, und nur der offene Schreibvorgang kann die
    Freigabe noch aufhalten.
    """
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 36)
    assert actuator_health(rt, "wallbox.evcs")["owned"] is True
    open_until = clock.now() + timedelta(seconds=20)
    real = rt.executor.pending

    def pending() -> frozenset[tuple[str, CommandKind]]:
        return real() | {key} if clock.now() < open_until else real()

    monkeypatch.setattr(rt.executor, "pending", pending)
    live = {"wallbox.evcs": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=2, control=False, actuators=live))  # Regelbetrieb aus
    return plant, rt, clock


async def test_release_waits_for_pending_writes(runtime_env, monkeypatch) -> None:
    key = ("wallbox.evcs", CommandKind.CURRENT)
    plant, rt, clock = hand_over_with_an_open_write(runtime_env, monkeypatch, key)
    run(rt, clock, 19)  # bei 0 bis 18 s nach der Übergabe
    evcs = actuator_health(rt, "wallbox.evcs")
    assert evcs["owned"] is False  # übergeben, die EVCS hält keine Werte des EMS mehr
    assert evcs["pending"] == ["current"]  # aber ein Schreibvorgang ist offen
    assert plant.heartbeats() == [1]  # keine Freigabe
    assert rt.heartbeat.recent == (1,)
    run(rt, clock, 3)  # ab 20 s ist er nicht mehr offen
    assert plant.heartbeats() == [1, 0]
    assert rt.health().watchdog["heartbeat"] == "released"
    run(rt, clock, 1)  # die Anlage meldet das Echo 0: Die Freigabe ist bestätigt
    assert rt.heartbeat.recent == ()  # der core vergisst seine Zähler


@pytest.mark.parametrize(
    "key", [("wallbox.evcs", CommandKind.CHARGING), ("victron", CommandKind.GRID_SETPOINT)]
)
async def test_release_waits_for_every_write_of_a_watchdog_actuator(
    runtime_env, monkeypatch, key
) -> None:
    plant, rt, clock = hand_over_with_an_open_write(runtime_env, monkeypatch, key)
    run(rt, clock, 19)
    assert plant.heartbeats() == [1]  # auch wenn es nicht die Art ist, die übergeben wurde
    run(rt, clock, 3)
    assert plant.heartbeats() == [1, 0]


async def test_release_does_not_wait_for_a_tesla_write(runtime_env, monkeypatch) -> None:
    key = ("vehicle.tesla", CommandKind.CURRENT)  # der Watchdog schützt den Tesla nicht
    plant, rt, clock = hand_over_with_an_open_write(runtime_env, monkeypatch, key)
    run(rt, clock, 2)
    assert actuator_health(rt, "vehicle.tesla")["pending"] == ["current"]
    assert plant.heartbeats() == [1, 0]


async def test_release_waits_for_a_write_the_evcs_has_not_taken(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(EGOLF_PLUGGED), extra=SURPLUS)
    run(rt, clock, 32 + 120)  # Start bei 151 s: 10 A und „Laden an“ gesendet, nicht übernommen
    assert evcs_writes(plant) == START
    live = {"wallbox.evcs": ActuatorMode.LIVE}
    rt.submit_intent(Intent(version=2, control=False, actuators=live))  # Regelbetrieb aus
    run(rt, clock, 5)  # die EVCS zeigt den Sicherstrom 6 A: übergeben, beide Schreibvorgänge offen
    evcs = actuator_health(rt, "wallbox.evcs")
    assert (evcs["owned"], evcs["pending"]) == (False, ["current", "charging"])
    assert 0 not in plant.heartbeats()  # keine Freigabe, solange 10 A noch ankommen könnten
    run(rt, clock, 10)  # nach 10 s ohne Rücklesen enden sie mit TIMEOUT
    assert actuator_health(rt, "wallbox.evcs")["pending"] == []
    assert plant.heartbeats()[-1] == 0


@pytest.mark.parametrize("step", ["_control", "_record_cycle"])  # vor und nach der früheren Stelle
async def test_prune_runs_after_a_failed_cycle(runtime_env, monkeypatch, step) -> None:
    rt, _, clock = runtime_env()
    pruned: list[datetime] = []
    monkeypatch.setattr(rt.decision_spool, "prune", pruned.append)
    monkeypatch.setattr(rt, step, lambda *a: (_ for _ in ()).throw(RuntimeError("Test")))
    with pytest.raises(RuntimeError):
        rt.cycle_once()
    assert pruned == [clock.now()]  # nach der Wanduhr, im ersten Zyklus


async def test_default_heartbeat_starts_with_the_run_value(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    rt, clock = live_evcs(
        runtime_env,
        plant=plant,
        state=dict(GOOD_STATE),
        start="2026-10-12T12:00:00+00:00",
        heartbeat_first=None,  # ohne Angabe: der Startwert des Laufs
    )
    run(rt, clock, 32)  # Zähler bei 30 s, sein Echo bei 31 s: scharf, CONTROL
    assert plant.heartbeats() == [792_806_400]
    assert rt.health().mode == "CONTROL"  # der Watchdog zeigt den Zähler als den eigenen
    run(rt, clock, 10)
    assert plant.heartbeats() == [792_806_400, 792_806_401]


def safe_tesla(runtime_env, car: dict[str, bool], *, tessie_stale: bool = False):
    """Der live geschaltete Tesla im sicheren Zustand; ``car`` sind seine Werte von Tessie.

    Tessie ist verbunden, die Werte stammen von dort (``vehicle.tesla.<name>``). Bei 32 s steht
    der core in CONTROL, dann veralten die Cerbo-Daten (und mit ``tessie_stale`` auch die von
    Tessie). 45 s SAFE umfassen die drei Versuche des Keepers im Abstand von 10 s und den Alarm.
    Zurück kommen die Laufzeit und die Entscheidungen des sicheren Zustands.
    """
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_tesla_live, tessie_token="t")
    rt.store.register_source("tessie:tesla", 900.0)
    rt.store.set_connected("tessie:tesla", True)
    for name, value in car.items():
        rt.store.update(
            f"vehicle.tesla.{name}", value, source="tessie:tesla", kind=SignalKind.STATE
        )
    rt.submit_intent(
        Intent(version=1, control=True, actuators={"vehicle.tesla": ActuatorMode.LIVE})
    )
    run(rt, clock, 32)
    assert rt.health().mode == "CONTROL"
    since = clock.now() - timedelta(microseconds=1)
    rt.store.set_connected("victron", False)  # Cerbo-Daten veraltet
    if tessie_stale:
        rt.store.set_connected("tessie:tesla", False)
    run(rt, clock, 45)
    assert rt.health().mode == "SAFE"
    return rt, rt.decisions(since, 1000)[0]


def alarm_ids(rt) -> set[str]:
    return {alarm["id"] for alarm in rt.health().alarms}


async def test_safe_state_skips_a_sleeping_tesla(runtime_env) -> None:
    rt, decisions = safe_tesla(runtime_env, {"online": False, "at_home": True, "plugged": True})
    # Victron und EVCS bekommen ihre Sicherwerte (im Dry-Run); der Tesla lädt nicht: keine Versuche,
    # die nicht gesendet werden, und kein Alarm
    assert {d.actuator for d in decisions} == {"victron", "wallbox.evcs"}
    assert "actuator.failed.vehicle.tesla" not in alarm_ids(rt)


@pytest.mark.parametrize("signal", ["at_home", "plugged"])
async def test_safe_state_skips_an_absent_or_unplugged_tesla(runtime_env, signal) -> None:
    car = {"online": True, "at_home": True, "plugged": True} | {signal: False}
    rt, decisions = safe_tesla(runtime_env, car)
    assert {d.actuator for d in decisions} == {"victron", "wallbox.evcs"}
    assert "actuator.failed.vehicle.tesla" not in alarm_ids(rt)


@pytest.mark.parametrize(
    ("car", "tessie_stale"),
    [
        ({}, False),  # Tessie hat noch nichts gemeldet
        ({"online": False, "at_home": True, "plugged": True}, True),  # der Wert ist veraltet
    ],
    ids=["unknown", "stale"],
)
async def test_safe_state_keeps_a_tesla_whose_state_is_not_known(
    runtime_env, car, tessie_stale
) -> None:
    rt, decisions = safe_tesla(runtime_env, car, tessie_stale=tessie_stale)
    tesla = [d for d in decisions if d.actuator == "vehicle.tesla"]
    assert [(d.kind, d.result, d.codes[0]) for d in tesla] == [
        (CommandKind.CURRENT, DecisionResult.NOT_SENT, "safe_state")
    ] * 3  # ein veralteter oder fehlender Wert sagt nicht, dass es nicht lädt
    assert "actuator.failed.vehicle.tesla" in alarm_ids(rt)


async def test_watchdog_trip_forgets_the_expectations(runtime_env) -> None:
    """Wie ``test_foreign_change_locks_the_evcs`` (2b), nur mit der Regelung: Sie schreibt 10 A,
    das erwartet der core danach. In 2b stand die Erwartung nach dem sicheren Zustand bei 6 A, also
    beim Wert des Watchdogs; so hätte der Test nichts geprüft.
    """
    plant = Plant(watchdog_state=1)
    state = dict(EGOLF_PLUGGED)
    rt, clock = live_evcs(runtime_env, plant=plant, state=state, extra=SURPLUS)
    run(rt, clock, 32 + 121)  # Start bei 151 s mit 10 A, bestätigt bei 152 s: ab jetzt erwartet
    plant.watchdog_state = 2  # der Watchdog löst aus und setzt den Sicherstrom
    plant.follow = False
    state["wallbox.evcs.gx_set_current_a"] = 6
    run(rt, clock, 40)  # drei Prüfungen im Abstand von 10 s fänden die „fremde“ Änderung
    assert "watchdog.tripped" in alarm_ids(rt)
    assert "competitor.foreign.wallbox.evcs" not in alarm_ids(rt)
    assert actuator_health(rt, "wallbox.evcs")["locked"] == []


@pytest.mark.parametrize(
    ("watchdog_state", "foreign"), [(1, True), (2, False)], ids=["armed", "tripped"]
)
async def test_watchdog_trip_forgets_the_expectations_of_victron(
    runtime_env, watchdog_state, foreign
) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"ess.override.setpoint": None}  # kein Override aktiv
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.foreign.expect("victron", CommandKind.GRID_SETPOINT, 100.0)  # an Stelle eines Befehls
    plant.watchdog_state = watchdog_state  # ausgelöst: Der Watchdog hebt den Override auf
    run(rt, clock, 40)
    assert ("competitor.foreign.victron" in alarm_ids(rt)) is foreign
