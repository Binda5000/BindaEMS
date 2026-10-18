"""Tests des Anlagenmodells: Netz, ESS, Akku, PV, Watchdog, Ausfälle und fremder Regler.

Die erwarteten Werte sind von Hand gerechnet, mit der Beispielkonfiguration: 230 V, Importlimit
20 A (4600 W je Phase), Akku 30 kWh, Reserve 20 %, Laden bis 10 kW, Entladen bis 12 kW (4 kW je
Phase). Dazu Home Assistant, der Gast und der Tesla woanders (Plan 2c-2b, Präzisierung 12).
"""

import math
import random
from datetime import datetime
from typing import Final

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

from bindaems.core.adapters.victron_mqtt import HEARTBEAT_PATH, MIN_SOC_PATH, SETPOINT_OVERRIDE_PATH
from bindaems.core.checks.selfcheck import run_selfcheck
from bindaems.core.control.charge_inputs import observe
from bindaems.core.state.derived import PHASES, derive
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION, WatchdogState, read_watchdog
from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import Quality, SignalKind
from bindaems.tools.sim.cars import Car
from bindaems.tools.sim.model import GUEST_CAR, PlantModel, _Routes

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


def test_discharge_stops_exactly_at_the_reserve(model) -> None:
    # 0,005 % über der Reserve sind 1,5 Wh von 30 kWh: In dieser Sekunde liefert der Akku nur
    # 5400 W statt der 9000 W des Hauses, dann nichts mehr. Er sinkt nie unter die Reserve.
    model.soc_pct = 20.005
    model.house_w = (3000.0, 3000.0, 3000.0)
    model.step(T0)
    assert model.battery_w == pytest.approx(-5400.0)
    assert model.soc_pct == 20.0
    assert model.grid_a["L1"] == pytest.approx((3000.0 - 1800.0) / 230)
    model.step(at(1))
    assert (model.battery_w, model.soc_pct) == (0.0, 20.0)


def test_peak_shaving_stops_exactly_at_the_reserve(model) -> None:
    model.soc_pct = 20.005
    model.house_w = (8000.0, 300.0, 300.0)  # ESS und Peak Shaving wollten 9133 W
    model.step(T0)
    assert model.battery_w == pytest.approx(-5400.0)
    assert model.soc_pct == 20.0


def test_soc_follows_the_battery_power(model, store, clock) -> None:
    # 3000 W aus dem Akku 360 s lang sind 300 Wh, 1 % von 30 kWh.
    model.house_w = (1000.0, 1000.0, 1000.0)
    run(model, store, clock, 360)
    assert model.soc_pct == pytest.approx(59.0)
    assert store.snapshot().ok("battery.soc_pct") == pytest.approx(59.0, abs=0.01)


ETA: Final = math.sqrt(0.88)  # je Richtung bei 88 % Roundtrip (Plan 3c, Präzisierung 15)


def lossy(cfg, soc_pct: float = 60.0) -> PlantModel:
    """Das Modell mit 88 % Roundtrip, SOC ``soc_pct``, ohne PV und Autos."""
    plant = PlantModel(cfg, random.Random(1), roundtrip_pct=88.0)  # noqa: S311 - Saat
    plant.soc_pct = soc_pct
    return plant


def test_charging_stores_eta_times_the_energy(cfg) -> None:
    # 1 h mit 3000 W Laden (PV 3900 W, Haus 900 W): 3 kWh · √0,88 im Akku, von 30 kWh
    model = lossy(cfg)
    model.pv_w = 3900.0
    model.house_w = (300.0, 300.0, 300.0)
    for t in range(3600):
        model.step(at(t))
    assert model.battery_w == pytest.approx(3000.0)
    assert model.soc_pct == pytest.approx(60.0 + 3.0 * ETA / 30.0 * 100)


def test_discharging_draws_energy_divided_by_eta(cfg) -> None:
    # 3000 W aus dem Akku 360 s lang sind 300 Wh am Netz, 300 Wh / √0,88 aus dem Akku
    model = lossy(cfg)
    model.house_w = (1000.0, 1000.0, 1000.0)
    for t in range(360):
        model.step(at(t))
    assert model.battery_w == pytest.approx(-3000.0)
    assert model.soc_pct == pytest.approx(60.0 - 1.0 / ETA)


