from datetime import timedelta

import pytest
from tests.helpers import T0, snap, snap_at

from bindaems.core.safety.inputs import (
    SAFETY_SIGNALS,
    SOC_JUMP_HOLD_S,
    SOC_JUMP_PP,
    SOC_JUMP_WINDOW_S,
    InputCheck,
)
from bindaems.core.safety.types import InputStatus
from bindaems.shared.domain import Alarm, Quality, Severity, Snapshot, Value

GOOD = {
    "grid.l1.power_w": 1000.0,
    "grid.l2.power_w": 1000.0,
    "grid.l3.power_w": 1000.0,
    "grid.l1.voltage_v": 230.0,
    "grid.l2.voltage_v": 230.0,
    "grid.l3.voltage_v": 230.0,
    "battery.soc_pct": 50.0,
    "battery.power_w": 0.0,
}
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def test_all_ok() -> None:
    assert InputCheck().evaluate(snap(GOOD), [], T0) == InputStatus(True, ())


def test_missing_or_stale_signal() -> None:
    values = {k: v for k, v in GOOD.items() if k != "grid.l2.voltage_v"}
    assert InputCheck().evaluate(snap(values), [], T0) == InputStatus(
        False, ("Messwert fehlt oder ist veraltet: grid.l2.voltage_v.",)
    )


def test_plausibility_alarm_passes_through() -> None:
    alarms = [
        Alarm("plaus.balance", Severity.WARNING, "Energiebilanz geht nicht auf.", T0),
        Alarm("competitor.dess", Severity.ERROR, "Dynamic ESS ist aktiv (Modus 1).", T0),
    ]
    assert InputCheck().evaluate(snap(GOOD), alarms, T0).problems == (
        "Energiebilanz geht nicht auf.",
    )


def test_soc_jump_holds_60s() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    jumped = GOOD | {"battery.soc_pct": 58.0}
    assert check.evaluate(snap_at(S(10), jumped), [], S(10)).problems == (
        "Sprung im Akku-SOC: 50 % → 58 %.",
    )
    assert check.evaluate(snap_at(S(71), jumped), [], S(71)).ok


def test_small_soc_change_is_fine() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    assert check.evaluate(snap_at(S(10), GOOD | {"battery.soc_pct": 53.0}), [], S(10)).ok


def test_change_after_long_gap_is_no_jump() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    assert check.evaluate(snap_at(S(120), GOOD | {"battery.soc_pct": 60.0}), [], S(120)).ok


# --- Weitere Randfälle -------------------------------------------------------------------------

SOC = "battery.soc_pct"


def soc_snap(seconds: float, pct: Value, quality: Quality = Quality.OK) -> Snapshot:
    """Alle Signale in Ordnung; der SOC hat den Wert ``pct`` und die Qualität ``quality``.

    Alle Messwerte tragen den Zeitstempel ``S(seconds)``.
    """
    others = snap_at(S(seconds), {k: v for k, v in GOOD.items() if k != SOC})
    soc = snap_at(S(seconds), {SOC: pct}, quality=quality)
    return Snapshot(S(seconds), {**others.readings, **soc.readings})


def degraded(quality: Quality, *signals: str) -> Snapshot:
    """``GOOD``, aber die genannten Signale haben die Qualität ``quality``.

    Im Snapshot stehen sie hinter den übrigen, in der Reihenfolge der Argumente.
    """
    ok = snap({k: v for k, v in GOOD.items() if k not in signals})
    bad = snap({k: GOOD[k] for k in signals}, quality=quality)
    return Snapshot(T0, {**ok.readings, **bad.readings})


def test_constants_are_the_specified_ones() -> None:
    assert SAFETY_SIGNALS == (
        "grid.l1.power_w",
        "grid.l2.power_w",
        "grid.l3.power_w",
        "grid.l1.voltage_v",
        "grid.l2.voltage_v",
        "grid.l3.voltage_v",
        "battery.soc_pct",
        "battery.power_w",
    )
    assert (SOC_JUMP_PP, SOC_JUMP_WINDOW_S, SOC_JUMP_HOLD_S) == (5.0, 60.0, 60.0)


# Problem 1: Messwert fehlt oder ist veraltet


@pytest.mark.parametrize("signal", SAFETY_SIGNALS)
def test_every_safety_signal_is_required(signal: str) -> None:
    values = {k: v for k, v in GOOD.items() if k != signal}
    assert InputCheck().evaluate(snap(values), [], T0) == InputStatus(
        False, (f"Messwert fehlt oder ist veraltet: {signal}.",)
    )


