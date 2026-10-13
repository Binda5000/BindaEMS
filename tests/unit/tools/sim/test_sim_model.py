"""Tests des Anlagenmodells: Netz, ESS, Akku, PV, Watchdog, Ausfälle und fremder Regler.

Die erwarteten Werte sind von Hand gerechnet, mit der Beispielkonfiguration: 230 V, Importlimit
20 A (4600 W je Phase), Akku 30 kWh, Reserve 20 %, Laden bis 10 kW, Entladen bis 12 kW (4 kW je
Phase).
"""

import random

import pytest
from tests.integration.conftest import GOOD_STATE
from tests.unit.tools.sim.conftest import (
    T0,
    WAIT,
    at,
    at_once,
    command_url,
    run,
    tessie_client,
    tick,
)

from bindaems.core.adapters.victron_mqtt import HEARTBEAT_PATH, MIN_SOC_PATH
from bindaems.core.checks.selfcheck import run_selfcheck
from bindaems.core.control.charge_inputs import observe
from bindaems.core.state.derived import PHASES, derive
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION, WatchdogState, read_watchdog
from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import Quality, SignalKind
from bindaems.tools.sim.model import PlantModel, _Routes

# --- ESS, Peak Shaving und Akku -----------------------------------------------------------------


def test_ess_covers_the_house_from_the_battery(model) -> None:
    model.house_w = (1000.0, 1000.0, 1000.0)
    model.step(T0)
    assert model.battery_w == pytest.approx(-3000.0)
    assert all(abs(model.grid_a[p]) < 0.01 for p in PHASES)


def test_peak_shaving_holds_the_import_limit(model) -> None:
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    assert model.grid_a["L1"] == pytest.approx(20.0)


def test_charger_only_does_not_discharge(model) -> None:
    model.charger_only = True
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    assert model.battery_w >= 0 and model.grid_a["L1"] == pytest.approx(8000.0 / 230)


def test_reserve_stops_discharge(model) -> None:
    model.soc_pct = 20.0
    model.house_w = (1000.0, 1000.0, 1000.0)
    model.step(T0)
    assert model.battery_w >= 0


def test_island_supplies_from_the_battery(model, store) -> None:
    model.outages.add("grid")
    model.step(T0)
    model.publish(store, T0)
    assert store.snapshot().ok("system.active_in_source") == 240


def test_battery_takes_the_pv_surplus_up_to_its_charge_limit(model) -> None:
    # 15 kW PV, 900 W Haus: Der Akku nimmt 10 kW, je Phase 3333 W; der Rest geht ins Netz.
    model.pv_w = 15000.0
    model.house_w = (300.0, 300.0, 300.0)
    model.step(T0)
    assert model.battery_w == pytest.approx(10000.0)
    assert model.grid_a == pytest.approx(dict.fromkeys(PHASES, (300 + 10000 / 3 - 5000) / 230))
    assert model.battery_feed_a == pytest.approx(dict.fromkeys(PHASES, 0.0))


def test_full_battery_takes_no_more(model) -> None:
    model.soc_pct = 100.0
    model.pv_w = 6000.0
    model.house_w = (300.0, 300.0, 300.0)
    model.step(T0)
    assert model.battery_w == 0.0
    assert model.grid_a == pytest.approx(dict.fromkeys(PHASES, -1700.0 / 230))


def test_peak_shaving_stops_at_a_third_of_the_discharge_limit(model) -> None:
    # Anteil des ESS −3533 W je Phase; L1 bräuchte −5400 W für 20 A, der Multi gibt höchstens
    # −4000 W: L1 bleibt bei 6000 W.
    model.house_w = (10000.0, 300.0, 300.0)
    model.step(T0)
    assert model.grid_a["L1"] == pytest.approx(6000.0 / 230)
    assert model.battery_w == pytest.approx(-4000.0 - 2 * 10600.0 / 3)
    assert model.battery_feed_a["L1"] == pytest.approx(4000.0 / 230)


def test_peak_shaving_only_above_the_reserve(model) -> None:
    model.soc_pct = 20.0
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    assert model.battery_w == 0.0
    assert model.grid_a["L1"] == pytest.approx(8000.0 / 230)


def test_soc_follows_the_battery_power(model, store, clock) -> None:
    # 3000 W aus dem Akku 360 s lang sind 300 Wh, 1 % von 30 kWh.
    model.house_w = (1000.0, 1000.0, 1000.0)
    run(model, store, clock, 360)
    assert model.soc_pct == pytest.approx(59.0)
    assert store.snapshot().ok("battery.soc_pct") == pytest.approx(59.0, abs=0.01)


