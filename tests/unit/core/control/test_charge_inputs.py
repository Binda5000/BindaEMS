import math
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta

import pytest
from tests.helpers import T0, snap

from bindaems.core.control.charge_inputs import (
    EVCS_CHARGED,
    EVCS_CHARGING,
    EVCS_DISCONNECTED,
    NO_VEHICLE_DATA,
    TWC_CHARGING_A,
    ChargeInputs,
    GridObs,
    VehicleObs,
    WallboxObs,
    homes,
    observe,
    wallbox_connected,
)
from bindaems.core.state.derived import derive
from bindaems.shared.control import CommandKind, Intent
from bindaems.shared.domain import Quality

# Vorgaben des Plans


def obs(cfg, values, connected=lambda source: True):
    snap_ = snap(values)
    return observe(snap_, derive(snap_, cfg), cfg, connected)


def test_battery_feed_per_phase(cfg) -> None:
    grid, _, _ = obs(
        cfg,
        {
            "vebus.l1.ac_in_power_w": 500.0,
            "vebus.l1.ac_out_power_w": 2800.0,
            "grid.l1.voltage_v": 230.0,
            "vebus.l2.ac_in_power_w": 1000.0,
            "vebus.l2.ac_out_power_w": 400.0,
            "grid.l2.voltage_v": 230.0,
            "vebus.l3.ac_in_power_w": 0.0,
            "vebus.l3.ac_out_power_w": 400.0,
        },
    )
    assert grid.battery_feed_a == {"L1": 10.0, "L2": 0.0, "L3": None}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (0, (False, False, False)),
        (1, (True, False, False)),
        (2, (True, True, False)),
        (3, (True, False, True)),
        (7, (True, False, False)),
        (None, (None, None, None)),
    ],
)
def test_evcs_status_codes(cfg, status, expected) -> None:
    values = {} if status is None else {"wallbox.evcs.status": status}
    _, wallboxes, _ = obs(cfg, values)
    evcs = wallboxes["evcs"]
    assert (evcs.connected, evcs.charging, evcs.complete) == expected


def test_wall_connector_charges_from_one_ampere(cfg) -> None:
    currents = {
        "wallbox.twc.l1.current_a": 0.4,
        "wallbox.twc.l2.current_a": 0.3,
        "wallbox.twc.l3.current_a": 0.3,
    }
    _, wallboxes, _ = obs(cfg, {"wallbox.twc.vehicle_connected": True, **currents})
    assert (wallboxes["twc"].connected, wallboxes["twc"].charging) == (True, True)


def test_tessie_signals(cfg) -> None:
    _, _, vehicles = obs(
        cfg,
        {
            "vehicle.tesla.plugged": True,
            "vehicle.tesla.charging_state": "Complete",
            "vehicle.tesla.online": False,
            "vehicle.tesla.at_home": True,
        },
    )
    tesla = vehicles["tesla"]
    assert (tesla.plugged, tesla.charging, tesla.complete, tesla.asleep, tesla.at_home) == (
        True,
        False,
        True,
        True,
        True,
    )
    assert vehicles["egolf"] == NO_VEHICLE_DATA


def test_unreachable_sources(cfg) -> None:
    _, wallboxes, vehicles = obs(
        cfg, {}, connected=lambda source: source not in ("evcs", "tessie:tesla")
    )
    assert not wallboxes["evcs"].reachable and wallboxes["twc"].reachable
    assert not vehicles["tesla"].reachable


# Weitere Fälle: Netz und Akku

GRID = {
    "grid.l1.power_w": 460.0,
    "grid.l1.voltage_v": 230.0,
    "grid.l2.power_w": -230.0,
    "grid.l2.voltage_v": 230.0,
    "grid.l3.power_w": 0.0,
    "grid.l3.voltage_v": 230.0,
    "grid.power_w": 230.0,
    "battery.power_w": -1200.0,
    "battery.soc_pct": 55.0,
}
UNKNOWN_PHASES = {"L1": None, "L2": None, "L3": None}