def test_reserve_limit_counts_eta(cfg) -> None:
    # 0,005 % über der Reserve sind 1,5 Wh im Akku; am Netz kommen davon 1,5 Wh · √0,88 an: In
    # dieser Sekunde liefert der Akku 5400 W · √0,88, dann nichts mehr, und er bleibt auf 20 %.
    model = lossy(cfg, soc_pct=20.005)
    model.house_w = (3000.0, 3000.0, 3000.0)
    model.step(T0)
    assert model.battery_w == pytest.approx(-5400.0 * ETA)
    assert model.soc_pct == 20.0
    model.step(at(1))
    assert (model.battery_w, model.soc_pct) == (0.0, 20.0)


def test_default_model_stays_lossless(cfg) -> None:
    # ohne Angabe und mit 100 % wie bisher: 300 Wh aus dem Akku sind 1 %, 3 kWh hinein 10 %
    for plant in (
        PlantModel(cfg, random.Random(1)),  # noqa: S311 - Saat der Simulation
        PlantModel(cfg, random.Random(1), roundtrip_pct=100.0),  # noqa: S311 - Saat
    ):
        plant.soc_pct = 60.0
        plant.house_w = (1000.0, 1000.0, 1000.0)
        for t in range(360):
            plant.step(at(t))
        assert plant.soc_pct == pytest.approx(59.0)
        plant.pv_w, plant.house_w = 3900.0, (300.0, 300.0, 300.0)
        for t in range(360, 3960):
            plant.step(at(t))
        assert plant.soc_pct == pytest.approx(69.0)


@pytest.mark.parametrize("roundtrip_pct", [0.0, -5.0, 100.5, math.nan])
def test_a_roundtrip_outside_0_to_100_pct_is_an_error(cfg, roundtrip_pct) -> None:
    with pytest.raises(ValueError, match="Roundtrip"):
        PlantModel(cfg, random.Random(1), roundtrip_pct=roundtrip_pct)  # noqa: S311 - Saat


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


# --- Override des Netz-Sollwerts, Verfall und Dynamic ESS ----------------------------------------

AC_SETPOINT = "settings/0/Settings/CGwacs/AcPowerSetPoint"
DESS_MODE = "settings/0/Settings/DynamicEss/Mode"
HOUSE_900 = (300.0, 300.0, 300.0)  # Hauslast 300 W je Phase, zusammen 900 W


def test_ess_follows_the_override(model) -> None:
    # PV 0, Haus 300 W je Phase: Der ESS regelt den Netzbezug auf den Override. Der Akku lädt mit
    # 3000 − 900 = 2100 W, je Phase 700 W, und am Netz liegen je Phase 300 + 700 = 1000 W an.
    model.house_w = HOUSE_900
    model.step(T0)
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    model.step(at(1))
    assert model.grid_w == pytest.approx(3000.0)
    assert model.battery_w == pytest.approx(2100.0)
    assert model.override_w == 3000.0
    assert model.grid_a == pytest.approx(dict.fromkeys(PHASES, 1000.0 / 230))
    assert model.battery_feed_a == pytest.approx(dict.fromkeys(PHASES, 0.0))


def test_the_override_counts_the_pv(model) -> None:
    # PV 2000 W, Haus 900 W, Override 1500 W: Der Akku lädt mit 2000 − 900 + 1500 = 2600 W.
    model.pv_w = 2000.0
    model.house_w = HOUSE_900
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 1500.0)
    model.step(at(1))
    assert model.grid_w == pytest.approx(1500.0)
    assert model.battery_w == pytest.approx(2600.0)