def test_island_runs_the_loads_from_the_battery_without_pv(model, store, clock) -> None:
    # Ohne Netz ist die PV am AC-Eingang aus; der Akku trägt die Lasten, auch unter der Reserve.
    model.soc_pct = 20.0
    model.pv_w = 3000.0
    model.house_w = (1000.0, 500.0, 500.0)
    model.outages.add("grid")
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert model.battery_w == pytest.approx(-2000.0)
    assert model.grid_a == dict.fromkeys(PHASES, 0.0)
    assert model.battery_feed_a == pytest.approx(
        {"L1": 1000 / 230, "L2": 500 / 230, "L3": 500 / 230}
    )
    assert snap.ok("grid.l1.power_w") == 0.0 and snap.ok("grid.l1.voltage_v") == 0.0
    assert snap.ok("pv.huawei.power_w") == 0.0
    assert snap.ok("vebus.l1.ac_in_power_w") == 0.0 and snap.ok("vebus.l1.ac_out_power_w") == 1000.0
    model.outages.discard("grid")
    run(model, store, clock, 1)
    assert store.snapshot().ok("system.active_in_source") == 1
    assert model.battery_w == pytest.approx(1000.0)  # PV deckt die Lasten, der Rest lädt


# --- Was der core sieht --------------------------------------------------------------------------


def test_the_core_derives_the_model_values(model, store, clock, cfg) -> None:
    # Haus 1000 W je Phase, e-Golf mit 10 A auf L1 und L2: Lasten 3300/3300/1000 W, der Akku
    # deckt 7600 W, je Phase 2533 W.
    evcs = model.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 10
    model.plug("egolf", "evcs")
    model.house_w = (1000.0, 1000.0, 1000.0)
    run(model, store, clock, 2)
    snap = store.snapshot()
    derived = derive(snap, cfg)
    grid, wallboxes, _ = observe(snap, derived, cfg, store.connected)
    assert derived.phase_import_a == pytest.approx({"L1": 10 / 3, "L2": 10 / 3, "L3": -20 / 3})
    assert grid.battery_feed_a == pytest.approx(dict.fromkeys(PHASES, 7600 / 3 / 230))
    assert derived.wallbox_phase_a["evcs"] == {"L1": 10.0, "L2": 10.0, "L3": 0.0}
    assert derived.battery_w == pytest.approx(-7600.0)
    assert grid.battery_w == pytest.approx(-7600.0) and grid.soc_pct == pytest.approx(60.0, abs=0.1)
    assert derived.balance_residual_w == pytest.approx(0.0, abs=1e-6)
    assert wallboxes["evcs"].connected and wallboxes["evcs"].charging
    assert model.grid_a == pytest.approx(derived.phase_import_a)


def test_pv_feeds_the_ac_input_on_three_phases(model, store, clock, cfg) -> None:
    # 6 kW PV, 900 W Haus: Der Akku nimmt 5100 W, am Netz bleibt nichts.
    model.pv_w = 6000.0
    model.house_w = (300.0, 300.0, 300.0)
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("pv.huawei.power_w") == 6000.0
    assert [snap.ok(f"pv.huawei.l{n}.power_w") for n in (1, 2, 3)] == [2000.0] * 3
    assert [snap.ok(f"vebus.l{n}.ac_in_power_w") for n in (1, 2, 3)] == [2000.0] * 3
    derived = derive(snap, cfg)
    assert derived.pv_total_w == 6000.0 and derived.house_load_w == pytest.approx(900.0)
    assert derived.battery_w == pytest.approx(5100.0)
    assert derived.phase_import_a == pytest.approx(dict.fromkeys(PHASES, 0.0))


def test_without_victron_instances_the_adapter_takes_the_first_seen(cfg, store, clock) -> None:
    # Ohne Instanzen nimmt der Adapter die erste, die er sieht; die EVCS hat dann keinen GX-Dienst.
    bare = cfg.model_copy(
        update={"victron": cfg.victron.model_copy(update={"instances": VictronInstances()})}
    )
    plant = PlantModel(bare, random.Random(1))  # noqa: S311 - Saat der Simulation
    plant.attach(store)
    plant.pv_w = 3000.0
    evcs = plant.wallboxes["evcs"]
    evcs.start_stop, evcs.set_current_a = 1, 10
    plant.plug("egolf", "evcs")
    run(plant, store, clock, 1)
    snap = store.snapshot()
    derived = derive(snap, bare)
    assert derived.pv_total_w == 3000.0 and derived.grid_w is not None
    assert snap.ok("wallbox.evcs.current_a") == 10.0 and snap.get("wallbox.evcs.status") is None
    with pytest.raises(ValueError, match="nicht freigegeben"):
        plant.mqtt_write("evcharger/40/SetCurrent", 6)