def test_grid_values(cfg) -> None:
    grid, _, _ = obs(cfg, GRID)
    assert grid.phase_import_a == {"L1": 2.0, "L2": -1.0, "L3": 0.0}
    # Akkuleistung und SOC sind die Werte des Akkus, nicht die abgeleiteten aus den Multis
    assert (grid.grid_w, grid.battery_w, grid.soc_pct) == (230.0, -1200.0, 55.0)


def test_phase_import_is_the_derived_one(cfg) -> None:
    snap_ = snap(GRID | {"grid.l2.voltage_v": 99.0})  # unter 100 V ist der Strom unbekannt
    derived = derive(snap_, cfg)
    grid, _, _ = observe(snap_, derived, cfg, lambda source: True)
    assert grid.phase_import_a == derived.phase_import_a == {"L1": 2.0, "L2": None, "L3": 0.0}


def test_without_values_nothing_is_known(cfg) -> None:
    grid, wallboxes, vehicles = obs(cfg, {})
    assert grid == GridObs(UNKNOWN_PHASES, UNKNOWN_PHASES, None, None, None)
    assert wallboxes == {
        "evcs": WallboxObs(True, None, None, None, None, None),
        "twc": WallboxObs(True, None, None, None, None, None),
    }
    assert vehicles == {
        "tesla": VehicleObs(True, None, None, None, None, None, None, None),
        "egolf": NO_VEHICLE_DATA,
    }


@pytest.mark.parametrize(
    ("ac_in", "ac_out", "volts", "expected"),
    [
        (500.0, 2800.0, 230.0, 10.0),
        (1000.0, 400.0, 230.0, 0.0),  # der Multi nimmt auf: nichts aus dem Akku
        (0.0, 2300.0, 100.0, 23.0),  # ab 100 V
        (0.0, 2300.0, 99.9, None),  # darunter ist die Spannung nicht brauchbar
        (0.0, 2300.0, None, None),
        (None, 2300.0, 230.0, None),
        (0.0, None, 230.0, None),
    ],
)
def test_battery_feed_needs_all_three_values(cfg, ac_in, ac_out, volts, expected) -> None:
    values = {
        "vebus.l1.ac_in_power_w": ac_in,
        "vebus.l1.ac_out_power_w": ac_out,
        "grid.l1.voltage_v": volts,
    }
    grid, _, _ = obs(cfg, {name: value for name, value in values.items() if value is not None})
    assert grid.battery_feed_a["L1"] == expected


BAD_NUMBERS = [math.nan, math.inf, -math.inf, True, "230"]


@pytest.mark.parametrize("bad", BAD_NUMBERS, ids=["nan", "inf", "-inf", "bool", "text"])
def test_grid_values_that_are_no_finite_number_are_unknown(cfg, bad) -> None:
    grid, _, _ = obs(
        cfg,
        {
            "grid.l1.power_w": bad,
            "grid.l1.voltage_v": 230.0,
            "grid.power_w": bad,
            "vebus.l1.ac_in_power_w": bad,
            "vebus.l1.ac_out_power_w": 2800.0,
            "battery.power_w": bad,
            "battery.soc_pct": bad,
        },
    )
    assert grid == GridObs(UNKNOWN_PHASES, UNKNOWN_PHASES, None, None, None)


# Weitere Fälle: Qualität der Werte

EVERYTHING = GRID | {
    "vebus.l1.ac_in_power_w": 500.0,
    "vebus.l1.ac_out_power_w": 2800.0,
    "wallbox.evcs.status": 2,
    "wallbox.evcs.power_w": 3680.0,
    "wallbox.evcs.current_a": 16.0,
    "wallbox.twc.vehicle_connected": True,
    "wallbox.twc.contactor_closed": True,
    "wallbox.twc.power_w": 5520.0,
    "wallbox.twc.l1.current_a": 8.0,
    "wallbox.twc.l2.current_a": 8.0,
    "wallbox.twc.l3.current_a": 8.0,
    "vehicle.tesla.plugged": True,
    "vehicle.tesla.charging_state": "Charging",
    "vehicle.tesla.online": True,
    "vehicle.tesla.at_home": True,
    "vehicle.tesla.actual_current_a": 11.0,
    "vehicle.tesla.phases": 3,
}


