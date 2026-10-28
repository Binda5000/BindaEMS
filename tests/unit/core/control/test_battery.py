import math
from datetime import datetime, timedelta
from typing import TypedDict

import pytest
from tests.helpers import T0

from bindaems.core.control.battery import (
    BRIDGE_END_S,
    SPARE_MODES,
    SUPPORT_MODES,
    BatteryCar,
    BatteryNeed,
    Spare,
    Support,
    allowed,
    deficits,
    effective_threshold,
)
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.shared.config import ControlConfig
from bindaems.shared.control import SupportIntent
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind


def car(
    wallbox: str = "evcs",
    mode: Effective = Effective.PV,
    power: float | None = 4000.0,
    charging: bool | None = True,
) -> BatteryCar:
    return BatteryCar(wallbox, mode, power, charging)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Cycle(TypedDict):
    """Schwelle und Schalter eines Zyklus für ``Support.update``."""

    threshold_pct: float
    allowed: bool
    plan_blocks: bool


# freie Entladeleistung, die in den Tests vor der Fix-Welle F3 nie begrenzt
ROOM_W = 1_000_000.0
CARS = [car(mode=Effective.MIN_PV)]  # ein Auto, für das die Unterstützung gelten kann
DEFICIT = {"evcs": 500.0}  # weit über der Schwelle von 100 W
AT_70 = Cycle(
    threshold_pct=70.0, allowed=True, plan_blocks=False
)  # die Schwelle aus der Konfiguration, erlaubt


def test_constants() -> None:
    assert BRIDGE_END_S == 60.0
    assert SUPPORT_MODES == {Effective.PV, Effective.MIN_PV}
    assert SPARE_MODES == {Effective.NOW}


# --- Fehlbetrag -----------------------------------------------------------------------------------


def test_pv_covers_the_house_first_then_the_cars_by_priority() -> None:
    cars = [car("twc", Effective.PV, 4000.0), car("evcs", Effective.NOW, 3000.0)]
    assert deficits(6000.0, 1000.0, cars) == {"twc": 0.0, "evcs": 2000.0}


def test_sunny_car_has_no_deficit() -> None:  # Design 5.1: 6 kW PV, 1 kW Haus, 5 kW Auto
    assert deficits(6000.0, 1000.0, [car(power=5000.0)]) == {"evcs": 0.0}


def test_the_house_takes_the_pv_before_any_car() -> None:
    assert deficits(1500.0, 2000.0, [car(power=3000.0)]) == {"evcs": 3000.0}
    assert deficits(2500.0, 2000.0, [car(power=3000.0)]) == {"evcs": 2500.0}


def test_the_rest_of_the_pv_goes_to_the_cars_in_order() -> None:
    cars = [car("c", power=1000.0), car("a", power=1000.0), car("b", power=1000.0)]
    found = deficits(1500.0, 0.0, cars)
    assert found == {"c": 0.0, "a": 500.0, "b": 1000.0}
    assert list(found) == ["c", "a", "b"]  # in der Reihenfolge von cars


def test_no_cars_have_no_deficits() -> None:
    assert deficits(6000.0, 1000.0, []) == {}


def test_unknown_power_of_a_charging_car_makes_the_need_unknown() -> None:  # Review Focus 3
    assert deficits(6000.0, 1000.0, [car(power=None, charging=None)]) is None
    assert deficits(6000.0, 1000.0, [car(power=None, charging=False)]) == {"evcs": 0.0}


def test_unknown_power_makes_the_whole_need_unknown_wherever_the_car_stands() -> None:
    known = [car("a", power=1000.0), car("b", power=1000.0)]
    for index in range(len(known) + 1):
        cars = [*known[:index], car("x", power=None, charging=True), *known[index:]]
        assert deficits(6000.0, 1000.0, cars) is None


def test_a_car_that_surely_does_not_charge_takes_no_pv() -> None:
    cars = [car("a", power=None, charging=False), car("b", power=2000.0)]
    assert deficits(2000.0, 0.0, cars) == {"a": 0.0, "b": 0.0}


def test_a_known_power_counts_whatever_the_charging_state_says() -> None:
    for charging in (True, False, None):
        assert deficits(0.0, 0.0, [car(power=1500.0, charging=charging)]) == {"evcs": 1500.0}


