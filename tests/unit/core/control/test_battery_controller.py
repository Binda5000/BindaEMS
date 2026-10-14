import math
from datetime import datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.control.battery import (
    BATTERY_COMP,
    BATTERY_SUPPORT,
    BatteryCar,
    BatteryController,
    BatteryNeed,
)
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand
from bindaems.core.control.phase_guard import GuardCause
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, Command, CommandKind
from bindaems.shared.decisions import DecisionResult

SETPOINT = (VICTRON, CommandKind.GRID_SETPOINT)
FREE = {
    "base_w": 0.0,
    "budget_w": None,
    "present": {SETPOINT: None},
    "controllable": True,
    "locked": False,
    "blocked": False,
}
IDLE = {
    "state": "idle",
    "target_w": None,
    "override_w": None,
    "support": None,
    "bridging_s": None,
    "refresh_age_s": None,
}


def need(wanted: float, allowed: float = 0.0) -> BatteryNeed:
    return BatteryNeed(0.0, 500.0, wanted, {"evcs": wanted}, {"evcs": allowed}, "soc", None)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def setpoint(value: float | None, *reasons: str, refresh: bool = False) -> Command:
    return Command(VICTRON, CommandKind.GRID_SETPOINT, value, reasons=reasons, refresh=refresh)


def accept(
    battery: BatteryController, commands: list[ControlCommand], dry_run: bool = True
) -> None:
    for found in commands:
        command = found.command
        battery.note_verdict(
            command, Verdict(command, Outcome.ACCEPTED, command.value, (), dry_run)
        )


def holding(cfg: Config, value: float) -> BatteryController:
    """Ein Akku-Sollwert mit dem eigenen Override ``value`` aus einem Urteil zur Zeit ``T0``."""
    battery = BatteryController(cfg)
    [found] = battery.decide(T0, need(value), **FREE)
    assert found.command == setpoint(value, "battery_comp")
    accept(battery, [found])
    return battery


def with_control(cfg: Config, **update: float) -> Config:
    return cfg.model_copy(update={"control": cfg.control.model_copy(update=update)})


def test_reason_codes() -> None:
    assert (BATTERY_COMP, BATTERY_SUPPORT) == ("battery_comp", "battery_support")
    assert GuardCause.PEAK_GUARD.value == "peak_guard"


# --- Bedarf ---------------------------------------------------------------------------------------


def test_need_puts_on_the_grid_what_the_battery_may_not_give() -> None:
    found = BatteryNeed(
        0.0, 0.0, 3000.0, {"a": 1000.0, "b": 2000.0}, {"a": 1500.0, "b": 0.0}, "on", None
    )
    assert found.grid_w == {"a": 0.0, "b": 2000.0}
    assert found.wanted_w == 2000.0
    assert BatteryNeed(0.0, 0.0, 0.0, {}, {}, "mode", None).wanted_w == 0.0


def test_assess_combines_deficit_support_and_allowed(cfg) -> None:
    battery = BatteryController(cfg)
    cars = [BatteryCar("evcs", Effective.MIN_PV, 2760.0, True)]
    found = battery.assess(T0, 80.0, 0.0, 500.0, cars)
    assert found == BatteryNeed(0.0, 500.0, 2760.0, {"evcs": 2760.0}, {"evcs": 2760.0}, "on", 0.0)
    assert found.grid_w == {"evcs": 0.0}
    assert found.wanted_w == 0.0
    assert battery.assess(at(1), 80.0, None, 500.0, cars) is None


def test_assess_puts_on_the_grid_what_the_battery_may_not_give(cfg) -> None:
    cars = [
        BatteryCar("twc", Effective.PV, 4000.0, True),
        BatteryCar("evcs", Effective.NOW, 2000.0, True),
        BatteryCar("spare", Effective.PV, None, False),  # lädt sicher nicht: 0 W
    ]
    found = BatteryController(cfg).assess(T0, 80.0, 500.0, 500.0, cars)  # die PV deckt das Haus
    assert found is not None
    assert found.cars_w == 6000.0
    assert found.deficit_w == {"twc": 4000.0, "evcs": 2000.0, "spare": 0.0}
    assert found.allowed_w == {"twc": 3000.0, "evcs": 0.0, "spare": 0.0}  # support_max_w 3000
    assert found.grid_w == {"twc": 1000.0, "evcs": 2000.0, "spare": 0.0}
    assert found.wanted_w == 3000.0
    low = BatteryController(cfg).assess(T0, 60.0, 500.0, 500.0, cars)
    assert low is not None
    assert (low.support, low.wanted_w) == ("soc", 6000.0)