def test_every_value_is_read_with_quality_ok(cfg) -> None:
    grid, wallboxes, vehicles = obs(cfg, EVERYTHING)
    assert grid == GridObs(
        {"L1": 2.0, "L2": -1.0, "L3": 0.0},
        {"L1": 10.0, "L2": None, "L3": None},
        230.0,
        -1200.0,
        55.0,
    )
    assert wallboxes == {
        "evcs": WallboxObs(True, True, True, False, {"L1": 16.0, "L2": 0.0, "L3": 0.0}, 3680.0),
        "twc": WallboxObs(True, True, True, None, {"L1": 8.0, "L2": 8.0, "L3": 8.0}, 5520.0),
    }
    assert vehicles == {
        "tesla": VehicleObs(True, True, True, False, False, True, 11.0, 3, T0),
        "egolf": NO_VEHICLE_DATA,
    }


@pytest.mark.parametrize("quality", [Quality.STALE, Quality.INVALID])
def test_values_without_quality_ok_are_unknown(cfg, quality) -> None:
    stale = snap(EVERYTHING, quality=quality)
    assert observe(stale, derive(stale, cfg), cfg, lambda source: True) == obs(cfg, {})


# Weitere Fälle: EVCS


def test_evcs_status_constants() -> None:
    assert (EVCS_DISCONNECTED, EVCS_CHARGING, EVCS_CHARGED) == (0, 2, 3)


def test_evcs_status_may_be_a_float(cfg) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.evcs.status": 2.0})
    assert wallboxes["evcs"].charging is True


@pytest.mark.parametrize("bad", BAD_NUMBERS, ids=["nan", "inf", "-inf", "bool", "text"])
def test_evcs_status_that_is_no_finite_number_is_unknown(cfg, bad) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.evcs.status": bad})
    evcs = wallboxes["evcs"]
    assert (evcs.connected, evcs.charging, evcs.complete) == (None, None, None)


def test_evcs_current_and_power_come_from_the_derived_values(cfg) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.evcs.power_w": 11040.0, "wallbox.evcs.current_a": 16.0})
    assert wallboxes["evcs"].phase_a == {"L1": 16.0, "L2": 16.0, "L3": 16.0}
    assert wallboxes["evcs"].power_w == 11040.0
    _, two_phases, _ = obs(cfg, {"wallbox.evcs.power_w": 7360.0, "wallbox.evcs.current_a": 16.0})
    assert two_phases["evcs"].phase_a == {"L1": 16.0, "L2": 16.0, "L3": 0.0}


def test_evcs_without_current_has_no_phase_current(cfg) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.evcs.power_w": 3680.0})
    assert (wallboxes["evcs"].phase_a, wallboxes["evcs"].power_w) == (None, 3680.0)


# Weitere Fälle: Wall Connector


CONTACTOR = "wallbox.twc.contactor_closed"


def currents(*amps: float | None) -> dict[str, float]:
    """Die Ströme des Wall Connectors auf L1 bis L3; ein ``None`` lässt die Phase aus."""
    return {f"wallbox.twc.l{n}.current_a": a for n, a in enumerate(amps, start=1) if a is not None}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (None, None), (1, None), ("yes", None)],
    ids=["true", "false", "none", "number", "text"],
)
def test_wall_connector_connected_is_the_vehicle_flag(cfg, value, expected) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.twc.vehicle_connected": value})
    assert wallboxes["twc"].connected is expected


def test_wall_connector_charging_threshold_constant() -> None:
    assert TWC_CHARGING_A == 1.0