def test_a_power_that_is_not_a_finite_number_is_unknown() -> None:
    for power in (math.nan, math.inf, -math.inf):
        assert deficits(0.0, 0.0, [car(power=power, charging=None)]) is None
        assert deficits(0.0, 0.0, [car(power=power, charging=False)]) == {"evcs": 0.0}


def test_negative_pv_and_house_count_as_zero() -> None:
    assert deficits(-20.0, -5.0, [car(power=1000.0)]) == {"evcs": 1000.0}


def test_unknown_pv_or_house_makes_the_need_unknown() -> None:  # Review Focus 3
    for unknown in (None, math.nan, math.inf, -math.inf):
        assert deficits(unknown, 1000.0, [car()]) is None
        assert deficits(6000.0, unknown, [car()]) is None
        assert deficits(unknown, 1000.0, []) is None  # auch ohne Autos


def test_negative_car_power_counts_as_zero() -> None:
    assert deficits(0.0, 0.0, [car(power=-30.0)]) == {"evcs": 0.0}
    cars = [car("a", power=-30.0), car("b", power=1000.0)]  # die −30 W geben nichts zurück
    assert deficits(0.0, 0.0, cars) == {"a": 0.0, "b": 1000.0}


# --- Unterstützung: SOC ---------------------------------------------------------------------------


def test_support_needs_70_percent_and_ends_below_65() -> None:
    support, cars, deficit = Support(ControlConfig()), [car(mode=Effective.MIN_PV)], {"evcs": 500.0}
    socs = [69.9, 70.0, 65.0, 64.9, 69.0, 70.0]
    found = [support.update(at(s), soc, cars, deficit, **AT_70)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "on", "on", "soc", "soc", "on"]


def test_unknown_soc_blocks_without_resetting_the_hysteresis() -> None:
    support = Support(ControlConfig())
    socs = [72.0, None, 66.0]  # „on“, im Zyklus gesperrt, im Band weiter „on“
    found = [support.update(at(s), soc, CARS, DEFICIT, **AT_70)[0] for s, soc in enumerate(socs)]
    assert found == ["on", "soc", "on"]


def test_unknown_soc_does_not_start_the_support_either() -> None:
    support = Support(ControlConfig())
    socs = [None, 66.0, None, 70.0]
    found = [support.update(at(s), soc, CARS, DEFICIT, **AT_70)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "soc", "soc", "on"]


def test_a_soc_that_is_not_a_finite_number_is_unknown() -> None:
    support = Support(ControlConfig())
    socs = [72.0, math.nan, math.inf, -math.inf, 66.0]
    found = [support.update(at(s), soc, CARS, DEFICIT, **AT_70)[0] for s, soc in enumerate(socs)]
    assert found == ["on", "soc", "soc", "soc", "on"]


def test_soc_thresholds_follow_the_threshold_and_the_config() -> None:
    support = Support(ControlConfig(support_hysteresis_pct=10.0))
    at_50 = Cycle(threshold_pct=50.0, allowed=True, plan_blocks=False)
    socs = [49.9, 50.0, 40.0, 39.9, 49.0, 50.0]
    found = [support.update(at(s), soc, CARS, DEFICIT, **at_50)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "on", "on", "soc", "soc", "on"]


def test_hysteresis_follows_the_moving_threshold() -> None:  # Plan 3c, Präzisierung 8
    support = Support(ControlConfig())
    assert support.update(at(0), 72.0, CARS, DEFICIT, **AT_70)[0] == "on"
    at_80 = Cycle(threshold_pct=80.0, allowed=True, plan_blocks=False)
    assert support.update(at(1), 76.0, CARS, DEFICIT, **at_80)[0] == "on"  # nicht unter 80 − 5
    assert support.update(at(2), 72.0, CARS, DEFICIT, **at_80)[0] == "soc"  # unter 75 %: gesperrt
    assert support.update(at(3), 68.0, CARS, DEFICIT, **AT_70)[0] == "soc"  # erst ab 70 % wieder
    assert support.update(at(4), 70.0, CARS, DEFICIT, **AT_70)[0] == "on"


# --- Unterstützung: Überbrückung ------------------------------------------------------------------