def test_without_an_override_the_ess_follows_the_ac_power_setpoint(model) -> None:
    # Basis-Sollwert 500 W: 500 W Netzbezug heißt 900 − 500 = 400 W aus dem Akku. Der Override geht
    # vor, und ohne ihn gilt der Basis-Sollwert wieder.
    model.house_w = HOUSE_900
    model._settings[AC_SETPOINT] = 500
    model.step(T0)
    assert model.grid_w == pytest.approx(500.0) and model.battery_w == pytest.approx(-400.0)
    assert model.override_w is None
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2000.0)
    model.step(at(1))
    assert model.grid_w == pytest.approx(2000.0) and model.battery_w == pytest.approx(1100.0)
    model.step(at(300))
    assert model.grid_w == pytest.approx(500.0) and model.override_w is None


@pytest.mark.parametrize("value", [None, "500", True, float("nan"), float("inf")])
def test_an_ac_power_setpoint_that_is_no_number_counts_as_0(model, value) -> None:
    model.house_w = HOUSE_900
    model._settings[AC_SETPOINT] = value
    model.step(T0)
    assert model.grid_w == pytest.approx(0.0) and model.battery_w == pytest.approx(-900.0)


@pytest.mark.parametrize(
    ("override_w", "soc_pct", "battery_w"),
    [
        (20000.0, 60.0, 10000.0),  # lädt höchstens mit 10 kW
        (20000.0, 100.0, 0.0),  # ein voller Akku lädt nicht
        (-20000.0, 60.0, -12000.0),  # entlädt höchstens mit 12 kW
        (-20000.0, 20.0, 0.0),  # an der Reserve entlädt er nicht
    ],
)
def test_the_limits_of_the_ess_bind_the_override(model, override_w, soc_pct, battery_w) -> None:
    model.house_w = HOUSE_900
    model.step(T0)
    model.soc_pct = soc_pct
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, override_w)
    model.step(at(1))
    assert model.battery_w == pytest.approx(battery_w)
    assert model.grid_w == pytest.approx(900.0 + battery_w)
    assert model.override_w == override_w  # er gilt weiter, nur der ESS kann ihm nicht folgen


def test_peak_shaving_binds_the_override(model) -> None:
    # Haus 8000/300/300 W und Override 8600 W: Der ESS ließe L1 mit 8000 W am Netz (34,8 A); Peak
    # Shaving hält die Phase bei 20 A und entlädt dafür 3400 W.
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 8600.0)
    model.step(at(1))
    assert model.grid_a["L1"] == pytest.approx(20.0)
    assert model.battery_w == pytest.approx(-3400.0)
    assert model.grid_w == pytest.approx(4600.0 + 600.0)


def test_override_expires_300_s_after_the_last_write(model) -> None:
    # geschrieben bei T0: bei T0 + 299 s noch da, bei T0 + 300 s weg; der ESS regelt wieder auf
    # AcPowerSetPoint 0 (der Akku trägt das Haus)
    model.house_w = HOUSE_900
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    model.step(at(299))
    assert (model.override_w, model.override_age_s) == (3000.0, 299.0)
    assert model.grid_w == pytest.approx(3000.0)
    model.step(at(300))
    assert (model.override_w, model.override_age_s) == (None, None)
    assert model.grid_w == pytest.approx(0.0) and model.battery_w == pytest.approx(-900.0)


def test_a_new_write_restarts_the_300_s(model) -> None:
    # geschrieben bei T0 und erneut bei T0 + 240 s: Der zweite Wert gilt bis T0 + 539 s, und das
    # Alter zählt ab dem letzten Schreiben
    model.house_w = HOUSE_900
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    model.step(at(240))
    assert model.override_age_s == 240.0
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2000.0)
    model.step(at(300))  # der erste Wert wäre hier verfallen
    assert (model.override_w, model.override_age_s) == (2000.0, 60.0)
    assert model.grid_w == pytest.approx(2000.0)
    model.step(at(539))
    assert (model.override_w, model.override_age_s) == (2000.0, 299.0)
    model.step(at(540))
    assert (model.override_w, model.override_age_s) == (None, None)


