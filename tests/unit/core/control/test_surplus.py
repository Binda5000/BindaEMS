import math
from dataclasses import replace

import pytest

from bindaems.core.control.surplus import SurplusCar, share_surplus, surplus_w
from bindaems.shared.control import SurplusOrder, SurplusPriority

U = 230.0
TESLA = SurplusCar("tesla", full_power=False, minimum=False, phases=3, min_a=5, max_a=16)
EGOLF = SurplusCar("egolf", full_power=False, minimum=False, phases=2, min_a=6, max_a=16)
SINGLE = SurplusCar("single", full_power=False, minimum=False, phases=1, min_a=6, max_a=16)
CARS_FIRST = SurplusPriority(kind=SurplusOrder.CARS_FIRST)
BATTERY_FIRST = SurplusPriority(kind=SurplusOrder.BATTERY_FIRST)
B30 = SurplusPriority(kind=SurplusOrder.BATTERY_UNTIL_SOC, soc_pct=30.0)
PRIORITIES = [CARS_FIRST, BATTERY_FIRST, B30]

# --- Überschuss -----------------------------------------------------------------------------------


def test_discharge_always_counts_against_the_cars() -> None:
    assert (
        surplus_w(
            cars_w=4000.0,
            grid_w=500.0,
            battery_w=-1500.0,
            soc_pct=80.0,
            priority=CARS_FIRST,
            margin_w=100.0,
        )
        == 1900.0
    )


@pytest.mark.parametrize(
    ("priority", "soc", "expected"),
    [
        (CARS_FIRST, 10.0, 2900.0),
        (BATTERY_FIRST, 90.0, 900.0),
        (B30, 29.9, 900.0),
        (B30, 30.0, 2900.0),
        (B30, None, 900.0),
    ],
)
def test_charging_battery_counts_by_priority(priority, soc, expected) -> None:
    assert (
        surplus_w(
            cars_w=0.0,
            grid_w=-1000.0,
            battery_w=2000.0,
            soc_pct=soc,
            priority=priority,
            margin_w=100.0,
        )
        == expected
    )


@pytest.mark.parametrize("missing", ["cars_w", "grid_w", "battery_w"])
def test_a_missing_value_makes_the_surplus_unknown(missing) -> None:
    values = {"cars_w": 0.0, "grid_w": -1000.0, "battery_w": 0.0} | {missing: None}
    assert surplus_w(**values, soc_pct=50.0, priority=B30, margin_w=100.0) is None


@pytest.mark.parametrize("priority", PRIORITIES)
@pytest.mark.parametrize("soc", [10.0, 80.0, None])
def test_discharge_counts_with_every_priority_and_soc(priority, soc) -> None:
    surplus = surplus_w(
        cars_w=4000.0,
        grid_w=500.0,
        battery_w=-1500.0,
        soc_pct=soc,
        priority=priority,
        margin_w=100.0,
    )
    assert surplus == 1900.0


@pytest.mark.parametrize("priority", PRIORITIES)
def test_an_idle_battery_counts_nothing(priority) -> None:
    surplus = surplus_w(
        cars_w=0.0,
        grid_w=-1000.0,
        battery_w=0.0,
        soc_pct=50.0,
        priority=priority,
        margin_w=100.0,
    )
    assert surplus == 900.0


def test_the_surplus_may_be_negative() -> None:
    surplus = surplus_w(
        cars_w=0.0,
        grid_w=2000.0,
        battery_w=0.0,
        soc_pct=50.0,
        priority=B30,
        margin_w=100.0,
    )
    assert surplus == -2100.0