@pytest.mark.parametrize("quality", [Quality.STALE, Quality.INVALID])
def test_stale_and_invalid_signals_count_as_missing(quality: Quality) -> None:
    status = InputCheck().evaluate(degraded(quality, "grid.l3.power_w"), [], T0)
    assert status == InputStatus(False, ("Messwert fehlt oder ist veraltet: grid.l3.power_w.",))


def test_several_signals_are_named_in_the_order_of_the_list() -> None:
    # Die Reihenfolge ist die von SAFETY_SIGNALS, nicht die im Snapshot.
    bad = degraded(Quality.STALE, "battery.power_w", "grid.l2.voltage_v", "grid.l1.power_w")
    assert InputCheck().evaluate(bad, [], T0).problems == (
        "Messwert fehlt oder ist veraltet: grid.l1.power_w, grid.l2.voltage_v, battery.power_w.",
    )


def test_empty_snapshot_names_all_signals_in_one_problem() -> None:
    assert InputCheck().evaluate(snap({}), [], T0) == InputStatus(
        False,
        (
            "Messwert fehlt oder ist veraltet: grid.l1.power_w, grid.l2.power_w, "
            "grid.l3.power_w, grid.l1.voltage_v, grid.l2.voltage_v, grid.l3.voltage_v, "
            "battery.soc_pct, battery.power_w.",
        ),
    )


def test_other_signals_are_not_checked() -> None:
    stale_pv = snap({"pv.total_power_w": 3000.0}, quality=Quality.STALE)
    snapshot = Snapshot(T0, {**snap(GOOD).readings, **stale_pv.readings})
    assert InputCheck().evaluate(snapshot, [], T0) == InputStatus(True, ())


# Problem 2: Alarme der Plausibilitätsprüfung


def test_plausibility_alarms_are_listed_in_the_order_given() -> None:
    # Schwere und ``since`` spielen keine Rolle; es zählt nur die ID mit dem Präfix ``plaus.``.
    alarms = [
        Alarm("plaus.voltage.L2", Severity.INFO, "Netzspannung L2 unplausibel: 300.0 V", T0),
        Alarm("safety.write_budget", Severity.WARNING, "Schreibbudget erschöpft.", T0),
        Alarm("plaus.soc", Severity.ERROR, "Akku-SOC unplausibel: 120.0 %", S(5)),
    ]
    assert InputCheck().evaluate(snap(GOOD), alarms, T0) == InputStatus(
        False, ("Netzspannung L2 unplausibel: 300.0 V", "Akku-SOC unplausibel: 120.0 %")
    )


@pytest.mark.parametrize(
    "alarm_id",
    [
        "competitor.dess",
        "safety.write_budget",
        "plaus",
        "plausible.soc",
        "x.plaus.soc",
        "PLAUS.soc",
        "",
    ],
)
def test_only_ids_starting_with_plaus_dot_count(alarm_id: str) -> None:
    alarm = Alarm(alarm_id, Severity.ERROR, "Meldung.", T0)
    assert InputCheck().evaluate(snap(GOOD), [alarm], T0) == InputStatus(True, ())


def test_problems_come_in_the_order_of_the_rules() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    values = {k: v for k, v in GOOD.items() if k != "grid.l2.voltage_v"} | {SOC: 58.0}
    alarms = [Alarm("plaus.balance", Severity.WARNING, "Energiebilanz geht nicht auf.", S(10))]
    assert check.evaluate(snap_at(S(10), values), alarms, S(10)) == InputStatus(
        False,
        (
            "Messwert fehlt oder ist veraltet: grid.l2.voltage_v.",
            "Energiebilanz geht nicht auf.",
            "Sprung im Akku-SOC: 50 % → 58 %.",
        ),
    )


# Problem 3: Sprung im Akku-SOC


@pytest.mark.parametrize(
    ("after", "problems"),
    [
        (55.0, ()),  # genau 5 Prozentpunkte sind kein Sprung
        (45.0, ()),
        (55.5, ("Sprung im Akku-SOC: 50 % → 55,5 %.",)),
        (44.5, ("Sprung im Akku-SOC: 50 % → 44,5 %.",)),
    ],
)
def test_a_jump_is_more_than_5_pp_in_either_direction(
    after: float, problems: tuple[str, ...]
) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    assert check.evaluate(soc_snap(10, after), [], S(10)).problems == problems


@pytest.mark.parametrize(
    ("gap_s", "jump"), [(59.0, True), (60.0, True), (60.5, False), (61.0, False)]
)
def test_the_measurements_must_be_at_most_60_s_apart(gap_s: float, jump: bool) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    assert check.evaluate(soc_snap(gap_s, 60.0), [], S(gap_s)).ok is not jump


