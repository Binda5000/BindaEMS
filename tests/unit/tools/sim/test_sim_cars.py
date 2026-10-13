"""Tests der Ladepunkte und Fahrzeuge: EVCS mit Auto, Wall Connector und Tesla mit Tessie."""

import asyncio
import random

import httpx
import pytest
from pydantic import SecretStr
from tests.unit.tools.sim.conftest import (
    T0,
    VIN,
    WAIT,
    at,
    at_once,
    command_url,
    run,
    settle,
    tessie_client,
    tick,
)

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.adapters.evcs import KNOWN_PRODUCT_IDS
from bindaems.core.adapters.tessie import TESSIE_BASE_URL
from bindaems.core.control.actuator import Confirmation
from bindaems.core.control.charge_inputs import observe
from bindaems.core.state.derived import derive
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import SignalKind
from bindaems.tools.sim.cars import Tesla

M, S = SignalKind.MEASUREMENT, SignalKind.STATE


def readings(store, names):
    """Wert, Quelle und Art je Signal aus dem Speicher."""
    snap = store.snapshot()
    return {name: (r.value, r.source, r.kind) for name in names if (r := snap.get(name))}


async def command(model, store, clock, client, name: str, **params: str) -> httpx.Response:
    """Ein Befehl an Tessie zur Zeit des letzten Schritts; höchstens 40 s bis zur Antwort."""
    answer = asyncio.create_task(client.post(command_url(name), params=WAIT | params))
    await settle()
    for _ in range(40):
        if answer.done():
            break
        await tick(model, store, clock, 1)
    assert answer.done(), "keine Antwort von Tessie binnen 40 s"
    return answer.result()


# --- EVCS ----------------------------------------------------------------------------------------


def test_evcs_follows_the_set_current_after_a_second(model, store, clock, cfg) -> None:
    # e-Golf angesteckt, Laden an; mqtt_write("evcharger/40/SetCurrent", 10) bei T0 → bei T0+1 s
    # 10 A auf L1 und L2, Status 2
    evcs = model.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 6
    model.plug("egolf", "evcs")
    model.step(T0)
    assert model.mqtt_write("evcharger/40/SetCurrent", 10)
    model.publish(store, T0)
    assert store.snapshot().ok("wallbox.evcs.current_a") == 6.0  # noch der alte Sollstrom
    clock.advance(1)
    model.step(at(1))
    model.publish(store, at(1))
    snap = store.snapshot()
    assert derive(snap, cfg).wallbox_phase_a["evcs"] == {"L1": 10.0, "L2": 10.0, "L3": 0.0}
    assert snap.ok("wallbox.evcs.status") == 2
    assert model.car_a["egolf"] == 10.0 and model.car_w["egolf"] == 4600.0


def test_evcs_switches_charging_after_two_seconds(model, store, clock) -> None:
    model.wallboxes["evcs"].set_current_a = 16
    model.plug("egolf", "evcs")
    run(model, store, clock, 1)
    assert model.mqtt_write("evcharger/40/StartStop", 1)
    run(model, store, clock, 1)
    assert store.snapshot().ok("wallbox.evcs.status") == 1 and model.car_a["egolf"] == 0.0
    run(model, store, clock, 1)
    assert store.snapshot().ok("wallbox.evcs.status") == 2 and model.car_a["egolf"] == 16.0
    assert model.mqtt_write("evcharger/40/StartStop", 0)
    run(model, store, clock, 1)
    assert model.car_a["egolf"] == 16.0
    run(model, store, clock, 1)
    assert store.snapshot().ok("wallbox.evcs.status") == 1 and model.car_a["egolf"] == 0.0