@pytest.mark.parametrize("missing", ["cars_w", "grid_w", "battery_w"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_value_that_is_no_finite_number_makes_the_surplus_unknown(missing, bad) -> None:
    values = {"cars_w": 0.0, "grid_w": -1000.0, "battery_w": 0.0} | {missing: bad}
    assert surplus_w(**values, soc_pct=50.0, priority=B30, margin_w=100.0) is None


@pytest.mark.parametrize("soc", [math.nan, math.inf])
def test_a_soc_that_is_no_finite_number_does_not_count(soc) -> None:
    surplus = surplus_w(
        cars_w=0.0,
        grid_w=-1000.0,
        battery_w=2000.0,
        soc_pct=soc,
        priority=B30,
        margin_w=100.0,
    )
    assert surplus == 900.0


# --- Verteilung -----------------------------------------------------------------------------------


def test_full_power_cars_take_their_share_first() -> None:
    now = replace(TESLA, full_power=True)
    assert share_surplus(11000.0, [now, EGOLF], U) == {"tesla": 16, "egolf": 0}


def test_pv_cars_share_in_order() -> None:
    assert share_surplus(9000.0, [TESLA, EGOLF], U) == {"tesla": 13, "egolf": 0}
    assert share_surplus(9000.0, [EGOLF, TESLA], U) == {"egolf": 16, "tesla": 2}


def test_min_pv_takes_its_minimum_even_without_surplus() -> None:
    assert share_surplus(500.0, [replace(TESLA, minimum=True), EGOLF], U) == {
        "tesla": 5,
        "egolf": 0,
    }


def test_a_pv_car_in_its_stop_minutes_uses_its_minimum() -> None:
    assert share_surplus(2000.0, [replace(EGOLF, holding_min=True), TESLA], U) == {
        "egolf": 4,
        "tesla": 0,
    }


def test_unknown_surplus() -> None:
    cars = [TESLA, replace(EGOLF, minimum=True)]
    assert share_surplus(None, cars, U) == {"tesla": None, "egolf": 6}


@pytest.mark.parametrize(("available_w", "egolf"), [(11000.0, 0), (15000.0, 8)])
def test_full_power_cars_deduct_up_front_wherever_they_stand(available_w, egolf) -> None:
    now = replace(TESLA, full_power=True)
    assert share_surplus(available_w, [EGOLF, now], U) == {"egolf": egolf, "tesla": 16}


@pytest.mark.parametrize(
    ("available_w", "car", "expected"),
    [
        (0.0, TESLA, 0),
        (689.0, TESLA, 0),  # knapp unter 1 A: abgerundet
        (690.0, TESLA, 1),
        (3450.0, TESLA, 5),
        (20000.0, TESLA, 16),  # begrenzt auf max_a
        (-500.0, TESLA, 0),  # negativer Überschuss
        (-500.0, replace(TESLA, minimum=True), 5),
        (9000.0, replace(TESLA, minimum=True), 13),  # über dem Minimum zählt der Anteil
        (0.0, replace(TESLA, full_power=True, minimum=True), 16),
    ],
)
def test_share_of_a_single_car(available_w, car, expected) -> None:
    assert share_surplus(available_w, [car], U) == {"tesla": expected}


def test_a_car_below_its_minimum_uses_nothing() -> None:
    assert share_surplus(2000.0, [EGOLF, SINGLE], U) == {"egolf": 4, "single": 8}


def test_holding_the_minimum_uses_the_minimum_not_the_share() -> None:
    holding = replace(EGOLF, holding_min=True)
    assert share_surplus(2600.0, [holding, SINGLE], U) == {"egolf": 5, "single": 0}


def test_holding_above_the_minimum_uses_the_own_share() -> None:
    holding = replace(EGOLF, holding_min=True)
    assert share_surplus(4000.0, [holding, SINGLE], U) == {"egolf": 8, "single": 1}


def test_unknown_surplus_gives_full_power_its_maximum_and_a_pv_car_nothing() -> None:
    cars = [replace(TESLA, full_power=True, minimum=True), replace(EGOLF, holding_min=True)]
    assert share_surplus(None, cars, U) == {"tesla": 16, "egolf": None}


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_surplus_that_is_no_finite_number_is_unknown(bad) -> None:
    cars = [TESLA, replace(EGOLF, minimum=True)]
    assert share_surplus(bad, cars, U) == {"tesla": None, "egolf": 6}


def test_the_nominal_voltage_sets_the_power_per_ampere() -> None:
    assert share_surplus(9000.0, [TESLA, EGOLF], 240.0) == {"tesla": 12, "egolf": 0}


def test_the_result_keeps_the_order_of_the_cars() -> None:
    now = replace(TESLA, full_power=True)
    shares = share_surplus(15000.0, [EGOLF, now, SINGLE], U)
    assert list(shares) == ["egolf", "tesla", "single"]


def test_no_cars_get_no_shares() -> None:
    assert share_surplus(5000.0, [], U) == {}
    assert share_surplus(None, [], U) == {}


# --- Hysterese der EVCS (Design 6.5) --------------------------------------------------------------

HELD = replace(EGOLF, held_a=8, hysteresis_a=0.5)  # 1 A sind beim e-Golf 460 W


@pytest.mark.parametrize(
    ("available_w", "car", "expected"),
    [
        (3680.0, HELD, 8),  # Anteil 8,0 A: Er bleibt.
        (4369.0, HELD, 8),  # knapp unter 8 + 1 + 0,5 A: Er bleibt.
        (4370.0, HELD, 9),  # 9,5 A: Er steigt, auf abgerundet 9,5 − 0,5.
        (5289.0, HELD, 10),  # knapp unter 11,5 A: 10, mit 0,5 A Abstand nach unten
        (5290.0, HELD, 11),
        (3679.0, HELD, 7),  # unter 8 A: Er sinkt sofort.
        (2300.0, replace(HELD, minimum=True), 6),  # Min+PV: nie unter den Mindeststrom
        (7452.0, replace(HELD, held_a=15), 15),  # 16,2 A: Auch vor dem Höchststrom bleibt er.
        (7590.0, replace(HELD, held_a=15), 16),
        (9200.0, replace(HELD, held_a=16, max_a=10), 10),  # begrenzt auf max_a
    ],
)
def test_a_held_current_rises_only_from_one_ampere_plus_the_hysteresis(
    available_w, car, expected
) -> None:
    assert share_surplus(available_w, [car], U) == {"egolf": expected}


def test_the_next_car_gets_the_rest_after_the_held_current() -> None:
    # Anteil des e-Golf 9,4 A: Er hält 8 A, der Rest 4324 − 3680 = 644 W geht weiter.
    assert share_surplus(4324.0, [HELD, SINGLE], U) == {"egolf": 8, "single": 2}


def test_without_a_held_current_the_share_is_rounded_down() -> None:
    assert share_surplus(4324.0, [replace(HELD, held_a=None)], U) == {"egolf": 9}