@pytest.mark.parametrize(
    ("amps", "expected"),
    [
        ((0.0, 0.0, 0.0), False),
        ((0.3, 0.3, 0.3), False),  # 0,9 A
        ((0.5, 0.0, 0.5), True),  # genau 1 A
        ((0.7, 0.2, 0.1), True),  # 1 A, auch wenn die Summe nach der Rundung knapp darunter läge
        ((16.0, 16.0, 16.0), True),
    ],
)
def test_wall_connector_charges_by_the_sum_of_its_currents(cfg, amps, expected) -> None:
    _, wallboxes, _ = obs(cfg, currents(*amps))
    assert wallboxes["twc"].charging is expected


@pytest.mark.parametrize(("amps", "contactor"), [((0.0, 0.0, 0.0), True), ((8.0, 8.0, 8.0), False)])
def test_wall_connector_with_all_currents_ignores_the_contactor(cfg, amps, contactor) -> None:
    _, wallboxes, _ = obs(cfg, currents(*amps) | {CONTACTOR: contactor})
    assert wallboxes["twc"].charging is (not contactor)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({CONTACTOR: True}, True),
        ({CONTACTOR: False}, False),
        ({}, None),
        ({CONTACTOR: 1}, None),
        # fehlt ein Strom, entscheidet das Schütz, nicht die Summe der übrigen
        (currents(0.0, 0.0, None) | {CONTACTOR: True}, True),
        (currents(5.0, 5.0, None) | {CONTACTOR: False}, False),
    ],
)
def test_wall_connector_without_all_currents_goes_by_the_contactor(cfg, values, expected) -> None:
    _, wallboxes, _ = obs(cfg, values)
    assert wallboxes["twc"].charging is expected


def test_wall_connector_never_reports_complete(cfg) -> None:
    _, wallboxes, _ = obs(cfg, EVERYTHING)
    assert wallboxes["twc"].complete is None


def test_wall_connector_current_and_power(cfg) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.twc.power_w": 5520.0} | currents(8.0, 7.0, 6.0))
    assert wallboxes["twc"].phase_a == {"L1": 8.0, "L2": 7.0, "L3": 6.0}
    assert wallboxes["twc"].power_w == 5520.0


@pytest.mark.parametrize("bad", [math.nan, math.inf], ids=["nan", "inf"])
def test_wall_connector_power_that_is_no_finite_number_is_unknown(cfg, bad) -> None:
    _, wallboxes, _ = obs(cfg, {"wallbox.twc.power_w": bad})
    assert wallboxes["twc"].power_w is None


def test_wall_connector_with_a_missing_current_has_no_phase_current(cfg) -> None:
    _, wallboxes, _ = obs(cfg, currents(8.0, 8.0, None))
    assert wallboxes["twc"].phase_a is None


@pytest.mark.parametrize("bad", [math.nan, math.inf], ids=["nan", "inf"])
def test_a_phase_current_that_is_no_finite_number_makes_all_unknown(cfg, bad) -> None:
    _, wallboxes, _ = obs(cfg, currents(8.0, bad, 8.0))
    assert wallboxes["twc"].phase_a is None


# Weitere Fälle: Erreichbarkeit


@pytest.mark.parametrize(
    ("connected", "wallbox", "vehicle"),
    [(True, True, True), (False, False, False), (None, True, False)],
    ids=["connected", "disconnected", "never registered"],
)
def test_reachability_follows_the_connection(cfg, connected, wallbox, vehicle) -> None:
    _, wallboxes, vehicles = obs(cfg, {}, connected=lambda source: connected)
    assert [wallboxes[key].reachable for key in ("evcs", "twc")] == [wallbox, wallbox]
    assert vehicles["tesla"].reachable is vehicle


def test_only_wallboxes_and_vehicles_with_tessie_ask_for_their_source(cfg) -> None:
    asked: list[str] = []

    def connected(source: str) -> bool | None:
        asked.append(source)
        return True

    obs(cfg, {}, connected=connected)
    assert sorted(asked) == ["evcs", "tessie:tesla", "twc"]


# Weitere Fälle: Fahrzeuge