def test_evcs_reports_its_status_like_the_gx_service(model, store, clock, cfg) -> None:
    # 0 getrennt, 1 verbunden, 2 lädt, 3 geladen (Präzisierung 4)
    evcs = model.wallboxes["evcs"]
    evcs.set_current_a = 16

    def status() -> tuple[object, tuple[bool | None, bool | None, bool | None]]:
        snap = store.snapshot()
        _, wallboxes, _ = observe(snap, derive(snap, cfg), cfg, store.connected)
        seen = wallboxes["evcs"]
        return snap.ok("wallbox.evcs.status"), (seen.connected, seen.charging, seen.complete)

    run(model, store, clock, 1)
    assert status() == (0, (False, False, False))
    model.plug("egolf", "evcs")
    run(model, store, clock, 1)
    assert status() == (1, (True, False, False))
    evcs.start_stop = 1
    run(model, store, clock, 1)
    assert status() == (2, (True, True, False))
    model.cars["egolf"].soc_pct = 100.0  # am Ladelimit zieht das Auto nichts mehr
    run(model, store, clock, 1)
    assert status() == (3, (True, False, True))
    assert model.car_a["egolf"] == 0.0
    model.unplug("egolf")
    run(model, store, clock, 1)
    assert status() == (0, (False, False, False))


def test_evcs_current_is_limited_by_the_car(model, store, clock, cfg) -> None:
    # Tesla an der EVCS: Sollstrom 16 A, das Auto will 8 A, auf allen drei Phasen
    tesla = model.teslas["tesla"]
    tesla.charge_enabled, tesla.charge_amps = True, 8
    evcs = model.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 16
    model.plug("tesla", "evcs")
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert derive(snap, cfg).wallbox_phase_a["evcs"] == {"L1": 8.0, "L2": 8.0, "L3": 8.0}
    assert model.car_a["tesla"] == 8.0 and model.car_w["tesla"] == 3 * 8 * 230
    # das Bild „Tesla an der EVCS“: Tessie meldet angesteckt, der Wall Connector kein Fahrzeug
    assert snap.ok("vehicle.tesla.plugged") is True
    assert snap.ok("wallbox.twc.vehicle_connected") is False


def test_evcs_values_come_like_from_the_adapters(model, store, clock) -> None:
    evcs = model.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 10
    model.plug("egolf", "evcs")
    run(model, store, clock, 1)
    modbus = readings(
        store,
        [f"wallbox.evcs.{key}" for key in ("mode", "start_stop", "set_current_a", "current_a")],
    )
    assert modbus == {
        "wallbox.evcs.mode": (0, "evcs", S),
        "wallbox.evcs.start_stop": (1, "evcs", S),
        "wallbox.evcs.set_current_a": (10, "evcs", S),
        "wallbox.evcs.current_a": (10.0, "evcs", M),
    }
    assert store.snapshot().ok("wallbox.evcs.product_id") in KNOWN_PRODUCT_IDS
    gx = readings(
        store,
        [
            "wallbox.evcs.power_w",
            "wallbox.evcs.l1.power_w",
            "wallbox.evcs.l2.power_w",
            "wallbox.evcs.l3.power_w",
            "wallbox.evcs.gx_current_a",
            "wallbox.evcs.status",
            "wallbox.evcs.gx_mode",
            "wallbox.evcs.gx_set_current_a",
            "wallbox.evcs.gx_max_current_a",
            "wallbox.evcs.gx_start_stop",
        ],
    )
    assert gx == {
        "wallbox.evcs.power_w": (4600.0, "victron", M),
        "wallbox.evcs.l1.power_w": (2300.0, "victron", M),
        "wallbox.evcs.l2.power_w": (2300.0, "victron", M),
        "wallbox.evcs.l3.power_w": (0.0, "victron", M),
        "wallbox.evcs.gx_current_a": (10.0, "victron", M),
        "wallbox.evcs.status": (2, "victron", S),
        "wallbox.evcs.gx_mode": (0, "victron", S),
        "wallbox.evcs.gx_set_current_a": (10, "victron", S),
        "wallbox.evcs.gx_max_current_a": (16, "victron", S),
        "wallbox.evcs.gx_start_stop": (1, "victron", S),
    }


def test_evcs_actuator_writes_reach_the_evcs(model, store, clock, cfg) -> None:
    actuator = EvcsActuator("evcs", cfg.wallboxes["evcs"], 40, model.mqtt_write, lambda: True)
    model.plug("egolf", "evcs")
    run(model, store, clock, 1)
    assert actuator.send(CommandKind.CHARGING, True) is None
    assert actuator.send(CommandKind.CURRENT, 12.0) is None
    run(model, store, clock, 2)
    assert actuator.present(store.snapshot()) == {
        CommandKind.CURRENT: 12.0,
        CommandKind.CHARGING: True,
    }
    assert model.car_a["egolf"] == 12.0


