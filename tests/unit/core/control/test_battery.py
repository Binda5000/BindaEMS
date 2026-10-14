import math
from datetime import datetime, timedelta

from tests.helpers import T0

from bindaems.core.control.battery import (
    BRIDGE_END_S,
    SUPPORT_MODES,
    BatteryCar,
    Support,
    allowed,
    deficits,
)
from bindaems.core.control.charge_modes import Effective
from bindaems.shared.config import ControlConfig


def car(
    wallbox: str = "evcs",
    mode: Effective = Effective.PV,
    power: float | None = 4000.0,
    charging: bool | None = True,
) -> BatteryCar:
    return BatteryCar(wallbox, mode, power, charging)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


CARS = [car(mode=Effective.MIN_PV)]  # ein Auto, für das die Unterstützung gelten kann
DEFICIT = {"evcs": 500.0}  # weit über der Schwelle von 100 W


def test_constants() -> None:
    assert BRIDGE_END_S == 60.0
    assert SUPPORT_MODES == {Effective.PV, Effective.MIN_PV}


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


def test_negative_car_power_counts_as_zero() -> None:
    assert deficits(0.0, 0.0, [car(power=-30.0)]) == {"evcs": 0.0}
    cars = [car("a", power=-30.0), car("b", power=1000.0)]  # die −30 W geben nichts zurück
    assert deficits(0.0, 0.0, cars) == {"a": 0.0, "b": 1000.0}


# --- Unterstützung: SOC ---------------------------------------------------------------------------


def test_support_needs_70_percent_and_ends_below_65() -> None:
    support, cars, deficit = Support(ControlConfig()), [car(mode=Effective.MIN_PV)], {"evcs": 500.0}
    socs = [69.9, 70.0, 65.0, 64.9, 69.0, 70.0]
    found = [support.update(at(s), soc, cars, deficit)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "on", "on", "soc", "soc", "on"]


def test_unknown_soc_blocks_without_resetting_the_hysteresis() -> None:
    support = Support(ControlConfig())
    socs = [72.0, None, 66.0]  # „on“, im Zyklus gesperrt, im Band weiter „on“
    found = [support.update(at(s), soc, CARS, DEFICIT)[0] for s, soc in enumerate(socs)]
    assert found == ["on", "soc", "on"]


def test_unknown_soc_does_not_start_the_support_either() -> None:
    support = Support(ControlConfig())
    socs = [None, 66.0, None, 70.0]
    found = [support.update(at(s), soc, CARS, DEFICIT)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "soc", "soc", "on"]


def test_a_soc_that_is_not_a_finite_number_is_unknown() -> None:
    support = Support(ControlConfig())
    socs = [72.0, math.nan, math.inf, -math.inf, 66.0]
    found = [support.update(at(s), soc, CARS, DEFICIT)[0] for s, soc in enumerate(socs)]
    assert found == ["on", "soc", "soc", "soc", "on"]


def test_soc_thresholds_follow_the_config() -> None:
    support = Support(ControlConfig(support_soc_pct=50.0, support_hysteresis_pct=10.0))
    socs = [49.9, 50.0, 40.0, 39.9, 49.0, 50.0]
    found = [support.update(at(s), soc, CARS, DEFICIT)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "on", "on", "soc", "soc", "on"]


# --- Unterstützung: Überbrückung ------------------------------------------------------------------


def test_bridging_blocks_after_900_s_until_60_s_without_deficit() -> None:
    support = Support(ControlConfig())
    found = {}
    for second in range(962):
        deficit_w = 500.0 if second < 900 or second == 961 else 0.0
        found[second] = support.update(at(second), 80.0, CARS, {"evcs": deficit_w})
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
    assert support.update(at(0), 80.0, CARS, DEFICIT) == ("on", 0.0)
    assert support.update(at(5000), 80.0, CARS, DEFICIT) == ("time", 5000.0)