def test_tessie_values(cfg) -> None:
    _, _, vehicles = obs(
        cfg,
        {
            "vehicle.tesla.plugged": True,
            "vehicle.tesla.charging_state": "Charging",
            "vehicle.tesla.online": True,
            "vehicle.tesla.at_home": False,
            "vehicle.tesla.actual_current_a": 11.0,
            "vehicle.tesla.phases": 3,
        },
    )
    assert vehicles["tesla"] == VehicleObs(True, True, True, False, False, False, 11.0, 3, T0)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("Charging", (True, False)),
        ("Complete", (False, True)),
        ("Stopped", (False, False)),
        ("Disconnected", (False, False)),
        ("NoPower", (False, False)),
        (None, (None, None)),
        (5, (None, None)),
    ],
)
def test_tessie_charging_state(cfg, state, expected) -> None:
    _, _, vehicles = obs(cfg, {"vehicle.tesla.charging_state": state})
    assert (vehicles["tesla"].charging, vehicles["tesla"].complete) == expected


@pytest.mark.parametrize(
    ("online", "asleep"), [(True, False), (False, True), (None, None), ("online", None)]
)
def test_tessie_asleep_is_not_online(cfg, online, asleep) -> None:
    _, _, vehicles = obs(cfg, {"vehicle.tesla.online": online})
    assert vehicles["tesla"].asleep is asleep


@pytest.mark.parametrize("signal", ["plugged", "at_home"])
@pytest.mark.parametrize(
    ("value", "expected"), [(True, True), (False, False), (1, None), ("yes", None)]
)
def test_tessie_flags_must_be_booleans(cfg, signal, value, expected) -> None:
    _, _, vehicles = obs(cfg, {f"vehicle.tesla.{signal}": value})
    assert getattr(vehicles["tesla"], signal) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (2, 2),
        (3, 3),
        (3.0, 3),
        (0, None),
        (4, None),
        (2.5, None),
        (math.nan, None),
        (True, None),
        (None, None),
    ],
)
def test_tessie_phase_count_is_one_to_three(cfg, value, expected) -> None:
    _, _, vehicles = obs(cfg, {"vehicle.tesla.phases": value})
    assert vehicles["tesla"].phases == expected
    assert type(vehicles["tesla"].phases) is type(expected)


@pytest.mark.parametrize("bad", BAD_NUMBERS, ids=["nan", "inf", "-inf", "bool", "text"])
def test_tessie_current_that_is_no_finite_number_is_unknown(cfg, bad) -> None:
    _, _, vehicles = obs(cfg, {"vehicle.tesla.actual_current_a": bad})
    assert vehicles["tesla"].actual_current_a is None


def test_vehicle_without_tessie_has_no_data_whatever_its_signals_say(cfg) -> None:
    values = {"vehicle.egolf.plugged": True, "vehicle.egolf.charge_limit_pct": 80.0}
    _, _, vehicles = obs(cfg, values)
    assert vehicles["egolf"] == NO_VEHICLE_DATA
    assert (NO_VEHICLE_DATA.reachable, NO_VEHICLE_DATA.plugged) == (False, None)


def test_observations_keep_the_order_of_the_configuration(cfg) -> None:
    _, wallboxes, vehicles = obs(cfg, {})
    assert list(wallboxes) == list(cfg.wallboxes)
    assert list(vehicles) == list(cfg.vehicles)


# Weitere Fälle: die Eingaben eines Zyklus


@pytest.mark.parametrize(
    ("cls", "names"),
    [
        (
            WallboxObs,
            ["reachable", "connected", "charging", "complete", "phase_a", "power_w", "total_kwh"],
        ),
        (
            VehicleObs,
            [
                "reachable",
                "plugged",
                "charging",
                "complete",
                "asleep",
                "at_home",
                "actual_current_a",
                "phases",
                "plugged_at",
                "soc_pct",
                "soc_at",
                "charge_limit_pct",
            ],
        ),
        (
            GridObs,
            ["phase_import_a", "battery_feed_a", "grid_w", "battery_w", "soc_pct"],
        ),
        (
            ChargeInputs,
            [
                "now",
                "wall_now",
                "grid",
                "wallboxes",
                "vehicles",
                "present",
                "controllable",
                "wakeable",
                "blocked",
                "intent",
                "accepted_at",
                "homes",
                "commands_hour",
            ],
        ),
    ],
)
def test_the_fields_are_the_agreed_ones(cls, names) -> None:
    assert [field.name for field in fields(cls)] == names