def test_assess_keeps_the_measured_pv_and_house(cfg) -> None:
    cars = [BatteryCar("evcs", Effective.NOW, 1000.0, True)]
    found = BatteryController(cfg).assess(T0, 80.0, -20.0, 300.0, cars)
    assert found is not None
    assert (found.pv_w, found.house_w, found.deficit_w) == (-20.0, 300.0, {"evcs": 1000.0})


@pytest.mark.parametrize(
    ("pv", "house"),
    [(None, 500.0), (0.0, None), (math.nan, 500.0), (0.0, math.inf), (-math.inf, 500.0)],
)
def test_assess_without_pv_or_house_leaves_the_support_alone(cfg, pv, house) -> None:
    battery = BatteryController(cfg)
    cars = [BatteryCar("evcs", Effective.MIN_PV, 2760.0, True)]
    assert battery.assess(T0, 80.0, pv, house, cars) is None
    # Die Unterstützung hat weder den SOC 80 noch den Fehlbetrag gesehen: Im Band der Hysterese
    # bleibt sie gesperrt, und die Überbrückung beginnt erst jetzt.
    found = battery.assess(at(10), 68.0, 0.0, 500.0, cars)
    assert found is not None
    assert (found.support, found.bridging_s) == ("soc", 0.0)


def test_assess_without_the_power_of_a_charging_car_is_unknown(cfg) -> None:
    cars = [BatteryCar("evcs", Effective.PV, None, None)]
    assert BatteryController(cfg).assess(T0, 80.0, 0.0, 500.0, cars) is None


# --- Override: an, aus, Änderung ------------------------------------------------------------------


def test_override_starts_at_200_w(cfg) -> None:
    battery = BatteryController(cfg)
    assert battery.decide(T0, need(190.0), **FREE) == []
    [found] = battery.decide(T0, need(204.0), **FREE)
    assert found.command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 200.0, reasons=("battery_comp",)
    )
    assert found.inputs == {
        "pv_w": 0.0,
        "house_w": 500.0,
        "cars_w": 204.0,
        "deficit_w": 204.0,
        "allowed_w": 0.0,
        "target_w": 204.0,
        "actual_setpoint_w": None,
    }


def test_override_follows_only_changes_of_200_w(cfg) -> None:
    battery = holding(cfg, 2000.0)
    assert battery.decide(at(1), need(2150.0), **FREE) == []
    [found] = battery.decide(at(2), need(2200.0), **FREE)
    assert found.command == setpoint(2200.0, "battery_comp")
    accept(battery, [found])
    assert battery.decide(at(3), need(2390.0), **FREE) == []
    assert battery.decide(at(4), need(2010.0), **FREE) == []  # nach unten ebenso
    [found] = battery.decide(at(5), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp")


def test_a_change_waits_for_a_verdict_with_value(cfg) -> None:
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(1), need(2200.0), **FREE)
    battery.note_verdict(found.command, Verdict(found.command, Outcome.DEFERRED, 2200.0, (), True))
    [again] = battery.decide(at(2), need(2390.0), **FREE)  # gemessen am eigenen Wert 2000
    assert again.command == setpoint(2390.0, "battery_comp")


def test_override_falls_away_below_100_w(cfg) -> None:
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(1), need(100.0), **FREE)
    assert found.command == setpoint(100.0, "battery_comp")
    accept(battery, [found])
    [found] = battery.decide(at(2), need(99.0), **FREE)
    assert found.command == setpoint(None, "battery_comp")
    accept(battery, [found])
    assert battery.health()["override_w"] is None
    assert battery.health()["refresh_age_s"] is None  # kein eigener Override: nichts aufzufrischen
    assert battery.decide(at(3), need(99.0), **FREE) == []
    assert battery.decide(at(4), need(199.0), **FREE) == []  # „kein Override“: erst ab 200 W
    [found] = battery.decide(at(5), need(200.0), **FREE)
    assert found.command == setpoint(200.0, "battery_comp")