def test_bridging_blocks_after_900_s_until_60_s_without_deficit() -> None:
    support = Support(ControlConfig())
    found = {}
    for second in range(962):
        deficit_w = 500.0 if second < 900 or second == 961 else 0.0
        found[second] = support.update(at(second), 80.0, CARS, {"evcs": deficit_w}, **AT_70)
    assert found[0] == ("on", 0.0)
    assert found[899] == ("on", 899.0)
    assert found[900] == ("time", 900.0)
    assert found[959] == ("time", 959.0)
    assert found[960] == ("on", None)  # 60 s ohne Fehlbetrag: Die Überbrückung ist beendet
    assert found[961] == ("on", 0.0)  # und mit dem nächsten Fehlbetrag beginnt eine neue
    assert all(found[s] == ("on", float(s)) for s in range(900))
    assert all(found[s] == ("time", float(s)) for s in range(900, 960))


def test_time_blocks_for_as_long_as_the_deficit_lasts() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, DEFICIT, **AT_70) == ("on", 0.0)
    assert support.update(at(5000), 80.0, CARS, DEFICIT, **AT_70) == ("time", 5000.0)


def test_the_longest_bridging_follows_the_config() -> None:
    support = Support(ControlConfig(support_max_s=30.0))
    assert support.update(at(0), 80.0, CARS, DEFICIT, **AT_70) == ("on", 0.0)
    assert support.update(at(29), 80.0, CARS, DEFICIT, **AT_70) == ("on", 29.0)
    assert support.update(at(30), 80.0, CARS, DEFICIT, **AT_70) == ("time", 30.0)


def test_a_deficit_within_60_s_continues_the_bridging_and_restarts_the_count() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, {"evcs": 500.0}, **AT_70) == ("on", 0.0)
    assert support.update(at(10), 80.0, CARS, {"evcs": 0.0}, **AT_70) == ("on", 10.0)  # läuft noch
    assert support.update(at(69), 80.0, CARS, {"evcs": 0.0}, **AT_70) == ("on", 69.0)  # 59 s ohne
    assert support.update(at(70), 80.0, CARS, {"evcs": 500.0}, **AT_70) == ("on", 70.0)  # dieselbe
    assert support.update(at(71), 80.0, CARS, {"evcs": 0.0}, **AT_70) == (
        "on",
        71.0,
    )  # die 60 s von vorn
    assert support.update(at(130), 80.0, CARS, {"evcs": 0.0}, **AT_70) == ("on", 130.0)  # 59 s ohne
    assert support.update(at(131), 80.0, CARS, {"evcs": 0.0}, **AT_70) == ("on", None)  # 60 s ohne


def test_small_deficits_neither_start_nor_extend_the_bridging() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, {"evcs": 99.0}, **AT_70) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 100.0}, **AT_70) == ("on", 0.0)
    # 99 W gelten als „ohne Fehlbetrag“: Nach 60 s davon ist die Überbrückung beendet.
    assert support.update(at(2), 80.0, CARS, {"evcs": 99.0}, **AT_70) == ("on", 1.0)
    assert support.update(at(61), 80.0, CARS, {"evcs": 99.0}, **AT_70) == ("on", 60.0)
    assert support.update(at(62), 80.0, CARS, {"evcs": 99.0}, **AT_70) == ("on", None)


def test_the_deficit_threshold_is_half_the_setpoint_deadband() -> None:
    support = Support(ControlConfig(setpoint_deadband_w=400.0))
    assert support.update(at(0), 80.0, CARS, {"evcs": 199.0}, **AT_70) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 200.0}, **AT_70) == ("on", 0.0)


def test_a_deficit_has_to_be_positive_even_with_the_smallest_deadband() -> None:
    # Das Totband liegt immer über 0 (``ControlConfig``); die Schwelle ist seine Hälfte.
    support = Support(ControlConfig(setpoint_deadband_w=0.1))
    assert support.update(at(0), 80.0, CARS, {"evcs": 0.0}, **AT_70) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 0.05}, **AT_70) == ("on", 0.0)


def test_the_deficits_of_the_pv_and_min_pv_cars_add_up() -> None:
    support = Support(ControlConfig())
    cars = [car("a", Effective.PV), car("b", Effective.MIN_PV)]
    assert support.update(at(0), 80.0, cars, {"a": 50.0, "b": 49.0}, **AT_70) == ("on", None)
    assert support.update(at(1), 80.0, cars, {"a": 50.0, "b": 50.0}, **AT_70) == ("on", 0.0)


def test_only_cars_in_pv_or_min_pv_count_for_the_bridging() -> None:
    cars = [
        car("a", Effective.PV),
        car("b", Effective.BOOST),
        car("c", Effective.NOW),
        car("d", Effective.OFF),
    ]
    deficit = {"a": 0.0, "b": 5000.0, "c": 4000.0, "d": 3000.0}
    assert Support(ControlConfig()).update(at(0), 80.0, cars, deficit, **AT_70) == ("on", None)


