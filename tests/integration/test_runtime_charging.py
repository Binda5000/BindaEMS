"""Laufzeit mit der Laderegelung im Regelzyklus (Plan 2c-1): Entscheidungen der Regelung im
Dry-Run und live, Neustart während des Ladens, Übergabe, Health je Fahrzeug und der gemeinsame
HTTP-Client."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from tests.helpers import T0
from tests.integration.conftest import GOOD_STATE, allow_evcs_live, live_evcs, run
from tests.integration.plant import Plant

from bindaems.shared.control import ActuatorMode, ChargeMode, CommandKind, Intent, VehicleIntent
from bindaems.shared.decisions import Decision, DecisionResult

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


def evcs_writes(plant: Plant) -> list[tuple[str, float | int | None]]:
    return [write for write in plant.writes if write[0].startswith("evcharger/")]


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
    assert seen == [{"vehicle.tesla": "twc"}]  # der e-Golf hat keinen eigenen Aktor


async def test_a_sleeping_tesla_gets_a_wake_start(runtime_env) -> None:
    car = {"online": False, "at_home": True, "plugged": True, "charging_state": "Stopped"}
    state = GOOD_STATE | {f"vehicle.tesla.{name}": value for name, value in car.items()}
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
            ("mode_now", "phase_limit", "wake"),
        )
    ]
    assert rt.health().vehicles[0]["wakes_today"] == 1  # zählt auch im Dry-Run


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
    await asyncio.sleep(0.01)  # der Befehl läuft im Hintergrund
    assert requests == ["/5YJ3E7EB0MF000000/command/set_charging_amps"]
    await rt.shutdown()
    assert client.is_closed  # der gemeinsame Client schließt mit der Laufzeit