def test_no_override_is_lifted_only_when_there_is_one(cfg) -> None:
    battery = BatteryController(cfg)
    assert battery.decide(T0, need(0.0), **FREE) == []
    assert battery.decide(at(1), need(0.0), **{**FREE, "present": {}}) == []
    foreign = {**FREE, "present": {SETPOINT: 1500.0}}
    [found] = battery.decide(at(2), need(0.0), **foreign)
    assert found.command == setpoint(None, "battery_comp")
    assert found.inputs["actual_setpoint_w"] == 1500.0
    accept(battery, [found])
    # Der eigene Wert ist „kein Override“: Er geht nicht noch einmal hinaus, auch wenn der Cerbo
    # weiter einen meldet.
    assert battery.decide(at(3), need(0.0), **foreign) == []


def test_target_adds_the_base_and_rounds_to_10_w(cfg) -> None:
    based = {**FREE, "base_w": 50.0}
    [found] = BatteryController(cfg).decide(T0, need(1234.0), **based)
    assert found.command.value == 1280.0
    [found] = BatteryController(cfg).decide(T0, need(1235.0), **based)
    assert found.command.value == 1290.0  # halbe Zehner aufwärts
    assert found.inputs["target_w"] == 1235.0  # W' ohne die Basis
    [found] = BatteryController(cfg).decide(T0, need(1234.0), **{**FREE, "base_w": -1500.0})
    assert found.command.value == -270.0  # was negativ ist, begrenzt Regel 2 der Safety-Schicht
    for unknown in (None, math.nan, math.inf):
        battery = BatteryController(cfg)
        assert battery.decide(T0, need(1234.0), **{**FREE, "base_w": unknown}) == []
        assert battery.health()["state"] == "unknown"


def test_thresholds_follow_the_config(cfg) -> None:
    battery = BatteryController(with_control(cfg, setpoint_deadband_w=400.0))
    assert battery.decide(T0, need(399.0), **FREE) == []
    [found] = battery.decide(at(1), need(400.0), **FREE)
    accept(battery, [found])
    assert battery.decide(at(2), need(790.0), **FREE) == []
    assert battery.decide(at(3), need(200.0), **FREE) == []
    [found] = battery.decide(at(4), need(199.0), **FREE)
    assert found.command == setpoint(None, "battery_comp")


