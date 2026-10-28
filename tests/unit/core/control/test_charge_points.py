from dataclasses import replace

import pytest
from tests.unit.core.control.charge_helpers import evcs, inputs, tesla, twc

from bindaems.core.control.assignment import AssignmentKind, VehicleState
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charge_points import (
    ChargePoint,
    can_charge,
    cars_w,
    charge_points,
    enough,
    holding_a,
    now_capped,
    reserved_a,
    wanted_a,
)
from bindaems.core.safety.limits import HardLimits

TWC_OUT = twc(reachable=False, connected=None, charging=None, phase_a=None, power_w=None)


def state(
    wallbox: str | None,
    kind: AssignmentKind = AssignmentKind.DEFAULT,
    *,
    plugged: bool | None = True,
    complete: bool = False,
) -> VehicleState:
    return VehicleState(wallbox, kind, plugged, False, complete, False, None)


def build(cfg, vehicles, case=None):
    """Die Ladepunkte für ``vehicles`` (Tesla vor e-Golf) und die Eingaben ``case``."""
    case = inputs(cfg) if case is None else case
    return charge_points(cfg, HardLimits.from_config(cfg), ["tesla", "egolf"], vehicles, case)


def test_evcs_point_uses_the_vehicle_phases_and_range(cfg) -> None:
    vehicles = {"tesla": state("twc"), "egolf": state("evcs")}
    points = charge_points(
        cfg, HardLimits.from_config(cfg), ["tesla", "egolf"], vehicles, inputs(cfg)
    )
    egolf = points["egolf"]
    assert (egolf.actuator, egolf.evcs, egolf.phases, egolf.min_a, egolf.max_a) == (
        "wallbox.evcs",
        True,
        ("L1", "L2"),
        6,
        16,
    )
    assert egolf.regulated and not points["tesla"].regulated  # Tesla weder steuerbar noch weckbar


def test_one_wallbox_is_one_point_for_the_first_by_priority(cfg) -> None:
    vehicles = {"tesla": state("evcs"), "egolf": state("evcs")}
    points = charge_points(
        cfg, HardLimits.from_config(cfg), ["egolf", "tesla"], vehicles, inputs(cfg)
    )
    assert list(points) == ["egolf"]


def test_unreachable_evcs_reserves_its_safe_current(cfg) -> None:
    reserved = reserved_a(cfg, [], {"evcs": evcs(reachable=False), "twc": twc()})
    assert reserved == {"L1": 6.0, "L2": 6.0, "L3": 6.0}


def test_a_vehicle_without_a_wallbox_has_no_point(cfg) -> None:
    points = build(cfg, {"tesla": state(None), "egolf": state("evcs")})
    assert list(points) == ["egolf"]


@pytest.mark.parametrize(
    "case",
    [{"controllable": {"vehicle.tesla": True}}, {"wakeable": {"vehicle.tesla": True}}],
    ids=["steerable", "wakeable"],
)
def test_the_tesla_at_the_wall_connector_is_regulated_when_it_can_be_steered_or_woken(
    cfg, case
) -> None:
    points = build(cfg, {"tesla": state("twc"), "egolf": state(None)}, inputs(cfg, **case))
    tesla_point = points["tesla"]
    assert (
        tesla_point.actuator,
        tesla_point.evcs,
        tesla_point.phases,
        tesla_point.min_a,
        tesla_point.max_a,
    ) == ("vehicle.tesla", False, ("L1", "L2", "L3"), 5, 16)
    assert tesla_point.regulated


def test_an_unclear_assignment_pauses_the_tesla_and_gives_the_whole_evcs(cfg) -> None:
    both = {"wallbox.evcs": True, "vehicle.tesla": True}
    vehicles = {
        "tesla": state("twc", AssignmentKind.UNCLEAR),
        "egolf": state("evcs", AssignmentKind.UNCLEAR),
    }
    points = build(cfg, vehicles, inputs(cfg, controllable=both))
    assert not points["tesla"].regulated  # pausiert, solange die Zuordnung unklar ist
    egolf = points["egolf"]
    assert egolf.regulated and egolf.phases == ("L1", "L2", "L3")  # nicht nur die 2 Phasen