def test_the_longest_bridging_follows_the_config() -> None:
    support = Support(ControlConfig(support_max_s=30.0))
    assert support.update(at(0), 80.0, CARS, DEFICIT) == ("on", 0.0)
    assert support.update(at(29), 80.0, CARS, DEFICIT) == ("on", 29.0)
    assert support.update(at(30), 80.0, CARS, DEFICIT) == ("time", 30.0)


def test_a_deficit_within_60_s_continues_the_bridging_and_restarts_the_count() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, {"evcs": 500.0}) == ("on", 0.0)
    assert support.update(at(10), 80.0, CARS, {"evcs": 0.0}) == ("on", 10.0)  # läuft noch
    assert support.update(at(69), 80.0, CARS, {"evcs": 0.0}) == ("on", 69.0)  # 59 s ohne
    assert support.update(at(70), 80.0, CARS, {"evcs": 500.0}) == ("on", 70.0)  # dieselbe
    assert support.update(at(71), 80.0, CARS, {"evcs": 0.0}) == ("on", 71.0)  # die 60 s von vorn
    assert support.update(at(130), 80.0, CARS, {"evcs": 0.0}) == ("on", 130.0)  # 59 s ohne
    assert support.update(at(131), 80.0, CARS, {"evcs": 0.0}) == ("on", None)  # 60 s ohne


def test_small_deficits_neither_start_nor_extend_the_bridging() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, {"evcs": 99.0}) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 100.0}) == ("on", 0.0)
    # 99 W gelten als „ohne Fehlbetrag“: Nach 60 s davon ist die Überbrückung beendet.
    assert support.update(at(2), 80.0, CARS, {"evcs": 99.0}) == ("on", 1.0)
    assert support.update(at(61), 80.0, CARS, {"evcs": 99.0}) == ("on", 60.0)
    assert support.update(at(62), 80.0, CARS, {"evcs": 99.0}) == ("on", None)


def test_the_deficit_threshold_is_half_the_setpoint_deadband() -> None:
    support = Support(ControlConfig(setpoint_deadband_w=400.0))
    assert support.update(at(0), 80.0, CARS, {"evcs": 199.0}) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 200.0}) == ("on", 0.0)


def test_a_deficit_has_to_be_positive_even_without_a_deadband() -> None:
    support = Support(ControlConfig(setpoint_deadband_w=0.0))
    assert support.update(at(0), 80.0, CARS, {"evcs": 0.0}) == ("on", None)
    assert support.update(at(1), 80.0, CARS, {"evcs": 0.1}) == ("on", 0.0)


def test_the_deficits_of_the_pv_and_min_pv_cars_add_up() -> None:
    support = Support(ControlConfig())
    cars = [car("a", Effective.PV), car("b", Effective.MIN_PV)]
    assert support.update(at(0), 80.0, cars, {"a": 50.0, "b": 49.0}) == ("on", None)
    assert support.update(at(1), 80.0, cars, {"a": 50.0, "b": 50.0}) == ("on", 0.0)


def test_only_cars_in_pv_or_min_pv_count_for_the_bridging() -> None:
    cars = [
        car("a", Effective.PV),
        car("b", Effective.BOOST),
        car("c", Effective.NOW),
        car("d", Effective.OFF),
    ]
    deficit = {"a": 0.0, "b": 5000.0, "c": 4000.0, "d": 3000.0}
    assert Support(ControlConfig()).update(at(0), 80.0, cars, deficit) == ("on", None)


def test_a_car_without_an_entry_has_no_deficit() -> None:
    assert Support(ControlConfig()).update(at(0), 80.0, CARS, {}) == ("on", None)


# --- Unterstützung: Stand und Neubeginn -----------------------------------------------------------


def test_without_pv_or_min_pv_cars_the_reason_is_mode() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, [car(mode=Effective.NOW)], DEFICIT) == ("mode", None)


def test_only_pv_and_min_pv_cars_make_the_support_possible() -> None:
    for mode in Effective:
        found = Support(ControlConfig()).update(at(0), 80.0, [car(mode=mode)], DEFICIT)[0]
        assert found == ("on" if mode in SUPPORT_MODES else "mode")
    assert Support(ControlConfig()).update(at(0), 80.0, [], {}) == ("mode", None)