def test_a_car_without_an_entry_has_no_deficit() -> None:
    assert Support(ControlConfig()).update(at(0), 80.0, CARS, {}, **AT_70) == ("on", None)


# --- Unterstützung: Stand und Neubeginn -----------------------------------------------------------


def test_without_pv_or_min_pv_cars_the_reason_is_mode() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, [car(mode=Effective.NOW)], DEFICIT, **AT_70) == (
        "mode",
        None,
    )


def test_only_pv_and_min_pv_cars_make_the_support_possible() -> None:
    for mode in Effective:
        found = Support(ControlConfig()).update(at(0), 80.0, [car(mode=mode)], DEFICIT, **AT_70)[0]
        assert found == ("on" if mode in SUPPORT_MODES else "mode")
    assert Support(ControlConfig()).update(at(0), 80.0, [], {}, **AT_70) == ("mode", None)


def test_the_reason_names_mode_before_soc_before_time() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, DEFICIT, **AT_70) == ("on", 0.0)
    assert support.update(at(900), 80.0, CARS, DEFICIT, **AT_70) == ("time", 900.0)
    assert support.update(at(901), 60.0, CARS, DEFICIT, **AT_70) == (
        "soc",
        901.0,
    )  # SOC und Zeit sperren
    now_only = [car(mode=Effective.NOW)]
    assert support.update(at(902), 60.0, now_only, DEFICIT, **AT_70) == (
        "mode",
        902.0,
    )  # dazu der Modus


def test_support_switched_off_is_blocked_with_reason_off() -> None:  # Plan 3c, Präzisierung 8
    support = Support(ControlConfig())
    off = Cycle(threshold_pct=70.0, allowed=False, plan_blocks=False)
    assert support.update(T0, 90.0, CARS, DEFICIT, **off) == ("off", 0.0)
    # Hysterese und Überbrückung laufen mit: Wieder erlaubt, gilt sie sofort, in derselben
    # Überbrückung.
    assert support.update(at(1), 68.0, CARS, DEFICIT, **AT_70) == ("on", 1.0)


def test_update_needs_plan_blocks_explicitly() -> None:
    with pytest.raises(TypeError):
        Support(ControlConfig()).update(  # type: ignore[call-arg]
            T0, 90.0, CARS, DEFICIT, threshold_pct=70.0, allowed=True
        )


def test_the_plan_blocks_the_support_with_reason_plan() -> None:  # Plan 3c, Präzisierung 8
    support = Support(ControlConfig())
    assert support.update(T0, 90.0, CARS, DEFICIT, **{**AT_70, "plan_blocks": True}) == (
        "plan",
        0.0,
    )
    assert support.update(at(1), 68.0, CARS, DEFICIT, **AT_70) == ("on", 1.0)


def test_the_reason_names_mode_off_plan_soc_and_time_in_this_order() -> None:
    support = Support(ControlConfig())
    off = Cycle(threshold_pct=70.0, allowed=False, plan_blocks=False)
    now_only = [car(mode=Effective.NOW)]
    assert (
        support.update(at(0), 60.0, now_only, DEFICIT, **{**off, "plan_blocks": True})[0] == "mode"
    )
    assert support.update(at(1), 60.0, CARS, DEFICIT, **{**off, "plan_blocks": True})[0] == "off"
    assert support.update(at(2), 60.0, CARS, DEFICIT, **{**AT_70, "plan_blocks": True})[0] == "plan"
    assert support.update(at(3), 60.0, CARS, DEFICIT, **AT_70)[0] == "soc"


def test_the_soc_state_follows_the_cycles_without_a_support_car() -> None:
    support = Support(ControlConfig())
    now_only = [car(mode=Effective.NOW)]
    assert support.update(at(0), 72.0, CARS, DEFICIT, **AT_70)[0] == "on"
    assert support.update(at(1), 60.0, now_only, DEFICIT, **AT_70)[0] == "mode"  # fällt unter 65 %
    assert support.update(at(2), 68.0, CARS, DEFICIT, **AT_70)[0] == "soc"  # erst ab 70 % wieder