def test_a_cerbo_topic_without_a_signal_is_an_error(cfg) -> None:
    # Schutz vor Tippfehlern in den Topics des Modells: sonst fehlte das Signal still
    routes = _Routes(cfg.victron.instances)
    assert routes("grid/30/Ac/L1/Power") == (("grid.l1.power_w", SignalKind.MEASUREMENT),)
    with pytest.raises(ValueError, match="grid/30/Ac/L1/Powr"):
        routes("grid/30/Ac/L1/Powr")


def test_writes_need_the_time_of_a_step(cfg) -> None:
    plant = PlantModel(cfg, random.Random(1))  # noqa: S311 - Saat der Simulation
    with pytest.raises(RuntimeError, match="step"):
        plant.mqtt_write(HEARTBEAT_PATH, 1)


def test_published_state_passes_the_selfcheck(model, store, clock, cfg) -> None:
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert {signal: snap.ok(signal) for signal in GOOD_STATE} == GOOD_STATE
    assert snap.ok("system.active_in_source") == 1
    results = run_selfcheck(snap, cfg, store.ok_since, clock.now())
    # frische Daten verlangen 30 s ununterbrochen OK im Takt der Laufzeit; das prüft hier niemand
    assert {r.id: r.status for r in results if r.id != "fresh_data"} == {
        "phases": "ok",
        "ess_mode": "ok",
        "batterylife": "ok",
        "dess": "ok",
        "schedules": "ok",
        "min_soc": "ok",
        "multi_mode": "ok",
        "import_limit": "ok",
        "evcs_mode.evcs": "ok",
    }


def test_charger_only_shows_in_the_multi_mode(model, store, clock, cfg) -> None:
    model.charger_only = True
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("vebus.mode") == 1
    check = next(
        r for r in run_selfcheck(snap, cfg, store.ok_since, clock.now()) if r.id == "multi_mode"
    )
    assert check.status == "warn"


def test_sources_age_like_the_adapters(model, store, clock) -> None:
    # victron und evcs 5 s, twc 10 s, tessie 900 s
    model.plug("egolf", "evcs")
    model.plug("tesla", "twc")
    run(model, store, clock, 1)  # Werte von T0, die Uhr steht auf T0 + 1 s
    signals = ("grid.l1.power_w", "wallbox.evcs.current_a", "wallbox.twc.l1.current_a")
    tessie = "vehicle.tesla.soc_pct"

    def quality(signal: str) -> Quality:
        reading = store.snapshot().get(signal)
        assert reading is not None
        return reading.quality

    clock.advance(4)  # T0 + 5 s
    assert [quality(s) for s in signals] == [Quality.OK, Quality.OK, Quality.OK]
    clock.advance(1)  # T0 + 6 s
    assert [quality(s) for s in signals] == [Quality.STALE, Quality.STALE, Quality.OK]
    clock.advance(5)  # T0 + 11 s
    assert quality("wallbox.twc.l1.current_a") is Quality.STALE
    assert quality(tessie) is Quality.OK
    clock.advance(890)  # T0 + 901 s
    assert quality(tessie) is Quality.STALE
    assert {store.connected(s) for s in ("victron", "evcs", "twc", "tessie:tesla")} == {True}


# --- Ausfälle ------------------------------------------------------------------------------------


def test_cerbo_outage_stops_victron_values_and_writes(model, store, clock) -> None:
    run(model, store, clock, 1)
    model.outages.add("cerbo")
    run(model, store, clock, 1)
    reading = store.snapshot().get("battery.soc_pct")
    assert reading is not None and reading.ts == T0 and reading.quality is Quality.STALE
    assert store.connected("victron") is False
    assert model.mqtt_write("evcharger/40/SetCurrent", 10) is False
    assert model.mqtt_write(HEARTBEAT_PATH, 5) is False
    model.outages.discard("cerbo")
    run(model, store, clock, 1)
    assert store.connected("victron") is True
    assert store.snapshot().ok("battery.soc_pct") == pytest.approx(60.0)
    assert model.mqtt_write("evcharger/40/SetCurrent", 10) is True


