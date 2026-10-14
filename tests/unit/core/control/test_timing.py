from datetime import timedelta

from tests.helpers import T0

from bindaems.core.control.timing import held_since


def test_held_since_keeps_the_start_while_it_holds() -> None:
    assert held_since(None, True, T0) == T0
    assert held_since(T0, True, T0 + timedelta(seconds=5)) == T0
    assert held_since(T0, False, T0 + timedelta(seconds=6)) is None


def test_held_since_does_not_start_without_the_condition() -> None:
    assert held_since(None, False, T0) is None