def test_support_reason_when_battery_may_help(cfg) -> None:
    [found] = BatteryController(cfg).decide(T0, need(3000.0, allowed=1000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_support")
    assert (found.inputs["deficit_w"], found.inputs["allowed_w"]) == (3000.0, 1000.0)
    assert found.inputs["target_w"] == 2000.0


def test_inputs_add_up_the_cars(cfg) -> None:
    found_need = BatteryNeed(
        800.0, 300.0, 4500.0, {"a": 1500.0, "b": 2500.0}, {"a": 500.0, "b": 0.0}, "on", 30.0
    )
    present = {SETPOINT: 1000.0}
    [found] = BatteryController(cfg).decide(T0, found_need, **{**FREE, "present": present})
    assert found.command == setpoint(3500.0, "battery_support")
    assert found.inputs == {
        "pv_w": 800.0,
        "house_w": 300.0,
        "cars_w": 4500.0,
        "deficit_w": 4000.0,
        "allowed_w": 500.0,
        "target_w": 3500.0,
        "actual_setpoint_w": 1000.0,
    }


# --- Auffrischen ----------------------------------------------------------------------------------


def test_refresh_follows_240_s_after_the_last_verdict(cfg) -> None:
    battery = BatteryController(cfg)
    accept(battery, battery.decide(T0, need(2000.0), **FREE))
    assert battery.decide(T0 + timedelta(seconds=239), need(2000.0), **FREE) == []
    [found] = battery.decide(T0 + timedelta(seconds=240), need(2050.0), **FREE)
    assert found.command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 2000.0, reasons=("battery_comp",), refresh=True
    )
    accept(battery, [found])
    assert battery.decide(T0 + timedelta(seconds=479), need(2000.0), **FREE) == []


def test_refresh_interval_follows_the_config(cfg) -> None:
    battery = BatteryController(with_control(cfg, override_refresh_s=100.0))
    accept(battery, battery.decide(T0, need(2000.0), **FREE))
    assert battery.decide(at(99), need(2000.0), **FREE) == []
    [found] = battery.decide(at(100), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp", refresh=True)


def test_refresh_carries_the_reasons_of_the_cycle(cfg) -> None:
    battery = holding(cfg, 2000.0)
    budget = {**FREE, "budget_w": 1950.0}
    [found] = battery.decide(at(240), need(3000.0, allowed=1000.0), **budget)
    assert found.command == setpoint(2000.0, "battery_support", "peak_guard", refresh=True)
    assert found.inputs["target_w"] == 1950.0


def test_a_new_value_counts_as_the_refresh(cfg) -> None:
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(240), need(2200.0), **FREE)
    assert found.command == setpoint(2200.0, "battery_comp")  # kein Auffrischen daneben
    accept(battery, [found])
    assert battery.decide(at(479), need(2200.0), **FREE) == []
    [found] = battery.decide(at(480), need(2200.0), **FREE)
    assert found.command == setpoint(2200.0, "battery_comp", refresh=True)


def test_blocked_refresh_follows_in_the_next_cycle(cfg) -> None:  # Review Focus 1
    battery = holding(cfg, 2000.0)
    assert battery.decide(at(240), need(2000.0), **{**FREE, "blocked": True}) == []
    assert battery.health()["state"] == "ok"
    [found] = battery.decide(at(241), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp", refresh=True)


@pytest.mark.parametrize("outcome", [Outcome.DEFERRED, Outcome.REJECTED, None])
def test_refresh_held_back_by_the_safety_layer_follows_in_the_next_cycle(
    cfg, outcome
) -> None:  # Review Focus 1
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(240), need(2000.0), **FREE)
    command = found.command
    if outcome is not None:
        value = None if outcome is Outcome.REJECTED else 2000.0
        battery.note_verdict(command, Verdict(command, outcome, value, (), True))
    else:
        battery.note_verdict(command, None)
    [again] = battery.decide(at(241), need(2000.0), **FREE)
    assert again.command == setpoint(2000.0, "battery_comp", refresh=True)


def test_failed_refresh_sends_the_value_again(cfg) -> None:  # Review Focus 1
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(240), need(2000.0), **FREE)
    assert found.command.refresh
    accept(battery, [found], dry_run=False)
    timeout = DecisionResult.TIMEOUT
    battery.note_result(WriteResult(VICTRON, CommandKind.GRID_SETPOINT, 2000.0, timeout, ""))
    assert battery.health()["refresh_age_s"] is None
    live = {**FREE, "present": {SETPOINT: 2000.0}}  # der Cerbo hält noch den alten Override
    [again] = battery.decide(at(241), need(2000.0), **live)
    assert again.command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 2000.0, reasons=("battery_comp",)
    )
    # Die Safety-Schicht findet ihn unverändert. Geschrieben wurde er zuletzt vor 241 s, also
    # frischt der Akku-Sollwert ihn sofort auf.
    command = again.command
    battery.note_verdict(command, Verdict(command, Outcome.UNCHANGED, 2000.0, (), False))
    [refresh] = battery.decide(at(242), need(2000.0), **live)
    assert refresh.command == setpoint(2000.0, "battery_comp", refresh=True)


def test_refresh_that_was_not_sent_sends_the_value_again(cfg) -> None:  # Review Focus 1
    battery = holding(cfg, 2000.0)
    [found] = battery.decide(at(240), need(2000.0), **FREE)
    command = found.command
    verdict = Verdict(command, Outcome.ACCEPTED, 2000.0, (), False)
    battery.note_verdict(command, verdict, not_sent=True)
    [again] = battery.decide(at(241), need(2000.0), **FREE)
    assert again.command == setpoint(2000.0, "battery_comp")


def test_override_found_unchanged_is_refreshed_at_once(cfg) -> None:  # Review Focus 1
    # Der Cerbo hält den Wert schon, etwa nach einem Neustart des core. Wann er geschrieben wurde,
    # ist unbekannt; der Akku-Sollwert frischt ihn deshalb im nächsten Zyklus auf.
    battery = BatteryController(cfg)
    live = {**FREE, "present": {SETPOINT: 2000.0}}
    [found] = battery.decide(T0, need(2000.0), **live)
    command = found.command
    battery.note_verdict(command, Verdict(command, Outcome.UNCHANGED, 2000.0, (), False))
    assert (battery.health()["override_w"], battery.health()["refresh_age_s"]) == (2000.0, None)
    [refresh] = battery.decide(at(1), need(2000.0), **live)
    assert refresh.command == setpoint(2000.0, "battery_comp", refresh=True)
    accept(battery, [refresh], dry_run=False)
    assert battery.decide(at(240), need(2000.0), **live) == []
    [again] = battery.decide(at(241), need(2000.0), **live)
    assert again.command == setpoint(2000.0, "battery_comp", refresh=True)


# --- Sperren und unbekannte Werte -----------------------------------------------------------------