def test_reset_starts_over_with_the_soc_and_the_bridging() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 72.0, CARS, DEFICIT, **AT_70) == ("on", 0.0)
    assert support.update(at(1000), 66.0, CARS, DEFICIT, **AT_70) == ("time", 1000.0)
    support.reset()
    assert support.update(at(1001), 66.0, CARS, DEFICIT, **AT_70) == (
        "soc",
        0.0,
    )  # 70 % wieder erreichen
    assert support.update(at(1002), 70.0, CARS, DEFICIT, **AT_70) == (
        "on",
        1.0,
    )  # neue Überbrückung


# --- Erlaubter Akku-Anteil ------------------------------------------------------------------------


def test_allowed_share_follows_the_mode_and_the_limit() -> None:
    cars = [
        car("a", Effective.PV),
        car("b", Effective.MIN_PV),
        car("c", Effective.NOW),
        car("d", Effective.BOOST),
    ]
    deficit = {"a": 2000.0, "b": 2000.0, "c": 4000.0, "d": 5000.0}
    on = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W)
    off = allowed(cars, deficit, support_on=False, support_max_w=3000.0, room_w=ROOM_W)
    assert on == {"a": 2000.0, "b": 1000.0, "c": 0.0, "d": 5000.0}
    assert off == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 5000.0}


def test_planned_gets_no_battery_support() -> None:  # Plan 4b, Entscheidung 4, Präzisierung 8
    assert SUPPORT_MODES == frozenset({Effective.PV, Effective.MIN_PV})
    cars = [car("a", Effective.PLANNED), car("b", Effective.PV)]
    deficit = {"a": 2000.0, "b": 1000.0}
    found = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W)
    assert found == {"a": 0.0, "b": 1000.0}
    # Sein Fehlbetrag geht ganz in den gewollten Netzbezug W, wie bei Sofort
    need = BatteryNeed(0.0, 0.0, 8000.0, deficit, found, "on", 0.0)
    assert (need.grid_w, need.wanted_w) == ({"a": 2000.0, "b": 0.0}, 2000.0)
    # Ein Auto in „Geplant“ allein macht die Unterstützung nicht möglich
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, [car(mode=Effective.PLANNED)], DEFICIT, **AT_70)[0] == "mode"


def test_off_gets_nothing_from_the_battery() -> None:
    cars = [car("a", Effective.OFF)]
    assert allowed(cars, {"a": 2000.0}, support_on=True, support_max_w=3000.0, room_w=ROOM_W) == {
        "a": 0.0
    }


def test_the_limit_goes_to_the_cars_in_order_of_the_list() -> None:
    cars = [car("a", Effective.MIN_PV), car("b", Effective.PV), car("c", Effective.PV)]
    deficit = {"a": 2500.0, "b": 2500.0, "c": 2500.0}
    found = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W)
    assert found == {"a": 2500.0, "b": 500.0, "c": 0.0}
    assert list(found) == ["a", "b", "c"]


def test_boost_does_not_use_up_the_limit_of_the_others() -> None:
    cars = [car("b", Effective.BOOST), car("a", Effective.PV)]
    deficit = {"b": 5000.0, "a": 2000.0}
    found = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W)
    assert found == {"b": 5000.0, "a": 2000.0}


def test_a_car_never_gets_more_than_its_deficit() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    found = allowed(
        cars, {"a": 800.0, "b": 0.0}, support_on=True, support_max_w=3000.0, room_w=ROOM_W
    )
    assert found == {"a": 800.0, "b": 0.0}


def test_without_a_limit_only_boost_gets_something() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    found = allowed(
        cars, {"a": 800.0, "b": 900.0}, support_on=True, support_max_w=0.0, room_w=ROOM_W
    )
    assert found == {"a": 0.0, "b": 900.0}


def test_a_car_without_an_entry_gets_nothing() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    assert allowed(cars, {}, support_on=True, support_max_w=3000.0, room_w=ROOM_W) == {
        "a": 0.0,
        "b": 0.0,
    }


# --- Akku ins Auto: Geplant und Sofort (Plan Akku ins Auto, Entscheidungen 4 und 6) ---------------


def test_planned_car_gets_its_plan_battery_share() -> None:
    cars = [BatteryCar("evcs", Effective.PLANNED, 7000.0, True, vehicle="tesla")]
    shares = allowed(
        cars,
        {"evcs": 7000.0},
        support_on=False,
        support_max_w=3000,
        room_w=ROOM_W,
        planned_w={"tesla": 2500},
        planned_on=True,
    )
    assert shares == {"evcs": 2500.0}