def test_evcs_ignores_an_empty_write(model, store, clock) -> None:
    model.wallboxes["evcs"].set_current_a = 10
    run(model, store, clock, 1)
    assert model.mqtt_write("evcharger/40/SetCurrent", None)  # angenommen, ohne Wirkung
    run(model, store, clock, 2)
    assert store.snapshot().ok("wallbox.evcs.gx_set_current_a") == 10


def test_cars_charge_their_battery(model, store, clock) -> None:
    # e-Golf mit 16 A auf zwei Phasen: 7360 W, in 360 s 736 Wh von 38,6 kWh
    evcs = model.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 16
    egolf = model.cars["egolf"]
    egolf.soc_pct = 50.0
    model.plug("egolf", "evcs")
    run(model, store, clock, 360)
    assert egolf.soc_pct == pytest.approx(50.0 + 0.736 / 38.6 * 100)


def test_plugging_needs_a_free_wallbox(model) -> None:
    model.plug("egolf", "evcs")
    with pytest.raises(ValueError, match="evcs"):
        model.plug("tesla", "evcs")
    with pytest.raises(ValueError, match="egolf"):
        model.plug("egolf", "twc")
    model.unplug("egolf")
    model.plug("tesla", "evcs")


# --- Wall Connector ------------------------------------------------------------------------------


def test_wall_connector_reports_the_tesla(model, store, clock, cfg) -> None:
    tesla = model.teslas["tesla"]
    tesla.charge_enabled = True
    model.plug("tesla", "twc")
    run(model, store, clock, 1)
    names = [f"wallbox.twc.{p}.current_a" for p in ("l1", "l2", "l3")]
    flags = ["wallbox.twc.vehicle_connected", "wallbox.twc.contactor_closed"]
    assert readings(store, [*names, *flags, "wallbox.twc.power_w"]) == {
        **dict.fromkeys(names, (16.0, "twc", M)),
        **dict.fromkeys(flags, (True, "twc", S)),
        "wallbox.twc.power_w": (3 * 16 * 230.0, "twc", M),
    }
    snap = store.snapshot()
    _, wallboxes, _ = observe(snap, derive(snap, cfg), cfg, store.connected)
    assert wallboxes["twc"].connected is True and wallboxes["twc"].charging is True
    assert model.car_w["tesla"] == 3 * 16 * 230.0
    tesla.charge_enabled = False
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("wallbox.twc.contactor_closed") is False and snap.ok(names[0]) == 0.0
    assert snap.ok("wallbox.twc.vehicle_connected") is True
    model.unplug("tesla")
    run(model, store, clock, 1)
    assert store.snapshot().ok("wallbox.twc.vehicle_connected") is False


# --- Tesla ---------------------------------------------------------------------------------------


def test_tesla_completes_at_its_limit(model, store, clock, cfg) -> None:
    tesla = model.teslas["tesla"]
    tesla.charge_enabled = True
    tesla.soc_pct = 79.999
    model.plug("tesla", "twc")
    run(model, store, clock, 5)
    assert tesla.soc_pct == 80.0 and model.car_a["tesla"] == 0.0
    run(model, store, clock, 30)  # nächste Abfrage des Tessie-Caches
    snap = store.snapshot()
    assert snap.ok("vehicle.tesla.charging_state") == "Complete"
    assert snap.ok("vehicle.tesla.charge_limit_pct") == 80
    _, _, vehicles = observe(snap, derive(snap, cfg), cfg, store.connected)
    assert vehicles["tesla"].complete is True and vehicles["tesla"].plugged is True


def test_tesla_sleeps_15_min_after_charging(model, store, clock) -> None:
    tesla = model.teslas["tesla"]
    tesla.charge_enabled = True
    model.plug("tesla", "twc")
    run(model, store, clock, 10)  # lädt bis T0 + 9 s
    tesla.charge_enabled = False
    run(model, store, clock, 899)  # bis T0 + 908 s
    assert not tesla.asleep
    run(model, store, clock, 1)  # T0 + 909 s: 15 min nach dem letzten Laden
    assert tesla.asleep and model.car_a["tesla"] == 0.0
    run(model, store, clock, 30)  # Abfragen bei 30 s, 90 s, … 930 s
    assert store.snapshot().ok("vehicle.tesla.online") is False