@pytest.mark.parametrize("held", [{"locked": True}, {"controllable": False}])
def test_locked_victron_gets_nothing_and_forgets(cfg, held) -> None:  # Review Focus 2
    battery = holding(cfg, 2000.0)
    assert battery.decide(at(1), need(3000.0), **{**FREE, **held}) == []
    assert battery.health() == {
        "state": "locked",
        "target_w": 3000.0,
        "override_w": None,
        "support": "soc",
        "bridging_s": None,
        "refresh_age_s": None,
    }
    foreign = {**FREE, **held, "present": {SETPOINT: 2000.0}}
    assert battery.decide(at(2), need(0.0), **foreign) == []  # auch kein „kein Override“
    assert battery.decide(at(240), need(2000.0), **{**FREE, **held}) == []  # und kein Auffrischen
    [found] = battery.decide(at(241), need(2000.0), **FREE)  # ohne eigenen Wert neu hinaus
    assert found.command == Command(
        VICTRON, CommandKind.GRID_SETPOINT, 2000.0, reasons=("battery_comp",)
    )
    assert battery.health()["state"] == "ok"


def test_locked_comes_before_unknown(cfg) -> None:
    battery = BatteryController(cfg)
    battery.decide(T0, None, **{**FREE, "locked": True})
    assert battery.health()["state"] == "locked"