def test_planned_share_needs_the_switch_the_vehicle_and_its_entry() -> None:
    deficit = {"evcs": 7000.0}
    tesla = [BatteryCar("evcs", Effective.PLANNED, 7000.0, True, vehicle="tesla")]
    no_vehicle = [BatteryCar("evcs", Effective.PLANNED, 7000.0, True)]
    plan = {"tesla": 2500}
    off = allowed(
        tesla, deficit, support_on=True, support_max_w=3000, room_w=ROOM_W, planned_w=plan
    )
    assert off == {"evcs": 0.0}  # ohne ``planned_on`` nichts
    assert allowed(
        no_vehicle,
        deficit,
        support_on=True,
        support_max_w=3000,
        room_w=ROOM_W,
        planned_w=plan,
        planned_on=True,
    ) == {"evcs": 0.0}
    assert allowed(
        tesla,
        deficit,
        support_on=True,
        support_max_w=3000,
        room_w=ROOM_W,
        planned_w={"egolf": 2500},
        planned_on=True,
    ) == {"evcs": 0.0}


def test_planned_share_never_exceeds_the_deficit() -> None:
    cars = [BatteryCar("evcs", Effective.PLANNED, 1800.0, True, vehicle="tesla")]
    shares = allowed(
        cars,
        {"evcs": 1200.0},  # die PV deckt einen Teil
        support_on=False,
        support_max_w=3000,
        room_w=ROOM_W,
        planned_w={"tesla": 2500},
        planned_on=True,
    )
    assert shares == {"evcs": 1200.0}


# --- Letzte Ampere-Stufe aus dem Akku (Fix-Welle F7, Entscheidung des Betreibers 28.10.2026) ------

EGOLF_STEP_W = 460.0  # 1 A × 230 V × 2 Phasen


def planned_egolf(step_w: float = EGOLF_STEP_W) -> list[BatteryCar]:
    return [BatteryCar("evcs", Effective.PLANNED, 3220.0, True, vehicle="egolf", step_w=step_w)]


def test_planned_car_takes_its_last_amp_step_from_the_battery() -> None:
    # Geplant mit 2800 W, alle aus dem Akku: Der core lädt mit 7 A (3220 W). Der Akku deckt auch
    # die aufgerundete Stufe, höchstens Akkuanteil plus eine Stufe (3260 W).
    def share(deficit_w: float) -> float:
        return allowed(
            planned_egolf(),
            {"evcs": deficit_w},
            support_on=False,
            support_max_w=3000.0,
            room_w=ROOM_W,
            planned_w={"egolf": 2800},
            planned_on=True,
        )["evcs"]

    assert share(3220.0) == 3220.0
    assert share(4000.0) == 3260.0


def test_a_car_without_planned_share_gets_no_step() -> None:
    for planned in ({"egolf": 0}, {}):
        assert allowed(
            planned_egolf(),
            {"evcs": 3220.0},
            support_on=False,
            support_max_w=3000.0,
            room_w=ROOM_W,
            planned_w=planned,
            planned_on=True,
        ) == {"evcs": 0.0}


@pytest.mark.parametrize("step", [0.0, -460.0, math.nan, math.inf])
def test_an_unknown_step_gives_none(step: float) -> None:
    shares = allowed(
        planned_egolf(step),
        {"evcs": 3220.0},
        support_on=False,
        support_max_w=3000.0,
        room_w=ROOM_W,
        planned_w={"egolf": 2800},
        planned_on=True,
    )
    assert shares == {"evcs": 2800.0}


def test_the_step_is_capped_at_the_free_discharge_power() -> None:
    shares = allowed(
        planned_egolf(),
        {"evcs": 3220.0},
        support_on=False,
        support_max_w=3000.0,
        room_w=3000.0,
        planned_w={"egolf": 2800},
        planned_on=True,
    )
    assert shares == {"evcs": 3000.0}


def test_the_step_needs_planned_on() -> None:
    shares = allowed(
        planned_egolf(),
        {"evcs": 3220.0},
        support_on=False,
        support_max_w=3000.0,
        room_w=ROOM_W,
        planned_w={"egolf": 2800},
    )
    assert shares == {"evcs": 0.0}