@pytest.mark.parametrize(("cycle_s", "held"), [(10, True), (69.5, True), (70, False), (71, False)])
def test_the_hold_lasts_60_s_from_the_jump_and_is_half_open(cycle_s: float, held: bool) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    check.evaluate(soc_snap(10, 58.0), [], S(10))  # Sprung erkannt, Haltezeit bis S(70)
    # Jeder weitere Zyklus wertet dieselbe Messung aus (gleicher Zeitstempel).
    assert check.evaluate(soc_snap(10, 58.0), [], S(cycle_s)).ok is not held


def test_cycles_with_the_same_measurement_do_not_extend_the_hold() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    for cycle_s in range(10, 70, 10):
        status = check.evaluate(soc_snap(10, 58.0), [], S(cycle_s))
        assert status.problems == ("Sprung im Akku-SOC: 50 % → 58 %.",)
    assert check.evaluate(soc_snap(10, 58.0), [], S(70)).ok


def test_the_jump_stays_named_while_the_soc_moves_on_slowly() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    check.evaluate(soc_snap(10, 58.0), [], S(10))
    status = check.evaluate(soc_snap(20, 59.0), [], S(20))  # 58 → 59 ist kein neuer Sprung
    assert status.problems == ("Sprung im Akku-SOC: 50 % → 58 %.",)


def test_a_second_jump_restarts_the_hold_with_its_own_values() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    check.evaluate(soc_snap(10, 58.0), [], S(10))
    second = check.evaluate(soc_snap(30, 66.0), [], S(30))
    assert second.problems == ("Sprung im Akku-SOC: 58 % → 66 %.",)
    assert not check.evaluate(soc_snap(30, 66.0), [], S(89)).ok  # Haltezeit bis S(90)
    assert check.evaluate(soc_snap(30, 66.0), [], S(90)).ok


def test_each_measurement_is_compared_with_the_one_before() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    assert check.evaluate(soc_snap(10, 53.0), [], S(10)).ok
    assert check.evaluate(soc_snap(20, 58.0), [], S(20)).ok  # 5 pp gegen 53, nicht 8 gegen 50
    assert check.evaluate(soc_snap(30, 64.0), [], S(30)).problems == (
        "Sprung im Akku-SOC: 58 % → 64 %.",
    )


def test_distance_counts_from_the_readings_and_the_hold_from_now() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    # Die Messung ist 10 s jünger als die erste, wird aber erst bei S(200) ausgewertet: Der
    # Abstand zählt nach den Messzeiten, die Haltezeit ab ``now``.
    late = check.evaluate(soc_snap(10, 58.0), [], S(200))
    assert late.problems == ("Sprung im Akku-SOC: 50 % → 58 %.",)
    assert not check.evaluate(soc_snap(10, 58.0), [], S(259)).ok
    assert check.evaluate(soc_snap(10, 58.0), [], S(260)).ok


def test_a_reading_with_the_same_timestamp_is_not_a_new_measurement() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    assert check.evaluate(soc_snap(0, 70.0), [], S(5)).ok  # gleicher Zeitstempel: nicht neu
    # Die letzte Messung ist weiter die von T0.
    assert check.evaluate(soc_snap(10, 58.0), [], S(10)).problems == (
        "Sprung im Akku-SOC: 50 % → 58 %.",
    )


@pytest.mark.parametrize(("ts_s", "jump"), [(140, True), (139, False)])
def test_a_reading_older_than_the_last_counts_by_its_distance(ts_s: float, jump: bool) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(200, 50.0), [], S(200))
    assert check.evaluate(soc_snap(ts_s, 58.0), [], S(201)).ok is not jump


@pytest.mark.parametrize("quality", [Quality.STALE, Quality.INVALID])
def test_a_soc_without_quality_ok_is_neither_judged_nor_remembered(quality: Quality) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    bad = check.evaluate(soc_snap(10, 70.0, quality), [], S(10))
    assert bad.problems == ("Messwert fehlt oder ist veraltet: battery.soc_pct.",)  # kein Sprung
    # Verglichen wird weiter mit der letzten gültigen Messung (50 % um T0).
    assert check.evaluate(soc_snap(20, 58.0), [], S(20)).problems == (
        "Sprung im Akku-SOC: 50 % → 58 %.",
    )


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "55", True])
def test_an_unusable_soc_value_is_not_remembered(value: Value) -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50.0), [], T0)
    check.evaluate(soc_snap(10, value), [], S(10))
    assert check.evaluate(soc_snap(20, 60.0), [], S(20)).problems == (
        "Sprung im Akku-SOC: 50 % → 60 %.",
    )


def test_an_integer_soc_is_a_number() -> None:
    check = InputCheck()
    check.evaluate(soc_snap(0, 50), [], T0)
    assert check.evaluate(soc_snap(10, 58), [], S(10)).problems == (
        "Sprung im Akku-SOC: 50 % → 58 %.",
    )