def test_the_reason_names_mode_before_soc_before_time() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 80.0, CARS, DEFICIT) == ("on", 0.0)
    assert support.update(at(900), 80.0, CARS, DEFICIT) == ("time", 900.0)
    assert support.update(at(901), 60.0, CARS, DEFICIT) == ("soc", 901.0)  # SOC und Zeit sperren
    now_only = [car(mode=Effective.NOW)]
    assert support.update(at(902), 60.0, now_only, DEFICIT) == ("mode", 902.0)  # dazu der Modus


def test_the_soc_state_follows_the_cycles_without_a_support_car() -> None:
    support = Support(ControlConfig())
    now_only = [car(mode=Effective.NOW)]
    assert support.update(at(0), 72.0, CARS, DEFICIT)[0] == "on"
    assert support.update(at(1), 60.0, now_only, DEFICIT)[0] == "mode"  # fällt unter 65 %
    assert support.update(at(2), 68.0, CARS, DEFICIT)[0] == "soc"  # erst ab 70 % wieder


def test_reset_starts_over_with_the_soc_and_the_bridging() -> None:
    support = Support(ControlConfig())
    assert support.update(at(0), 72.0, CARS, DEFICIT) == ("on", 0.0)
    assert support.update(at(1000), 66.0, CARS, DEFICIT) == ("time", 1000.0)
    support.reset()
    assert support.update(at(1001), 66.0, CARS, DEFICIT) == ("soc", 0.0)  # 70 % wieder erreichen
    assert support.update(at(1002), 70.0, CARS, DEFICIT) == ("on", 1.0)  # neue Überbrückung


# --- Erlaubter Akku-Anteil ------------------------------------------------------------------------


def test_allowed_share_follows_the_mode_and_the_limit() -> None:
    cars = [
        car("a", Effective.PV),
        car("b", Effective.MIN_PV),
        car("c", Effective.NOW),
        car("d", Effective.BOOST),
    ]
    deficit = {"a": 2000.0, "b": 2000.0, "c": 4000.0, "d": 5000.0}
    on = allowed(cars, deficit, support_on=True, support_max_w=3000.0)
    off = allowed(cars, deficit, support_on=False, support_max_w=3000.0)
    assert on == {"a": 2000.0, "b": 1000.0, "c": 0.0, "d": 5000.0}
    assert off == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 5000.0}


def test_off_gets_nothing_from_the_battery() -> None:
    cars = [car("a", Effective.OFF)]
    assert allowed(cars, {"a": 2000.0}, support_on=True, support_max_w=3000.0) == {"a": 0.0}


def test_the_limit_goes_to_the_cars_in_order_of_the_list() -> None:
    cars = [car("a", Effective.MIN_PV), car("b", Effective.PV), car("c", Effective.PV)]
    deficit = {"a": 2500.0, "b": 2500.0, "c": 2500.0}
    found = allowed(cars, deficit, support_on=True, support_max_w=3000.0)
    assert found == {"a": 2500.0, "b": 500.0, "c": 0.0}
    assert list(found) == ["a", "b", "c"]


def test_boost_does_not_use_up_the_limit_of_the_others() -> None:
    cars = [car("b", Effective.BOOST), car("a", Effective.PV)]
    deficit = {"b": 5000.0, "a": 2000.0}
    found = allowed(cars, deficit, support_on=True, support_max_w=3000.0)
    assert found == {"b": 5000.0, "a": 2000.0}


def test_a_car_never_gets_more_than_its_deficit() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    found = allowed(cars, {"a": 800.0, "b": 0.0}, support_on=True, support_max_w=3000.0)
    assert found == {"a": 800.0, "b": 0.0}


def test_without_a_limit_only_boost_gets_something() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    found = allowed(cars, {"a": 800.0, "b": 900.0}, support_on=True, support_max_w=0.0)
    assert found == {"a": 0.0, "b": 900.0}


def test_a_car_without_an_entry_gets_nothing() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.BOOST)]
    assert allowed(cars, {}, support_on=True, support_max_w=3000.0) == {"a": 0.0, "b": 0.0}
