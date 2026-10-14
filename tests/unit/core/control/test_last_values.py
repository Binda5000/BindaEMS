import pytest

from bindaems.core.control.charging import ChargeCommand
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, LastValues
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import VICTRON, Command, CommandKind
from bindaems.shared.decisions import DecisionResult

CURRENT = ("wallbox.evcs", CommandKind.CURRENT)
SETPOINT = (VICTRON, CommandKind.GRID_SETPOINT)
TESLA = ("vehicle.tesla", CommandKind.CURRENT)


def judge(last: LastValues, command: Command, outcome: Outcome = Outcome.ACCEPTED) -> bool:
    return last.judged(command, Verdict(command, outcome, command.value, (), True), not_sent=False)


def written(value: float, result: DecisionResult, *, not_taken: bool = False) -> WriteResult:
    """Das Ergebnis eines Stroms an den Tesla."""
    return WriteResult("vehicle.tesla", CommandKind.CURRENT, value, result, "", not_taken=not_taken)


def test_a_verdict_with_value_sets_the_last_value() -> None:
    last, command = LastValues(), Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    assert (last.holds(CURRENT), last.changed(CURRENT, 10.0)) == (False, True)
    assert judge(last, command)
    assert (last.holds(CURRENT), last.requested(CURRENT)) == (True, 10.0)
    assert not last.changed(CURRENT, 10.0)
    assert last.changed(CURRENT, 8.0)


@pytest.mark.parametrize(
    ("outcome", "sets"),
    [(Outcome.DEFERRED, False), (Outcome.REJECTED, False), (Outcome.UNCHANGED, True)],
)
def test_deferred_and_rejected_set_nothing_unchanged_does(outcome: Outcome, sets: bool) -> None:
    last, command = LastValues(), Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    assert judge(last, command, outcome) is sets
    assert last.holds(CURRENT) is sets


def test_no_verdict_sets_nothing() -> None:
    last, command = LastValues(), Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    assert not last.judged(command, None, not_sent=False)
    assert not last.holds(CURRENT)


def test_not_sent_clears_the_last_value() -> None:
    last, command = LastValues(), Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    judge(last, command)
    verdict = Verdict(command, Outcome.ACCEPTED, 10.0, (), False)
    assert not last.judged(command, verdict, not_sent=True)
    assert not last.holds(CURRENT)


def test_not_taken_clears_the_value_and_marks_the_retry() -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 5.0)
    judge(last, command)
    last.finished(written(5.0, DecisionResult.ERROR, not_taken=True))
    assert not last.holds(TESLA)
    assert last.retry(TESLA)
    retried = last.command("vehicle.tesla", CommandKind.CURRENT, 5.0, ("mode_pv",), urgent=True)
    assert retried == Command(
        "vehicle.tesla", CommandKind.CURRENT, 5.0, reasons=("mode_pv", "not_taken_retry")
    )


def test_a_new_verdict_ends_the_retry() -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 5.0)
    judge(last, command)
    last.finished(written(5.0, DecisionResult.ERROR, not_taken=True))
    assert judge(last, command)
    assert not last.retry(TESLA)
    again = last.command("vehicle.tesla", CommandKind.CURRENT, 5.0, ("mode_pv",))
    assert again.reasons == ("mode_pv",)


@pytest.mark.parametrize(
    ("result", "clears"),
    [
        (DecisionResult.OK, False),
        (DecisionResult.ERROR, True),
        (DecisionResult.TIMEOUT, True),
        (DecisionResult.NOT_SENT, True),
    ],
)
def test_only_a_failed_result_clears_the_value(result: DecisionResult, clears: bool) -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 5.0)
    judge(last, command)
    last.finished(written(5.0, result))
    assert last.holds(TESLA) is not clears


def test_a_plain_failure_asks_for_no_retry() -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 5.0)
    judge(last, command)
    last.finished(written(5.0, DecisionResult.TIMEOUT))
    assert (last.holds(TESLA), last.retry(TESLA)) == (False, False)


def test_a_result_for_another_value_changes_nothing() -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 10.0)
    judge(last, command)
    last.finished(written(8.0, DecisionResult.ERROR, not_taken=True))
    assert (last.holds(TESLA), last.requested(TESLA), last.retry(TESLA)) == (True, 10.0, False)


def test_a_command_without_retry_keeps_urgent_and_wake() -> None:
    command = LastValues().command(
        "vehicle.tesla", CommandKind.CHARGING, True, ("mode_now",), urgent=True, wake=True
    )
    assert command == Command(
        "vehicle.tesla", CommandKind.CHARGING, True, urgent=True, reasons=("mode_now",), wake=True
    )


def test_holds_tells_no_override_from_no_value() -> None:
    last = LastValues()
    judge(last, Command(VICTRON, CommandKind.GRID_SETPOINT, None))
    assert (last.holds(SETPOINT), last.requested(SETPOINT)) == (True, None)
    last.forget({VICTRON})
    assert (last.holds(SETPOINT), last.requested(SETPOINT)) == (False, None)


def test_forget_names_the_actuators() -> None:
    last = LastValues()
    judge(last, Command("wallbox.evcs", CommandKind.CURRENT, 10.0))
    judge(last, Command("vehicle.tesla", CommandKind.CURRENT, 5.0))
    last.finished(written(5.0, DecisionResult.ERROR, not_taken=True))  # Tesla: nur der Merker
    last.forget({"vehicle.tesla"})
    assert (last.holds(CURRENT), last.retry(TESLA)) == (True, False)
    last.forget({"wallbox.evcs"})
    assert not last.holds(CURRENT)


def test_forget_without_actuators_forgets_everything() -> None:
    last = LastValues()
    judge(last, Command("wallbox.evcs", CommandKind.CURRENT, 10.0))
    judge(last, Command("vehicle.tesla", CommandKind.CURRENT, 5.0))
    last.finished(written(5.0, DecisionResult.ERROR, not_taken=True))
    last.forget()
    assert (last.holds(CURRENT), last.holds(TESLA), last.retry(TESLA)) == (False, False, False)


def test_charge_command_stays_a_name_of_the_control_command() -> None:
    assert ChargeCommand is ControlCommand