def test_charge_inputs_bundle(cfg) -> None:
    grid, wallboxes, vehicles = obs(cfg, {})
    inputs = ChargeInputs(
        now=T0,
        wall_now=T0 + timedelta(hours=1),
        grid=grid,
        wallboxes=wallboxes,
        vehicles=vehicles,
        present={("wallbox.evcs", CommandKind.CURRENT): 6.0},
        controllable={"wallbox.evcs": True, "vehicle.tesla": False},
        wakeable={"vehicle.tesla": True},
        blocked=frozenset({("vehicle.tesla", CommandKind.CHARGING)}),
        intent=Intent(version=1, control=True),
        accepted_at=T0,
    )
    assert inputs.wallboxes["evcs"] is wallboxes["evcs"]
    assert ("vehicle.tesla", CommandKind.CHARGING) in inputs.blocked
    assert inputs.homes == {}  # ohne Angabe: kein Fahrzeug mit home_entity
    with pytest.raises(FrozenInstanceError):
        inputs.now = T0 + timedelta(seconds=1)
    with pytest.raises(FrozenInstanceError):
        NO_VEHICLE_DATA.reachable = True


# Zuordnung (Plan 2c-2b): Zustand aus Home Assistant und Zeitstempel der Tessie-Werte


@pytest.mark.parametrize(
    ("state", "expected"),
    [("home", True), ("not_home", False), ("Arbeit", False), (True, None), (None, None)],
)
def test_home_comes_from_home_assistant(cfg, state, expected) -> None:
    assert homes(snap({"vehicle.egolf.home_state": state}), cfg) == {"egolf": expected}


def test_a_stale_home_state_is_unknown(cfg) -> None:
    stale = snap({"vehicle.egolf.home_state": "home"}, quality=Quality.STALE)
    assert homes(stale, cfg) == {"egolf": None}


def test_without_a_home_state_the_vehicle_is_still_listed_as_unknown(cfg) -> None:
    assert homes(snap({}), cfg) == {"egolf": None}


def test_homes_list_the_vehicles_with_a_home_entity_in_the_order_of_the_configuration(cfg) -> None:
    tesla = cfg.vehicles["tesla"].model_copy(update={"home_entity": "device_tracker.tesla"})
    both = cfg.model_copy(update={"vehicles": cfg.vehicles | {"tesla": tesla}})
    values = {"vehicle.tesla.home_state": "not_home", "vehicle.egolf.home_state": "home"}
    # auch ein Fahrzeug mit Tessie, in der Reihenfolge von config.yaml
    assert list(homes(snap(values), both).items()) == [("tesla", False), ("egolf", True)]
    # ohne home_entity fehlt das Fahrzeug, auch wenn sein Signal einen Wert hat
    assert homes(snap(values), cfg) == {"egolf": True}


def test_plugged_at_is_the_time_of_the_tessie_value(cfg) -> None:
    at = T0 + timedelta(seconds=7)
    snap_ = snap({"vehicle.tesla.plugged": False}, ts=at)
    _, _, vehicles = observe(snap_, derive(snap_, cfg), cfg, lambda source: True)
    assert vehicles["tesla"].plugged_at == at
    assert obs(cfg, {"vehicle.tesla.plugged": "ja"})[2]["tesla"].plugged_at is None


def test_plugged_at_is_the_time_of_the_value_not_of_the_snapshot(cfg) -> None:
    at = T0 + timedelta(seconds=7)
    later = replace(snap({"vehicle.tesla.plugged": True}, ts=at), ts=at + timedelta(seconds=60))
    _, _, vehicles = observe(later, derive(later, cfg), cfg, lambda source: True)
    assert vehicles["tesla"].plugged_at == at


