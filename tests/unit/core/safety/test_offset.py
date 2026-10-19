"""Schrittregel für den Sollwertversatz der Klimaanlage (Plan 6a, Entscheidung 8)."""

import pytest

from bindaems.core.safety.limits import OffsetRange
from bindaems.core.safety.offset import OffsetSteps
from bindaems.core.safety.types import Finding
from bindaems.shared.control import Command, CommandKind

KLIMA = "thermal.klima"
RANGES = {KLIMA: OffsetRange(max_k=3.0, step_k=0.5, interval_s=600.0)}
STEP = Finding("offset.step", "Versatz nur eine Stufe je 10 min weg von 0.")
SIGN = Finding("offset.step", "Vorzeichenwechsel nur über 0.")


def cmd(value) -> Command:
    return Command(KLIMA, CommandKind.SETPOINT_OFFSET, value)


@pytest.fixture
def steps() -> OffsetSteps:
    return OffsetSteps(RANGES)


def test_step_one_away_from_zero(steps) -> None:
    assert steps.check(cmd(0.5), present_k=0.0, now=0) is None
    steps.record(cmd(0.5), 0.0, 0)
    assert steps.check(cmd(1.0), present_k=0.5, now=599) == STEP
    assert steps.check(cmd(1.0), present_k=0.5, now=600) is None
    assert steps.check(cmd(1.5), present_k=0.5, now=900) == STEP  # zwei Stufen


def test_negative_side_steps_the_same_way(steps) -> None:
    assert steps.check(cmd(-0.5), present_k=0.0, now=0) is None
    assert steps.check(cmd(-1.0), present_k=0.0, now=0) == STEP
    steps.record(cmd(-0.5), 0.0, 0)
    assert steps.check(cmd(-1.0), present_k=-0.5, now=300) == STEP
    assert steps.check(cmd(-1.0), present_k=-0.5, now=600) is None


def test_back_to_zero_always(steps) -> None:
    assert steps.check(cmd(0.0), present_k=3.0, now=0) is None
    assert steps.check(cmd(0.0), present_k=-3.0, now=0) is None


def test_decrease_always_allowed(steps) -> None:
    steps.record(cmd(1.0), 0.5, 0)
    assert steps.check(cmd(0.5), present_k=1.0, now=1) is None
    assert steps.check(cmd(-1.0), present_k=-3.0, now=1) is None
    assert steps.check(cmd(2.0), present_k=2.0, now=1) is None  # gleich bleibt erlaubt


def test_sign_change_rejected(steps) -> None:
    assert steps.check(cmd(-0.5), present_k=1.0, now=10_000) == SIGN
    assert steps.check(cmd(0.5), present_k=-0.5, now=10_000) == SIGN


def test_unknown_echo_counts_as_zero(steps) -> None:
    assert steps.check(cmd(0.5), present_k=None, now=0) is None
    assert steps.check(cmd(1.0), present_k=None, now=0) == STEP
    assert steps.check(cmd(-0.5), present_k=None, now=0) is None


@pytest.mark.parametrize("echo", [0.04, -0.05])
def test_echo_near_zero_counts_as_zero(steps, echo) -> None:
    # Das Echo kommt in Zehnteln; innerhalb der Toleranz von 0,05 K gilt es als 0
    assert steps.check(cmd(-0.5), present_k=echo, now=0) is None
    assert steps.check(cmd(0.5), present_k=echo, now=0) is None


def test_echo_within_tolerance_is_no_increase(steps) -> None:
    steps.record(cmd(1.0), 0.5, 0)
    assert steps.check(cmd(1.0), present_k=0.96, now=10) is None
    steps.record(cmd(1.0), 0.96, 10)  # keine Erhöhung: die Zeit der letzten bleibt 0
    assert steps.check(cmd(1.5), present_k=1.0, now=600) is None


def test_record_of_a_decrease_keeps_the_time_of_the_last_increase(steps) -> None:
    steps.record(cmd(1.0), 0.5, 0)
    steps.record(cmd(0.5), 1.0, 100)
    assert steps.check(cmd(1.0), present_k=0.5, now=500) == STEP
    assert steps.check(cmd(1.0), present_k=0.5, now=600) is None


def test_unknown_or_unusable_echo_counts_as_zero(steps) -> None:
    assert steps.check(cmd(0.5), present_k=float("nan"), now=0) is None
    assert steps.check(cmd(1.0), present_k=float("inf"), now=0) == STEP


def test_invalid_value_is_rejected(steps) -> None:
    finding = steps.check(cmd(True), present_k=0.0, now=0)
    assert finding is not None and finding.code == "value.invalid"
    assert steps.check(cmd(None), present_k=0.0, now=0) is not None


def test_dry_run_checks_against_last_decided(steps) -> None:
    # Im Dry-Run bleibt das Echo 0; die Regel prüft gegen den zuletzt entschiedenen Wert
    assert steps.check(cmd(0.5), present_k=0.0, now=0, dry_run=True) is None
    steps.record(cmd(0.5), 0.0, 0, dry_run=True)
    assert steps.check(cmd(1.0), present_k=0.0, now=599, dry_run=True) == STEP
    assert steps.check(cmd(1.0), present_k=0.0, now=600, dry_run=True) is None
    steps.record(cmd(1.0), 0.0, 600, dry_run=True)
    assert steps.check(cmd(1.5), present_k=0.0, now=1200, dry_run=True) is None
    assert steps.check(cmd(2.0), present_k=0.0, now=1200, dry_run=True) == STEP
    assert steps.check(cmd(-0.5), present_k=0.0, now=5000, dry_run=True) == SIGN


def test_dry_run_without_a_decision_starts_at_zero(steps) -> None:
    assert steps.check(cmd(1.0), present_k=2.0, now=0, dry_run=True) == STEP
    assert steps.check(cmd(0.5), present_k=-2.0, now=0, dry_run=True) is None


def test_live_checks_against_the_echo_after_dry_run(steps) -> None:
    steps.record(cmd(0.5), 0.0, 0, dry_run=True)
    steps.record(cmd(1.0), 0.0, 600, dry_run=True)
    # live gilt wieder das Echo: Der Adapter hat im Dry-Run 0 gesendet
    assert steps.check(cmd(1.0), present_k=0.0, now=1200) == STEP
    assert steps.check(cmd(0.5), present_k=0.0, now=1200) is None


def test_actuators_are_independent() -> None:
    other = "thermal.buero"
    steps = OffsetSteps({**RANGES, other: OffsetRange(3.0, 0.5, 600.0)})
    steps.record(cmd(0.5), 0.0, 0)
    second = Command(other, CommandKind.SETPOINT_OFFSET, 0.5)
    assert steps.check(second, present_k=0.0, now=1) is None


def test_interval_text_follows_the_configuration() -> None:
    steps = OffsetSteps({KLIMA: OffsetRange(3.0, 0.5, 90.0)})
    finding = steps.check(cmd(1.0), present_k=0.0, now=0)
    assert finding == Finding("offset.step", "Versatz nur eine Stufe je 1,5 min weg von 0.")


def test_record_ignores_an_invalid_value(steps) -> None:
    steps.record(cmd(None), 0.0, 0, dry_run=True)
    steps.record(cmd(float("nan")), 0.0, 0)
    assert steps.check(cmd(0.5), present_k=0.0, now=1, dry_run=True) is None
    assert steps.check(cmd(0.5), present_k=0.0, now=1) is None