def test_none_removes_the_override(model) -> None:
    model.house_w = HOUSE_900
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000)  # auch eine ganze Zahl ist eine Zahl
    model.step(at(1))
    assert model.override_w == 3000.0 and type(model.override_w) is float
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, None) is True
    model.step(at(2))
    assert (model.override_w, model.override_age_s) == (None, None)
    assert model.grid_w == pytest.approx(0.0)


@pytest.mark.parametrize("value", [True, "3000", float("nan"), float("inf"), -float("inf")])
def test_a_value_that_is_no_number_changes_nothing(model, value) -> None:
    # Der Cerbo nimmt den Auftrag an; der Override und die Zeit seines Schreibens bleiben.
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 1500.0)
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, value) is True
    model.step(at(1))
    assert (model.override_w, model.override_age_s) == (1500.0, 1.0)


def test_dess_drops_and_ignores_the_override(model, store, clock) -> None:
    model.house_w = HOUSE_900
    run(model, store, clock, 1)
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert (model.override_w, snap.ok("ess.override.setpoint"), snap.ok("dess.mode")) == (
        3000.0,
        3000.0,
        0,
    )
    model.dess = True  # Dynamic ESS verwirft den Override
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert (model.override_w, model.override_age_s) == (None, None)
    assert model.grid_w == pytest.approx(0.0) and model.battery_w == pytest.approx(-900.0)
    reading = snap.get("ess.override.setpoint")
    assert reading is not None and reading.value is None and reading.quality is Quality.INVALID
    assert snap.ok("dess.mode") == 1
    # ein neuer Auftrag wird angenommen und wirkt nicht, auch nicht vor dem nächsten Schritt
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2500.0) is True
    assert model._cerbo()[SETPOINT_OVERRIDE_PATH] is None
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, None) is True
    run(model, store, clock, 1)
    assert model.override_w is None and model.grid_w == pytest.approx(0.0)
    # ohne Dynamic ESS wirkt der Auftrag wieder
    model.dess = False
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2500.0) is True
    run(model, store, clock, 1)
    assert (model.override_w, store.snapshot().ok("dess.mode")) == (2500.0, 0)


def test_the_cerbo_reports_the_override_and_the_dess_mode(model, store, clock) -> None:
    run(model, store, clock, 1)
    topics = model._cerbo()
    assert topics[SETPOINT_OVERRIDE_PATH] is None and topics[DESS_MODE] == 0
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    run(model, store, clock, 1)
    topics = model._cerbo()
    assert topics[SETPOINT_OVERRIDE_PATH] == 3000.0 and topics[DESS_MODE] == 0
    model.dess = True
    run(model, store, clock, 1)
    topics = model._cerbo()
    assert topics[SETPOINT_OVERRIDE_PATH] is None and topics[DESS_MODE] == 1


def test_dess_makes_the_selfcheck_fail(model, store, clock, cfg) -> None:
    # Die Selbstprüfung sperrt Victron, sobald der Cerbo Dynamic ESS meldet.
    model.dess = True
    run(model, store, clock, 1)
    results = run_selfcheck(store.snapshot(), cfg, store.ok_since, clock.now())
    assert next(r.status for r in results if r.id == "dess") == "fail"


def test_a_cerbo_outage_refuses_override_writes_but_not_the_override(model) -> None:
    # Der Auftrag scheitert; der Override am Gerät läuft weiter und verfällt 300 s nach seinem
    # letzten Schreiben, auch mitten im Ausfall.
    model.house_w = HOUSE_900
    model.step(T0)
    model.mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0)
    model.outages.add("cerbo")
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2000.0) is False
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, None) is False
    model.step(at(100))
    assert (model.override_w, model.override_age_s) == (3000.0, 100.0)
    model.step(at(300))
    assert model.override_w is None
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2000.0) is False  # setzt keinen
    model.step(at(301))
    assert model.override_w is None
    model.outages.discard("cerbo")
    assert model.mqtt_write(SETPOINT_OVERRIDE_PATH, 2000.0) is True
    model.step(at(302))
    assert model.override_w == 2000.0