def test_wallbox_connected_reads_each_wallbox_as_the_observations_do(cfg) -> None:
    values = {"wallbox.evcs.status": 1, "wallbox.twc.vehicle_connected": False}
    assert wallbox_connected(snap(values), cfg) == {"evcs": True, "twc": False}
    _, wallboxes, _ = obs(cfg, values)
    assert wallbox_connected(snap(values), cfg) == {
        key: wallbox.connected for key, wallbox in wallboxes.items()
    }


def test_wallbox_connected_is_unknown_without_a_reading(cfg) -> None:
    assert wallbox_connected(snap({}), cfg) == {"evcs": None, "twc": None}
    stale = {"wallbox.evcs.status": 0}
    assert wallbox_connected(snap(stale, quality=Quality.STALE), cfg)["evcs"] is None


# Ladestand der Fahrzeuge und Zähler der Wallboxen (Plan 4b)


def test_stale_tessie_value_is_not_measured(cfg) -> None:
    values = {"vehicle.tesla.soc_pct": 60.0, "vehicle.tesla.charge_limit_pct": 80.0}
    stale = snap(values, quality=Quality.STALE)
    _, _, vehicles = observe(stale, derive(stale, cfg), cfg, lambda source: True)
    tesla = vehicles["tesla"]
    assert (tesla.soc_pct, tesla.soc_at, tesla.charge_limit_pct) == (None, None, None)


def test_tessie_soc_and_charge_limit(cfg) -> None:
    at = T0 + timedelta(seconds=7)
    values = {"vehicle.tesla.soc_pct": 61.0, "vehicle.tesla.charge_limit_pct": 80}
    later = replace(snap(values, ts=at), ts=at + timedelta(seconds=60))
    _, _, vehicles = observe(later, derive(later, cfg), cfg, lambda source: True)
    tesla = vehicles["tesla"]
    # soc_at ist der Zeitstempel des Werts, nicht der des Snapshots
    assert (tesla.soc_pct, tesla.soc_at, tesla.charge_limit_pct) == (61.0, at, 80.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, "61", True])
def test_soc_that_is_no_finite_number_is_unknown(cfg, bad) -> None:
    _, _, vehicles = obs(cfg, {"vehicle.tesla.soc_pct": bad, "vehicle.egolf.soc_pct": bad})
    assert (vehicles["tesla"].soc_pct, vehicles["tesla"].soc_at) == (None, None)
    assert vehicles["egolf"] == NO_VEHICLE_DATA


def test_vehicle_without_tessie_has_its_soc_from_home_assistant(cfg) -> None:
    at = T0 + timedelta(seconds=7)
    _, _, vehicles = obs(cfg, {})
    assert vehicles["egolf"].soc_pct is None
    later = replace(snap({"vehicle.egolf.soc_pct": 48.0}, ts=at), ts=at + timedelta(hours=2))
    _, _, vehicles = observe(later, derive(later, cfg), cfg, lambda source: True)
    # sonst sieht die Regelung von ihm nichts
    assert vehicles["egolf"] == replace(NO_VEHICLE_DATA, soc_pct=48.0, soc_at=at)
    stale = snap({"vehicle.egolf.soc_pct": 48.0}, quality=Quality.STALE)
    assert observe(stale, derive(stale, cfg), cfg, lambda source: True)[2]["egolf"] == (
        NO_VEHICLE_DATA
    )


def test_wallbox_total_kwh(cfg) -> None:
    values = {"wallbox.evcs.total_kwh": 1234.5, "wallbox.twc.total_kwh": 12}
    _, wallboxes, _ = obs(cfg, values)
    assert (wallboxes["evcs"].total_kwh, wallboxes["twc"].total_kwh) == (1234.5, 12.0)
    stale = snap(values, quality=Quality.STALE)
    _, wallboxes, _ = observe(stale, derive(stale, cfg), cfg, lambda source: True)
    assert (wallboxes["evcs"].total_kwh, wallboxes["twc"].total_kwh) == (None, None)