@pytest.mark.parametrize("unknown", ["need", "base"])
def test_unknown_need_keeps_the_override_without_refresh(cfg, unknown) -> None:  # Review Focus 3
    battery = holding(cfg, 2000.0)
    if unknown == "need":
        assert battery.decide(at(240), None, **FREE) == []
        target_w, support = None, None
    else:
        assert battery.decide(at(240), need(2000.0), **{**FREE, "base_w": None}) == []
        target_w, support = 2000.0, "soc"
    assert battery.health() == {
        "state": "unknown",
        "target_w": target_w,
        "override_w": 2000.0,
        "support": support,
        "bridging_s": None,
        "refresh_age_s": 240.0,
    }
    [found] = battery.decide(at(241), need(2050.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp", refresh=True)


def test_unknown_need_lifts_nothing(cfg) -> None:
    battery = BatteryController(cfg)
    foreign = {**FREE, "present": {SETPOINT: 1500.0}}
    assert battery.decide(T0, None, **foreign) == []
    assert battery.decide(at(1), need(0.0), **{**foreign, "base_w": None}) == []


# --- 15-min-Budget --------------------------------------------------------------------------------


def test_budget_lowers_the_target_with_the_peak_reason(cfg) -> None:
    [found] = BatteryController(cfg).decide(T0, need(5000.0), **{**FREE, "budget_w": 3000.0})
    assert found.command == setpoint(3000.0, "battery_comp", "peak_guard")
    assert found.inputs["target_w"] == 3000.0
    battery = BatteryController(cfg)
    assert battery.decide(T0, need(5000.0), **{**FREE, "budget_w": -500.0}) == []
    assert battery.health()["target_w"] == 0.0


def test_budget_counts_from_the_base(cfg) -> None:
    budget = {**FREE, "base_w": 1000.0, "budget_w": 3000.0}
    [found] = BatteryController(cfg).decide(T0, need(5000.0, allowed=1000.0), **budget)
    assert found.command == setpoint(3000.0, "battery_support", "peak_guard")  # 1000 + 2000
    assert found.inputs["target_w"] == 2000.0


def test_budget_counts_rounded_down_to_10_w(cfg) -> None:  # Präzisierung 12, Global Constraints
    # Mit dem Budget 7895,31 W lägen W' bei 7895,31 W und das Ziel auf 10 W gerundet bei 7900 W,
    # über dem Budget: Regel 2 kappte den Override auf einen Wert, der kein Vielfaches von 10 W ist.
    budget = {**FREE, "budget_w": 7895.31}
    [found] = BatteryController(cfg).decide(T0, need(9000.0), **budget)
    assert found.command == setpoint(7890.0, "battery_comp", "peak_guard")
    assert found.inputs["target_w"] == 7890.0
    # Auch mit einer Basis, die kein Vielfaches von 10 W ist, bleibt das Ziel darunter.
    based = {**budget, "base_w": 55.0}
    [found] = BatteryController(cfg).decide(T0, need(9000.0), **based)
    assert found.command == setpoint(7890.0, "battery_comp", "peak_guard")
    assert found.inputs["target_w"] == 7835.0


def test_budget_above_the_need_changes_nothing(cfg) -> None:
    [found] = BatteryController(cfg).decide(T0, need(5000.0), **{**FREE, "budget_w": 5000.0})
    assert found.command == setpoint(5000.0, "battery_comp")


def test_budget_can_lift_the_override(cfg) -> None:
    battery = holding(cfg, 3000.0)
    [found] = battery.decide(at(1), need(5000.0), **{**FREE, "budget_w": 50.0})
    assert found.command == setpoint(None, "battery_comp", "peak_guard")


@pytest.mark.parametrize("budget", [math.nan, math.inf])
def test_budget_needs_the_base_and_a_finite_value(cfg, budget) -> None:
    battery = BatteryController(cfg)
    assert battery.decide(T0, need(5000.0), **{**FREE, "budget_w": budget}) == []
    assert battery.health()["state"] == "unknown"
    unknown_base = {**FREE, "base_w": None, "budget_w": 3000.0}
    assert battery.decide(at(1), need(5000.0), **unknown_base) == []
    assert (battery.health()["state"], battery.health()["target_w"]) == ("unknown", None)


# --- Vergessen, Pause und Health ------------------------------------------------------------------


def test_verdicts_and_results_of_other_commands_change_nothing(cfg) -> None:
    # Verteidigung in der Tiefe: Der Kern gibt dem Akku-Sollwert nur den Override. Ein Urteil über
    # einen anderen Befehl setzt keinen eigenen Wert und verschiebt das Auffrischen nicht.
    battery = holding(cfg, 2000.0)
    assert battery.decide(at(100), need(2000.0), **FREE) == []
    current = Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    battery.note_verdict(current, Verdict(current, Outcome.ACCEPTED, 10.0, (), True))
    limit = Command(VICTRON, CommandKind.MAX_DISCHARGE, 2000.0)
    battery.note_verdict(limit, Verdict(limit, Outcome.ACCEPTED, 2000.0, (), False))
    error = DecisionResult.ERROR
    battery.note_result(WriteResult("wallbox.evcs", CommandKind.CURRENT, 10.0, error, ""))
    battery.note_result(WriteResult(VICTRON, CommandKind.MAX_DISCHARGE, 2000.0, error, ""))
    assert (battery.health()["override_w"], battery.health()["refresh_age_s"]) == (2000.0, 100.0)
    [found] = battery.decide(at(240), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp", refresh=True)


def test_forget_drops_the_own_value_of_victron(cfg) -> None:
    battery = holding(cfg, 2000.0)
    battery.forget({"wallbox.evcs"})
    assert battery.decide(at(1), need(2000.0), **FREE) == []
    battery.forget({VICTRON})
    assert (battery.health()["override_w"], battery.health()["refresh_age_s"]) == (None, None)
    [found] = battery.decide(at(2), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp")


def test_idle_forgets_the_values_and_starts_the_support_over(cfg) -> None:
    battery = BatteryController(cfg)
    cars = [BatteryCar("evcs", Effective.MIN_PV, 2760.0, True)]
    first = battery.assess(T0, 80.0, 0.0, 500.0, cars)
    assert first is not None
    assert first.support == "on"
    accept(battery, battery.decide(T0, need(2000.0), **FREE))
    battery.idle()
    assert battery.health() == IDLE
    later = battery.assess(at(10), 68.0, 0.0, 500.0, cars)
    assert later is not None
    assert (later.support, later.bridging_s) == ("soc", 0.0)  # 70 % wieder erreichen, neu
    [found] = battery.decide(at(10), need(2000.0), **FREE)
    assert found.command == setpoint(2000.0, "battery_comp")


def test_health_of_the_battery(cfg) -> None:
    battery = BatteryController(cfg)
    assert battery.health() == IDLE
    accept(battery, battery.decide(T0, need(2000.0), **FREE))
    assert battery.decide(T0 + timedelta(seconds=10), need(2000.0), **FREE) == []
    assert battery.health() == {
        "state": "ok",
        "target_w": 2000.0,
        "override_w": 2000.0,
        "support": "soc",
        "bridging_s": None,
        "refresh_age_s": 10.0,
    }
    bridging = BatteryNeed(0.0, 0.0, 2100.0, {"evcs": 2100.0}, {"evcs": 0.0}, "time", 950.0)
    assert battery.decide(at(11), bridging, **FREE) == []
    found = battery.health()
    assert (found["target_w"], found["support"], found["bridging_s"]) == (2100.0, "time", 950.0)