@pytest.mark.parametrize(
    ("outage", "source", "signal"),
    [
        ("evcs", "evcs", "wallbox.evcs.current_a"),
        ("twc", "twc", "wallbox.twc.l1.current_a"),
    ],
)
def test_device_outage_disconnects_its_source(model, store, clock, outage, source, signal) -> None:
    model.plug("egolf", "evcs")
    model.plug("tesla", "twc")
    run(model, store, clock, 1)
    model.outages.add(outage)
    run(model, store, clock, 2)
    reading = store.snapshot().get(signal)
    assert reading is not None and reading.ts == T0 and reading.quality is Quality.STALE
    assert store.connected(source) is False
    model.outages.discard(outage)
    run(model, store, clock, 1)
    reading = store.snapshot().get(signal)
    assert reading is not None and reading.ts == at(3) and reading.quality is Quality.OK
    assert store.connected(source) is True


async def test_tessie_outage_keeps_its_source_like_the_adapter(model, store, clock) -> None:
    # Der echte Tessie-Adapter trennt seine Quelle bei Fehlern nicht: Es kommen keine neuen Werte,
    # die alten veralten nach 900 s, und Befehle enden mit 503.
    signal = "vehicle.tesla.charging_state"
    model.plug("tesla", "twc")
    async with tessie_client(model) as client:
        await tick(model, store, clock, 1)  # Abfrage bei T0, die nächste wäre bei 60 s
        model.outages.add("tessie")
        response = await at_once(client.post(command_url("start_charging"), params=WAIT))
        assert response.status_code == 503
        run(model, store, clock, 120)  # bis T0 + 120 s, ohne Abfrage
        reading = store.snapshot().get(signal)
        assert reading is not None and reading.ts == T0 and reading.quality is Quality.OK
        assert store.connected("tessie:tesla") is True
        run(model, store, clock, 780)  # bis T0 + 900 s; die Uhr steht auf 901 s
        reading = store.snapshot().get(signal)
        assert reading is not None and reading.ts == T0 and reading.quality is Quality.STALE
        assert store.connected("tessie:tesla") is True
        model.outages.discard("tessie")
        run(model, store, clock, 1)  # nach dem Ausfall fragt das Modell gleich wieder ab
    reading = store.snapshot().get(signal)
    assert reading is not None and reading.ts == at(901) and reading.quality is Quality.OK
    assert not model.teslas["tesla"].charge_enabled  # der Befehl mit 503 wirkte nicht


def test_after_a_tessie_outage_the_model_polls_at_once(model, store, clock) -> None:
    # Vereinfachung des Modells: Nach dem Ausfall fragt es sofort ab, nicht erst zur geplanten Zeit.
    model.plug("tesla", "twc")
    run(model, store, clock, 1)  # Abfrage bei T0, die nächste wäre bei 60 s
    model.outages.add("tessie")
    run(model, store, clock, 2)
    model.outages.discard("tessie")
    run(model, store, clock, 1)  # T0 + 3 s
    reading = store.snapshot().get("vehicle.tesla.charging_state")
    assert reading is not None and reading.ts == at(3)


def test_tessie_outage_before_the_first_poll_never_connects(model, store, clock) -> None:
    # Verbunden ist Tessie wie beim Adapter erst nach der ersten Abfrage.
    model.outages.add("tessie")
    run(model, store, clock, 5)
    assert store.connected("tessie:tesla") is False
    assert store.snapshot().get("vehicle.tesla.soc_pct") is None


def test_evcs_outage_keeps_the_gx_values(model, store, clock) -> None:
    # Nur die Modbus-Quelle des core fällt aus; der Cerbo erreicht die EVCS weiter.
    model.plug("egolf", "evcs")
    model.outages.add("evcs")
    run(model, store, clock, 2)
    reading = store.snapshot().get("wallbox.evcs.status")
    assert reading is not None and reading.ts == at(1) and reading.quality is Quality.OK
    assert model.mqtt_write("evcharger/40/SetCurrent", 12)
    run(model, store, clock, 1)
    assert store.snapshot().ok("wallbox.evcs.gx_set_current_a") == 12


def test_unknown_outage_is_an_error(model) -> None:
    model.outages.add("internet")
    with pytest.raises(ValueError, match="internet"):
        model.step(T0)


# --- Schreibwege ---------------------------------------------------------------------------------


def test_only_the_writable_paths_of_the_adapter(model) -> None:
    model.step(T0)
    with pytest.raises(ValueError, match="nicht freigegeben"):
        model.mqtt_write("vebus/276/Mode", 4)


def test_victron_settings_follow_the_writes(model, store, clock) -> None:
    run(model, store, clock, 1)
    assert model.mqtt_write(MIN_SOC_PATH, 30)
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("ess.min_soc_pct") == 30
    assert snap.ok("settings.cgwacs.batterylife.minimumsoclimit") == 30