def test_grid_power_is_the_sum_of_the_phases(model) -> None:
    # 15 kW PV, Haus 900 W: Der Akku nimmt 10 kW, 4100 W gehen ins Netz (negativ); Bezug ist
    # positiv.
    model.pv_w = 15000.0
    model.house_w = HOUSE_900
    model.step(T0)
    assert model.grid_w == pytest.approx(-4100.0)
    assert model.grid_w == pytest.approx(sum(model.grid_a.values()) * 230.0)
    model.pv_w = 0.0
    model.charger_only = True  # der Akku entlädt nicht: Das Haus kommt vom Netz
    model.step(at(1))
    assert model.grid_w == pytest.approx(900.0)


def test_the_pv_of_the_step_is_zero_on_the_island(model) -> None:
    model.pv_w = 3000.0
    model.step(T0)
    assert model.pv_now_w == 3000.0
    model.outages.add("grid")
    model.step(at(1))
    assert model.pv_now_w == 0.0 and model.grid_w == 0.0
    assert model.pv_w == 3000.0  # die Stellgröße des Szenarios bleibt
    model.outages.discard("grid")
    model.step(at(2))
    assert model.pv_now_w == 3000.0


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


# --- Home Assistant, Gast und Tesla woanders (Plan 2c-2b, Präzisierung 12) ------------------------


def test_home_assistant_reports_home_or_away(model, store, clock) -> None:
    run(model, store, clock, 1)
    reading = store.snapshot().get("vehicle.egolf.home_state")
    assert (reading.value, reading.source, reading.kind) == ("home", "ha", SignalKind.STATE)
    model.away = {"egolf"}
    run(model, store, clock, 1)
    assert store.snapshot().ok("vehicle.egolf.home_state") == "not_home"


def test_nobody_is_away_unless_a_scenario_says_so(model) -> None:
    assert model.away == set()