def test_now_car_gets_its_whole_deficit_only_with_spare() -> None:
    cars = [car("evcs", Effective.NOW, 11000.0), car("twc", Effective.PV, 2000.0)]
    deficit = {"evcs": 11000.0, "twc": 2000.0}
    on = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W, spare_on=True)
    assert on == {"evcs": 11000.0, "twc": 2000.0}  # zählt nicht gegen die 3 kW
    off = allowed(cars, deficit, support_on=True, support_max_w=3000.0, room_w=ROOM_W)
    assert off == {"evcs": 0.0, "twc": 2000.0}


def test_allowed_mixes_support_planned_and_spare() -> None:
    cars = [
        BatteryCar("evcs", Effective.PV, 4000.0, True, vehicle="tesla"),
        BatteryCar("twc", Effective.NOW, 7000.0, True, vehicle="egolf"),
        BatteryCar("evcs2", Effective.PLANNED, 3000.0, True, vehicle="gast"),
    ]
    shares = allowed(
        cars,
        {"evcs": 4000.0, "twc": 7000.0, "evcs2": 3000.0},
        support_on=True,
        support_max_w=3000,
        room_w=ROOM_W,
        planned_w={"gast": 1000},
        planned_on=True,
        spare_on=True,
    )
    assert shares == {"evcs": 3000.0, "twc": 7000.0, "evcs2": 1000.0}


def spare_slot(
    support_soc_pct: float | None = 70.0,
    *,
    now_battery: bool = True,
    kind: BatteryIntentKind = BatteryIntentKind.EIGENVERBRAUCH,
) -> ActiveSlot:
    """Ein gültiger Slot mit dieser Plan-Schwelle, der übrigen Akku für Sofort erlaubt."""
    grid_charge_w = 2000.0 if kind is BatteryIntentKind.NETZLADEN else 0.0
    intent = BatteryIntent(kind=kind, grid_charge_w=grid_charge_w)
    return ActiveSlot(1, T0, intent, None, None, support_soc_pct, now_battery=now_battery)


def test_now_spare_hysteresis() -> None:
    spare = Spare(ControlConfig())
    active = spare_slot()
    assert spare.update(72.0, active) == "on"
    assert spare.update(70.0, active) == "soc"  # erreicht: gesperrt
    assert spare.update(74.0, active) == "soc"  # unter 70 + 5
    assert spare.update(75.0, active) == "on"


def test_spare_hysteresis_follows_the_config() -> None:
    spare = Spare(ControlConfig(support_hysteresis_pct=2.0))
    active = spare_slot()
    assert spare.update(69.0, active) == "soc"
    assert spare.update(71.9, active) == "soc"
    assert spare.update(72.0, active) == "on"


def test_spare_unknown_soc_blocks_without_changing_state() -> None:
    spare = Spare(ControlConfig())
    active = spare_slot()
    assert spare.update(80.0, active) == "on"
    assert spare.update(None, active) == "soc"
    assert spare.update(math.nan, active) == "soc"
    assert spare.update(72.0, active) == "on"
    assert spare.update(70.0, active) == "soc"
    assert spare.update(None, active) == "soc"
    assert spare.update(72.0, active) == "soc"  # gesperrt bleibt gesperrt


def test_spare_is_off_without_a_slot_or_now_battery() -> None:
    spare = Spare(ControlConfig())
    assert spare.update(95.0, None) == "off"
    assert spare.update(95.0, spare_slot(now_battery=False)) == "off"


@pytest.mark.parametrize("kind", [BatteryIntentKind.HALTEN, BatteryIntentKind.NETZLADEN])
def test_spare_is_blocked_by_the_plan(kind: BatteryIntentKind) -> None:
    assert Spare(ControlConfig()).update(95.0, spare_slot(kind=kind)) == "plan"


def test_spare_without_a_plan_threshold_is_blocked() -> None:
    assert Spare(ControlConfig()).update(95.0, spare_slot(None)) == "soc"


def test_spare_soc_state_follows_every_slot_with_a_threshold() -> None:
    spare = Spare(ControlConfig())
    assert spare.update(68.0, spare_slot(now_battery=False)) == "off"  # fällt auf die Schwelle
    assert spare.update(72.0, spare_slot()) == "soc"  # erst ab 75 % wieder


def test_spare_reset_unlocks() -> None:
    spare = Spare(ControlConfig())
    active = spare_slot()
    assert spare.update(70.0, active) == "soc"
    spare.reset()
    assert spare.update(72.0, active) == "on"


