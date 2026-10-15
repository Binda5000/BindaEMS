from datetime import timedelta

from tests.helpers import T0

from bindaems.core.control.timing import held_since, interrupted, lasted


def test_held_since_keeps_the_start_while_it_holds() -> None:
    assert held_since(None, True, T0) == T0
    assert held_since(T0, True, T0 + timedelta(seconds=5)) == T0
    assert held_since(T0, False, T0 + timedelta(seconds=6)) is None


def test_held_since_does_not_start_without_the_condition() -> None:
    assert held_since(None, False, T0) is None


def test_lasted_once_the_limit_is_reached() -> None:
    assert not lasted(T0, T0 + timedelta(seconds=59.9), 60.0)
    assert lasted(T0, T0 + timedelta(seconds=60), 60.0)  # genau das Limit zählt
    assert lasted(T0, T0 + timedelta(seconds=61), 60.0)


def test_lasted_needs_a_start() -> None:
    assert not lasted(None, T0, 0.0)  # ohne Zeitpunkt nein, auch bei Limit 0
    assert not lasted(None, T0 + timedelta(hours=1), 60.0)


def test_lasted_with_limit_zero_holds_from_the_start() -> None:
    assert lasted(T0, T0, 0.0)


def test_lasted_does_not_hold_for_a_start_in_the_future() -> None:
    assert not lasted(T0 + timedelta(seconds=5), T0, 0.0)


def test_a_gap_longer_than_the_limit_interrupts() -> None:  # Gesamtreview M-2
    assert not interrupted(None, T0, 60.0)  # der erste Aufruf
    assert not interrupted(T0, T0 + timedelta(seconds=60), 60.0)  # genau das Limit nicht
    assert interrupted(T0, T0 + timedelta(seconds=60.1), 60.0)