async def test_a_command_restarts_the_sleep_timer(model, store, clock) -> None:
    tesla = model.teslas["tesla"]
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 600)  # wach seit T0, ohne zu laden
        response = await command(model, store, clock, client, "stop_charging")
        assert response.json() == {"result": True}  # die Antwort kam zwischen 600 und 602 s
        await tick(model, store, clock, 797)  # bis höchstens 1399 s: weniger als 15 min danach
        assert not tesla.asleep  # ohne den Befehl schliefe er seit T0 + 900 s
        await tick(model, store, clock, 110)  # mindestens 15 min nach der Antwort
        assert tesla.asleep


def test_a_tesla_needs_tessie(cfg) -> None:
    rng = random.Random(1)  # noqa: S311 - Saat der Simulation
    with pytest.raises(ValueError, match="egolf"):
        Tesla("egolf", cfg.vehicles["egolf"], cfg.site.location, rng)


# --- Tessie --------------------------------------------------------------------------------------


async def test_tessie_wakes_a_sleeping_car_before_starting(model, store, clock) -> None:
    # Tesla schläft; POST start_charging → Antwort erst nach ≥ 30 s simulierter Zeit (resolve);
    # danach online und lädt
    tesla = model.teslas["tesla"]
    tesla.asleep = True
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)  # T0
        answer = asyncio.create_task(client.post(command_url("start_charging"), params=WAIT))
        await settle()  # der Befehl geht zur Zeit T0 hinaus
        await tick(model, store, clock, 30)  # bis T0 + 30 s
        assert not answer.done() and tesla.asleep
        await tick(model, store, clock, 3)  # bis T0 + 33 s
        assert answer.done()
        response = answer.result()
        assert response.status_code == 200 and response.json() == {"result": True}
        await tick(model, store, clock, 1)
        assert not tesla.asleep and model.car_a["tesla"] == 16.0
        await tick(model, store, clock, 60 - 34 + 1)  # nächste Abfrage bei T0 + 60 s
    snap = store.snapshot()
    assert snap.ok("vehicle.tesla.online") is True
    assert snap.ok("vehicle.tesla.charging_state") == "Charging"


async def test_tessie_answers_after_0_2_to_2_5_s(model, store, clock) -> None:
    model.plug("tesla", "twc")
    waited = []
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        for _ in range(20):
            sent = clock.now()
            response = await command(model, store, clock, client, "stop_charging")
            assert response.json() == {"result": True}
            waited.append((clock.now() - sent).total_seconds())
    assert set(waited) <= {1.0, 2.0, 3.0} and len(set(waited)) > 1


async def test_the_first_command_to_5_a_is_lost(model, store, clock) -> None:
    # Tesla 10 A; set_charging_amps 5 → result true, Strom bleibt 10; derselbe Befehl erneut → 5
    tesla = model.teslas["tesla"]
    tesla.charge_amps = 10
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        first = await command(model, store, clock, client, "set_charging_amps", amps="5")
        assert first.json() == {"result": True} and tesla.charge_amps == 10
        await command(model, store, clock, client, "set_charging_amps", amps="5")
        assert tesla.charge_amps == 5
        await command(model, store, clock, client, "set_charging_amps", amps="12")
        await command(model, store, clock, client, "set_charging_amps", amps="12")  # von 5 A
        assert tesla.charge_amps == 12
        await command(model, store, clock, client, "set_charging_amps", amps="5")
        assert tesla.charge_amps == 12  # wieder der erste Befehl auf 5 A


async def test_the_first_command_from_5_a_is_lost(model, store, clock) -> None:
    # Tesla 5 A; set_charging_amps 8 → result true, Strom bleibt 5; derselbe Befehl erneut → 8
    tesla = model.teslas["tesla"]
    tesla.charge_amps = 5
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        first = await command(model, store, clock, client, "set_charging_amps", amps="8")
        assert first.json() == {"result": True} and tesla.charge_amps == 5
        await command(model, store, clock, client, "set_charging_amps", amps="8")
        assert tesla.charge_amps == 8
        await command(model, store, clock, client, "set_charging_amps", amps="10")
        assert tesla.charge_amps == 10  # ohne 5 A wirkt schon der erste Befehl