# --- Wirksame Schwelle (Plan 3c, Präzisierung 8) --------------------------------------------------


def slot_with(support_soc_pct: float | None) -> ActiveSlot:
    """Ein gültiger Slot im Eigenverbrauch mit dieser Plan-Schwelle."""
    return ActiveSlot(1, T0, BatteryIntent(), None, None, support_soc_pct)


def test_plan_threshold_raises_the_effective_threshold() -> None:
    control = ControlConfig()
    assert effective_threshold(control, SupportIntent(), slot_with(82.0)) == (82.0, "plan")
    assert effective_threshold(control, SupportIntent(soc_pct=60.0), slot_with(40.0)) == (
        60.0,
        "intent",
    )
    assert effective_threshold(control, SupportIntent(), None) == (70.0, "config")


def test_the_plan_is_the_source_only_when_it_is_really_higher() -> None:
    control = ControlConfig()
    assert effective_threshold(control, SupportIntent(), slot_with(70.0)) == (70.0, "config")
    assert effective_threshold(control, SupportIntent(), slot_with(None)) == (70.0, "config")
    assert effective_threshold(control, SupportIntent(soc_pct=90.0), slot_with(82.0)) == (
        90.0,
        "intent",
    )
    assert effective_threshold(ControlConfig(support_soc_pct=50.0), SupportIntent(), None) == (
        50.0,
        "config",
    )


# --- Freie Entladeleistung für Geplant und Sofort (Fix-Welle F3) ----------------------------------


def test_plan_shares_are_capped_at_the_free_discharge_power() -> None:
    # Zwei Autos in Sofort mit je 11 kW, nach dem Haus bleiben 11 kW Entladeleistung: Das erste
    # bekommt sie, das zweite nichts; der Rest geht als Netzanteil an Override und Wächter.
    cars = [car("evcs", Effective.NOW, 11000.0), car("twc", Effective.NOW, 11000.0)]
    deficit = {"evcs": 11000.0, "twc": 11000.0}
    shares = allowed(
        cars, deficit, support_on=False, support_max_w=3000.0, spare_on=True, room_w=11000.0
    )
    assert shares == {"evcs": 11000.0, "twc": 0.0}


def test_the_cap_serves_support_and_boost_first_then_planned_then_now() -> None:
    cars = [
        BatteryCar("evcs", Effective.NOW, 6000.0, True, vehicle="egolf"),
        BatteryCar("twc", Effective.PV, 4000.0, True, vehicle="tesla"),
        BatteryCar("evcs2", Effective.PLANNED, 5000.0, True, vehicle="gast"),
        BatteryCar("evcs3", Effective.BOOST, 1000.0, True, vehicle="gast2"),
    ]
    deficit = {"evcs": 6000.0, "twc": 4000.0, "evcs2": 5000.0, "evcs3": 1000.0}
    shares = allowed(
        cars,
        deficit,
        support_on=True,
        support_max_w=3000.0,
        planned_w={"gast": 5000},
        planned_on=True,
        spare_on=True,
        room_w=10000.0,
    )
    # 10 kW − 3 kW Unterstützung − 1 kW Boost = 6 kW: 5 kW für Geplant, 1 kW für Sofort
    assert shares == {"evcs": 1000.0, "twc": 3000.0, "evcs2": 5000.0, "evcs3": 1000.0}


@pytest.mark.parametrize("room", [0.0, -500.0, math.nan, math.inf])
def test_without_free_discharge_power_plan_shares_get_nothing(room: float) -> None:
    # Keine freie Entladeleistung oder keine endliche Zahl: nichts für Geplant und Sofort; die
    # Unterstützung und Boost bleiben, wie sie sind.
    cars = [
        BatteryCar("evcs", Effective.NOW, 6000.0, True, vehicle="egolf"),
        BatteryCar("twc", Effective.PLANNED, 4000.0, True, vehicle="tesla"),
        BatteryCar("evcs2", Effective.BOOST, 2000.0, True, vehicle="gast"),
    ]
    deficit = {"evcs": 6000.0, "twc": 4000.0, "evcs2": 2000.0}
    shares = allowed(
        cars,
        deficit,
        support_on=True,
        support_max_w=3000.0,
        planned_w={"tesla": 4000},
        planned_on=True,
        spare_on=True,
        room_w=room,
    )
    assert shares == {"evcs": 0.0, "twc": 0.0, "evcs2": 2000.0}