def test_tessie_measures_the_tesla_while_its_wall_connector_is_out(cfg) -> None:
    vehicles = {"tesla": state("twc"), "egolf": state(None)}
    steerable = {"controllable": {"vehicle.tesla": True}}
    known = inputs(
        cfg, twc_obs=TWC_OUT, tesla_obs=tesla(plugged=True, actual_current_a=8.0), **steerable
    )
    point = build(cfg, vehicles, known)["tesla"]
    assert point.measured == {"L1": 8.0, "L2": 8.0, "L3": 8.0} and point.regulated
    on_one_phase = tesla(plugged=True, actual_current_a=8.0, phases=1)
    known = inputs(cfg, twc_obs=TWC_OUT, tesla_obs=on_one_phase, **steerable)
    assert build(cfg, vehicles, known)["tesla"].measured == {"L1": 8.0}  # nur die Phasen, die lädt
    unknown = inputs(cfg, twc_obs=TWC_OUT, tesla_obs=tesla(reachable=False), **steerable)
    point = build(cfg, vehicles, unknown)["tesla"]
    assert point.measured is None and not point.regulated  # ohne Strom aus Tessie ungeregelt


def test_a_wall_connector_that_is_out_without_tessie_reserves_the_maximum(cfg) -> None:
    case = inputs(cfg, twc_obs=TWC_OUT, tesla_obs=tesla(reachable=False))
    points = build(cfg, {"tesla": state("twc"), "egolf": state(None)}, case)
    reserved = reserved_a(cfg, list(points.values()), case.wallboxes)
    assert reserved == {"L1": 16.0, "L2": 16.0, "L3": 16.0}


def test_an_evcs_that_is_out_reserves_nothing_while_tessie_regulates_its_point(cfg) -> None:
    at_the_evcs = state("evcs", AssignmentKind.OVERRIDE)
    case = inputs(
        cfg, evcs_obs=evcs(reachable=False), tesla_obs=tesla(plugged=True, actual_current_a=8.0)
    )
    points = build(cfg, {"tesla": at_the_evcs, "egolf": state(None)}, case)
    assert points["tesla"].regulated  # nicht erreichbar, aber Tessie kennt den Strom
    reserved = reserved_a(cfg, list(points.values()), case.wallboxes)
    assert reserved == {"L1": 0.0, "L2": 0.0, "L3": 0.0}


@pytest.mark.parametrize(
    ("plugged", "complete", "mode", "expected"),
    [
        (True, False, Effective.PV, True),
        (None, False, Effective.PV, True),  # unbekannter Ansteckzustand: Es kann laden
        (False, False, Effective.PV, False),  # abgesteckt
        (True, True, Effective.NOW, False),  # „geladen“
        (True, False, Effective.OFF, False),
        (True, False, Effective.BOOST, True),
    ],
)
def test_a_car_takes_part_in_the_surplus_unless_it_cannot_charge(
    plugged: bool | None, complete: bool, mode: Effective, expected: bool
) -> None:
    assert can_charge(state("evcs", plugged=plugged, complete=complete), mode) is expected


def test_a_planned_car_at_its_target_takes_no_part_in_the_surplus() -> None:
    assert can_charge(state("evcs"), Effective.PLANNED)
    assert not can_charge(state("evcs"), Effective.PLANNED, reached=True)


def test_the_power_of_the_cars_is_unknown_when_one_wallbox_has_none(cfg) -> None:
    case = inputs(
        cfg,
        evcs_obs=evcs(power_w=1400.0),
        twc_obs=twc(power_w=2300.0),
        controllable={"wallbox.evcs": True, "vehicle.tesla": True},
    )
    points = list(build(cfg, {"tesla": state("twc"), "egolf": state("evcs")}, case).values())
    assert cars_w(points, case.wallboxes) == 3700.0
    assert cars_w([], case.wallboxes) == 0.0
    silent = {**case.wallboxes, "evcs": evcs(power_w=None)}
    assert cars_w(points, silent) is None


def test_the_wish_of_a_car_follows_its_mode(cfg) -> None:
    egolf = build(cfg, {"tesla": state(None), "egolf": state("evcs")})["egolf"]
    assert wanted_a(egolf, Effective.NOW, 8) == 16  # Sofort und Boost: max_a
    assert wanted_a(egolf, Effective.BOOST, None) == 16
    assert wanted_a(egolf, Effective.PV, 10) == 10  # PV: der Anteil, ohne Wert 0
    assert wanted_a(egolf, Effective.PV, None) == 0
    assert wanted_a(egolf, Effective.MIN_PV, 4) == 6  # Min+PV: der Anteil, mindestens min_a
    assert wanted_a(egolf, Effective.MIN_PV, 12) == 12