def test_home_assistant_reports_every_vehicle_with_a_home_entity(cfg, store, clock) -> None:
    # nur der Tesla ist weg; der e-Golf bleibt zu Hause
    tesla = cfg.vehicles["tesla"].model_copy(update={"home_entity": "device_tracker.tesla"})
    config = cfg.model_copy(update={"vehicles": cfg.vehicles | {"tesla": tesla}})
    plant = PlantModel(config, random.Random(1))  # noqa: S311 - Saat der Simulation
    plant.attach(store)
    plant.away = {"tesla"}
    run(plant, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("vehicle.tesla.home_state") == "not_home"
    assert snap.ok("vehicle.egolf.home_state") == "home"


def test_without_a_home_or_soc_entity_there_is_no_home_assistant(cfg, store, clock) -> None:
    vehicles = {
        key: v.model_copy(update={"home_entity": None, "soc_entity": None})
        for key, v in cfg.vehicles.items()
    }
    bare = cfg.model_copy(update={"vehicles": vehicles})
    plant = PlantModel(bare, random.Random(1))  # noqa: S311 - Saat der Simulation
    plant.attach(store)
    plant.away = {"egolf"}
    run(plant, store, clock, 1)
    assert store.connected("ha") is None  # die Quelle ist nie registriert worden
    assert store.snapshot().matching("vehicle.egolf.home_state") == {}


def test_home_assistant_reports_the_soc_of_the_egolf_when_it_changes(model, store, clock) -> None:
    # Wie ein Sensor in Home Assistant: ganze Prozent, abgerundet wie das Auto sie meldet, ein neuer
    # Wert nur bei einer Änderung (Plan 4b, Entscheidung 16); der Zeitstempel bleibt bis dahin der
    # der letzten Meldung.
    egolf = model.cars["egolf"]
    egolf.soc_pct = 50.2
    run(model, store, clock, 1)
    reading = store.snapshot().get("vehicle.egolf.soc_pct")
    assert reading is not None
    assert (reading.value, reading.source, reading.kind, reading.ts) == (
        50.0,
        "ha",
        SignalKind.STATE,
        T0,
    )
    egolf.soc_pct = 50.9
    run(model, store, clock, 1)
    assert store.snapshot().get("vehicle.egolf.soc_pct").ts == T0
    egolf.soc_pct = 51.1
    run(model, store, clock, 1)
    reading = store.snapshot().get("vehicle.egolf.soc_pct")
    assert (reading.value, reading.ts) == (51.0, at(2))


def test_the_soc_of_the_egolf_can_stay_unknown(model, store, clock) -> None:
    assert model.egolf_soc_known  # ohne Angabe meldet Home Assistant ihn
    model.egolf_soc_known = False
    run(model, store, clock, 5)
    assert store.snapshot().matching("vehicle.egolf.soc_pct") == {}
    assert store.snapshot().ok("vehicle.egolf.home_state") == "home"  # das andere bleibt


def test_a_soc_entity_alone_brings_home_assistant(cfg, store, clock) -> None:
    vehicles = {key: v.model_copy(update={"home_entity": None}) for key, v in cfg.vehicles.items()}
    plant = PlantModel(cfg.model_copy(update={"vehicles": vehicles}), random.Random(1))  # noqa: S311
    plant.attach(store)
    run(plant, store, clock, 1)
    assert store.connected("ha") is True
    assert store.snapshot().ok("vehicle.egolf.soc_pct") == 50.0
    assert store.snapshot().matching("vehicle.egolf.home_state") == {}


def test_the_ha_source_is_registered_like_the_adapter(model, store, clock) -> None:
    # Wie ``HaAdapter``: ohne Frischegrenze und mit ``invalidate_on_reconnect``; verbunden ist die
    # Quelle mit dem ersten ``publish``.
    assert store.connected("ha") is False
    run(model, store, clock, 1)
    assert store.connected("ha") is True
    store.update("probe", 1.0, source="ha", kind=SignalKind.MEASUREMENT, ts=clock.now())
    clock.advance(100_000)  # ohne Frischegrenze veraltet nicht einmal eine Messung
    reading = store.snapshot().get("probe")
    assert reading is not None and reading.quality is Quality.OK
    store.set_connected("ha", False)  # nach einer Wiederverbindung gilt ein alter Wert nicht mehr
    store.set_connected("ha", True)
    reading = store.snapshot().get("vehicle.egolf.home_state")
    assert reading is not None and reading.ts == T0 and reading.quality is Quality.STALE


def test_the_guest_is_a_car_without_tessie_and_stands_unplugged(model, cfg) -> None:
    guest = model.cars[GUEST_CAR]
    assert GUEST_CAR == "gast" and GUEST_CAR not in cfg.vehicles
    assert type(guest) is Car and GUEST_CAR not in model.teslas  # ohne Tessie
    assert (guest.phases, guest.max_a, guest.usable_kwh) == (3, 16.0, 50.0)
    assert guest.wallbox is None and model.car_a[GUEST_CAR] == 0.0


def test_the_guest_charges_on_its_phases(model, store, clock) -> None:
    model.cars[GUEST_CAR].phases = 1
    model.plug(GUEST_CAR, "twc")
    run(model, store, clock, 1)
    assert (model.car_a[GUEST_CAR], model.car_w[GUEST_CAR]) == (16.0, 16.0 * 230.0)


def test_the_guest_shows_only_at_the_wallbox_it_is_plugged_to(model, store, clock) -> None:
    # Der Gast hat kein Tessie: Der core sieht von ihm nur den Strom an der Wallbox.
    model.plug(GUEST_CAR, "twc")
    run(model, store, clock, 2)
    snap = store.snapshot()
    assert snap.matching("vehicle.gast") == {} and not any("gast" in name for name in snap.readings)
    assert snap.ok("wallbox.twc.l1.current_a") == 16.0


def test_a_tesla_plugged_elsewhere_reports_stopped(model) -> None:
    car = model.teslas["tesla"]
    car.elsewhere = True
    values = car.poll(T0, 230.0, reachable=True)
    assert (car.charging_state(), values["plugged"], values["at_home"]) == ("Stopped", True, True)


def test_a_tesla_plugged_elsewhere_is_plugged_and_at_home_for_the_core(model, store, clock) -> None:
    model.teslas["tesla"].elsewhere = True
    run(model, store, clock, 1)
    snap = store.snapshot()
    assert snap.ok("vehicle.tesla.charging_state") == "Stopped"
    assert (snap.ok("vehicle.tesla.plugged"), snap.ok("vehicle.tesla.at_home")) == (True, True)
    assert snap.ok("wallbox.twc.l1.current_a") == 0.0  # an keiner Wallbox, also ohne Strom


def test_elsewhere_matters_only_for_a_tesla_without_a_wallbox(model) -> None:
    car = model.teslas["tesla"]
    assert car.elsewhere is False and car.charging_state() == "Disconnected"  # wie bisher
    car.elsewhere = True
    car.charge_enabled = True
    model.step(T0)
    assert (car.current_a, car.charging_state()) == (0.0, "Stopped")  # ohne Wallbox kein Strom
    model.plug("tesla", "twc")
    model.step(at(1))
    assert car.charging_state() == "Charging"  # an einer Wallbox gilt es wie sonst
    car.soc_pct = car.limit_pct
    model.step(at(2))
    assert car.charging_state() == "Complete"
    model.unplug("tesla")
    assert car.charging_state() == "Stopped"


def test_self_start_sets_charge_enabled(model) -> None:
    # Das Auto startet selbst, etwa aus der App: „Laden an“ im Auto, ohne Befehl des core.
    from bindaems.tools.sim.scenarios import _SelfStart

    car = model.teslas["tesla"]
    model.plug("tesla", "twc")
    model.step(T0)
    assert (car.charge_enabled, car.current_a) == (False, 0.0)
    _SelfStart("tesla")(model, None)  # type: ignore[arg-type]  # das Ereignis braucht keine Laufzeit
    assert car.charge_enabled is True
    model.step(at(1))
    assert car.current_a > 0 and car.charging_state() == "Charging"


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
        # gescheitert bei 60, 120, 240 und 480 s; der nächste Versuch bei 480 + 480 = 960 s
        run(model, store, clock, 59)  # bis 959 s
        reading = store.snapshot().get(signal)
        assert reading is not None and reading.ts == T0
        run(model, store, clock, 1)
    reading = store.snapshot().get(signal)
    assert reading is not None and reading.ts == at(960) and reading.quality is Quality.OK
    assert not model.teslas["tesla"].charge_enabled  # der Befehl mit 503 wirkte nicht


def test_tessie_retries_with_the_backoff_of_the_adapter(model, store, clock) -> None:
    # Wie der Tessie-Adapter: Nach einer gescheiterten Abfrage wartet er 60 s, dann jeweils das
    # Doppelte, höchstens 900 s. Die erste gelungene setzt das zurück, danach gilt wieder der Takt.
    model.plug("tesla", "twc")  # angesteckt, lädt nicht: Abfrage alle 60 s

    def polled_at() -> datetime:
        reading = store.snapshot().get("vehicle.tesla.charging_state")
        assert reading is not None
        return reading.ts

    run(model, store, clock, 1)  # Abfrage bei 0 s
    model.outages.add("tessie")
    # gescheitert bei 60 s (geplant), 60 + 60 = 120, 120 + 120 = 240, 240 + 240 = 480 und
    # 480 + 480 = 960 s; der nächste Versuch nach höchstens 900 s
    run(model, store, clock, 999)  # bis 999 s
    model.outages.discard("tessie")
    run(model, store, clock, 860)  # bis 1859 s
    assert polled_at() == T0
    run(model, store, clock, 1)
    assert polled_at() == at(960 + 900)
    run(model, store, clock, 60)  # wieder im Takt: 1860 + 60 s
    assert polled_at() == at(1860 + 60)
    model.outages.add("tessie")  # die Abfrage bei 1980 s scheitert
    run(model, store, clock, 61)  # bis 1981 s
    model.outages.discard("tessie")
    run(model, store, clock, 58)  # bis 2039 s
    assert polled_at() == at(1920)
    run(model, store, clock, 1)
    assert polled_at() == at(1980 + 60)  # der Backoff begann nach dem Erfolg wieder bei 60 s


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