def test_tessie_values_change_only_at_poll_times(model, store, clock) -> None:
    # lädt: charge_amps im Speicher nur alle 30 s neu (Zeitstempel der Lesung)
    tesla = model.teslas["tesla"]
    tesla.charge_enabled = True
    model.plug("tesla", "twc")
    run(model, store, clock, 1)
    tesla.charge_amps = 10  # direkt am Auto, ab T0 + 1 s
    run(model, store, clock, 29)  # bis T0 + 29 s
    reading = store.snapshot().get("vehicle.tesla.charge_amps")
    assert reading is not None and (reading.value, reading.ts) == (16, T0)
    assert model.car_a["tesla"] == 10.0  # das Auto selbst folgt sofort
    run(model, store, clock, 1)  # T0 + 30 s
    reading = store.snapshot().get("vehicle.tesla.charge_amps")
    assert reading is not None and (reading.value, reading.ts) == (10, at(30))
    assert reading.source == "tessie:tesla" and reading.kind is M


@pytest.mark.parametrize(
    ("plugged", "charging", "interval"), [(True, True, 30), (True, False, 60), (False, False, 600)]
)
def test_tessie_polls_like_the_adapter(model, store, clock, plugged, charging, interval) -> None:
    tesla = model.teslas["tesla"]
    tesla.charge_enabled = charging
    if plugged:
        model.plug("tesla", "twc")
    run(model, store, clock, interval)
    reading = store.snapshot().get("vehicle.tesla.soc_pct")
    assert reading is not None and reading.ts == T0
    run(model, store, clock, 1)
    reading = store.snapshot().get("vehicle.tesla.soc_pct")
    assert reading is not None and reading.ts == at(interval)


async def test_tessie_outage_answers_503(model, store, clock) -> None:
    tesla = model.teslas["tesla"]
    model.plug("tesla", "twc")
    model.outages.add("tessie")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        response = await at_once(client.post(command_url("start_charging"), params=WAIT))
        assert response.status_code == 503
        await tick(model, store, clock, 35)
    assert not tesla.charge_enabled


async def test_an_abandoned_command_does_not_reach_the_car(model, store, clock) -> None:
    tesla = model.teslas["tesla"]
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        answer = asyncio.create_task(client.post(command_url("start_charging"), params=WAIT))
        await settle()
        answer.cancel()  # wie ``TeslaActuator.aclose`` beim Herunterfahren
        await tick(model, store, clock, 5)
    assert answer.cancelled() and not tesla.charge_enabled


async def test_tessie_knows_only_the_commands_of_the_actuator(model, store, clock) -> None:
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)
        state = await at_once(client.get(f"{TESSIE_BASE_URL}/{VIN}/state"))
        other_vin = f"{TESSIE_BASE_URL}/5YJ3E7EB0MF999999/command/start_charging"
        other = await at_once(client.post(other_vin, params=WAIT))
        unknown = await at_once(client.post(command_url("honk"), params=WAIT))
        no_amps = await at_once(
            client.post(command_url("set_charging_amps"), params=WAIT | {"amps": "x"})
        )
    assert [r.status_code for r in (state, other, unknown, no_amps)] == [404, 404, 404, 400]


async def test_the_tesla_actuator_commands_reach_the_car(model, store, clock, cfg) -> None:
    tesla = model.teslas["tesla"]
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        actuator = TeslaActuator(
            "tesla",
            cfg.vehicles["tesla"],
            SecretStr("t" * 32),
            client,
            lambda: True,
            clock=clock.now,
        )
        await tick(model, store, clock, 1)
        assert actuator.send(CommandKind.CHARGING, True) is None
        assert actuator.send(CommandKind.CURRENT, 10.0) is None
        results = {}
        for _ in range(70):
            await tick(model, store, clock, 1)
            snap = store.snapshot()
            for kind, value in ((CommandKind.CHARGING, True), (CommandKind.CURRENT, 10.0)):
                results[kind] = actuator.confirm(kind, value, snap)[0]
            if all(result is not Confirmation.PENDING for result in results.values()):
                break
        await actuator.aclose()
    assert results == dict.fromkeys(results, Confirmation.CONFIRMED)
    assert tesla.charge_enabled and tesla.charge_amps == 10 and model.car_a["tesla"] == 10.0