def test_the_wish_of_a_planned_car_is_its_firm_demand_or_its_share(cfg) -> None:
    egolf = build(cfg, {"tesla": state(None), "egolf": state("evcs")})["egolf"]
    planned = Effective.PLANNED
    assert wanted_a(egolf, planned, 10) == 10  # ohne festen Bedarf wie PV
    assert wanted_a(egolf, planned, None) == 0
    # fester Bedarf 4600 W auf 2 Phasen: 10 A, ein größerer Anteil geht vor
    assert wanted_a(egolf, planned, 3, reserved_w=4600.0, voltage_v=230.0) == 10
    assert wanted_a(egolf, planned, 12, reserved_w=4600.0, voltage_v=230.0) == 12
    assert wanted_a(egolf, planned, None, reserved_w=4601.0, voltage_v=230.0) == 11  # aufgerundet
    # begrenzt auf min_a und max_a; die volle Leistung ergibt genau max_a
    assert wanted_a(egolf, planned, None, reserved_w=500.0, voltage_v=230.0) == 6
    assert wanted_a(egolf, planned, None, reserved_w=20000.0, voltage_v=230.0) == 16
    assert wanted_a(egolf, planned, None, reserved_w=16 * 230.0 * 2, voltage_v=230.0) == 16


def test_a_point_holds_its_largest_measured_current(cfg) -> None:
    vehicles = {"tesla": state(None), "egolf": state("evcs")}
    measured = evcs(phase_a={"L1": 6.0, "L2": 9.0, "L3": 0.0})
    assert holding_a(build(cfg, vehicles, inputs(cfg, evcs_obs=measured))["egolf"]) == 9.0
    unknown = evcs(phase_a=None)
    assert holding_a(build(cfg, vehicles, inputs(cfg, evcs_obs=unknown))["egolf"]) == 0.0


def test_share_and_free_current_must_both_carry_the_minimum(cfg) -> None:
    egolf = build(cfg, {"tesla": state(None), "egolf": state("evcs")})["egolf"]
    assert enough(egolf, 6, 6.0) is True
    assert enough(egolf, 5, 20.0) is False  # der Anteil trägt den Mindeststrom nicht
    assert enough(egolf, 12, 5.9) is False  # der freie Strom trägt ihn nicht
    assert enough(egolf, None, 20.0) is None  # unbekannt, wenn eins davon fehlt
    assert enough(egolf, 12, None) is None
    assert enough(None, 12, 20.0) is None


# Plan 2c-2b: der Gast (Präzisierung 6)


def test_a_guest_gets_the_whole_evcs_and_is_load_at_the_wall_connector(cfg) -> None:
    vehicles = {
        "tesla": state(None),
        "egolf": state(None),
        "guest@evcs": state("evcs"),
        "guest@twc": state("twc"),
    }
    points = charge_points(
        cfg,
        HardLimits.from_config(cfg),
        list(vehicles),
        vehicles,
        inputs(cfg, twc_obs=twc(connected=True)),
    )
    guest = points["guest@evcs"]
    assert (guest.actuator, guest.phases, guest.min_a, guest.max_a, guest.regulated) == (
        "wallbox.evcs",
        ("L1", "L2", "L3"),
        6,
        16,
        True,
    )
    assert (points["guest@twc"].actuator, points["guest@twc"].regulated) == (None, False)


# Plan Sofort-Limit: Höchststrom in „Sofort“


def make_point(**kw) -> ChargePoint:
    """Ein geregelter Ladepunkt an der EVCS auf zwei Phasen; ``kw`` ändert Felder."""
    base = ChargePoint("egolf", "evcs", "wallbox.evcs", True, ("L1", "L2"), 6, 16, None, True)
    return replace(base, **kw)


def test_now_capped_lowers_max_a_but_never_below_min_a() -> None:
    point = make_point(min_a=6, max_a=16)
    assert now_capped(point, None) is point
    assert now_capped(point, 10).max_a == 10
    assert now_capped(point, 5).max_a == 6  # Mindeststrom der EVCS geht vor
    assert now_capped(point, 20).max_a == 16  # nie über den Ladepunkt
    assert now_capped(point, 10) == replace(point, max_a=10)  # sonst bleibt alles