# --- Watchdog ------------------------------------------------------------------------------------


def watchdog_state(store) -> object:
    return store.snapshot().ok("watchdog.state")


def test_watchdog_arms_trips_and_releases(model, store, clock) -> None:
    # Heartbeat bei 0 → bei 30 s state 1; 90 s ohne → state 2 und EVCS-Sollstrom 6; Heartbeat 0 →
    # state 0
    model.wallboxes["evcs"].set_current_a = 10
    run(model, store, clock, 1)  # T0
    assert model.mqtt_write(HEARTBEAT_PATH, 7)
    run(model, store, clock, 29)  # bis T0 + 29 s
    assert watchdog_state(store) == 0
    run(model, store, clock, 1)  # T0 + 30 s
    status = read_watchdog(store.snapshot())
    assert status.state is WatchdogState.ARMED and status.heartbeat == 7
    assert status.timeout_s == 90 and status.version == WATCHDOG_VERSION
    run(model, store, clock, 59)  # bis T0 + 89 s
    assert watchdog_state(store) == 1
    assert store.snapshot().ok("wallbox.evcs.gx_set_current_a") == 10
    run(model, store, clock, 1)  # T0 + 90 s
    status = read_watchdog(store.snapshot())
    assert status.state is WatchdogState.TRIPPED
    assert status.trip_count == 1 and status.last_trip == at(90) and status.last_trip_failures == 0
    run(model, store, clock, 1)  # die EVCS übernimmt den Sicherstrom nach 1 s
    snap = store.snapshot()
    assert (
        snap.ok("wallbox.evcs.gx_set_current_a") == 6 and snap.ok("wallbox.evcs.set_current_a") == 6
    )
    assert model.mqtt_write(HEARTBEAT_PATH, 0)
    run(model, store, clock, 1)
    assert watchdog_state(store) == 0
    assert store.snapshot().ok("watchdog.heartbeat") == 0  # das Echo, auch die 0


def test_tripped_watchdog_rearms_after_30_s_of_heartbeats(model, store, clock) -> None:
    run(model, store, clock, 1)
    model.mqtt_write(HEARTBEAT_PATH, 1)
    run(model, store, clock, 100)  # scharf bei 30 s, ausgelöst bei 90 s; die Uhr steht auf 101 s
    assert watchdog_state(store) == 2
    for second in range(101, 132):
        model.step(at(second))
        model.mqtt_write(HEARTBEAT_PATH, second)  # jede Sekunde, ab 101 s
        model.publish(store, at(second))
        clock.advance(1)
        assert watchdog_state(store) == (1 if second >= 131 else 2)


def test_watchdog_ignores_values_it_would_reject(model, store, clock) -> None:
    run(model, store, clock, 1)
    assert model.mqtt_write(HEARTBEAT_PATH, -3)  # angenommen vom Cerbo, abgelehnt vom Watchdog
    run(model, store, clock, 40)
    assert watchdog_state(store) == 0
    assert store.snapshot().ok("watchdog.heartbeat") is None


# --- fremder Regler und Saat ---------------------------------------------------------------------


def test_foreign_controller_sets_the_devices_every_45_s(model) -> None:
    evcs, tesla = model.wallboxes["evcs"], model.teslas["tesla"]
    model.foreign = True
    seen = []
    for second in range(91):
        model.step(at(second))
        seen.append((evcs.set_current_a, evcs.start_stop, tesla.charge_amps))
    assert all(values == seen[0] for values in seen[1:45])
    assert all(values == seen[45] for values in seen[46:90])
    for current, start_stop, amps in (seen[0], seen[45], seen[90]):
        assert 6 <= current <= 16 and start_stop == 1 and 5 <= amps <= 16
    model.foreign = False
    for second in range(91, 200):
        model.step(at(second))
    assert (evcs.set_current_a, evcs.start_stop, tesla.charge_amps) == seen[90]


def test_the_seed_decides_every_random_value(cfg) -> None:
    def record(seed: int) -> list[tuple[float, int]]:
        plant = PlantModel(cfg, random.Random(seed))  # noqa: S311 - Saat der Simulation
        plant.foreign = True
        values = []
        for second in range(0, 450, 45):
            plant.step(at(second))
            values.append(
                (plant.wallboxes["evcs"].set_current_a, plant.teslas["tesla"].charge_amps)
            )
        return values

    assert record(3) == record(3)
    assert record(3) != record(4)
