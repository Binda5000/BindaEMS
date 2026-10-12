from dataclasses import FrozenInstanceError

import pytest
from tests.helpers import T0
from tests.unit.core.control.fakes import FakeActuator

from bindaems.core.control.actuator import Actuator, Capabilities, Confirmation, at_most, matches
from bindaems.shared.control import CommandKind

K = CommandKind
NAN, INF = float("nan"), float("inf")


def test_confirmation_values() -> None:
    assert [(c.name, c.value) for c in Confirmation] == [
        ("PENDING", "pending"),
        ("CONFIRMED", "confirmed"),
        ("FAILED", "failed"),
    ]


def test_capabilities_have_no_reasons_by_default_and_are_frozen() -> None:
    capabilities = Capabilities(True)
    assert (capabilities.controllable, capabilities.reasons) == (True, ())
    with pytest.raises(FrozenInstanceError):
        capabilities.controllable = False


def test_the_protocol_asks_every_actuator_for_its_alarms() -> None:
    # Der Tesla meldet damit „nach Fehlern nicht steuerbar“; EVCS und Victron haben keine eigenen.
    assert callable(getattr(Actuator, "alarms", None))


def test_the_fake_actuator_has_no_alarms() -> None:
    assert FakeActuator("wallbox.evcs").alarms(T0) == []


@pytest.mark.parametrize(
    ("kind", "wanted", "actual"),
    [
        (K.CURRENT, 16.0, 16.0),
        (K.CURRENT, 16.0, 16),  # ein Gerät liefert ganze Ampere
        (K.CURRENT, 16.0, 16.4),
        (K.CURRENT, 16.0, 15.6),
        (K.CURRENT, 6.0, 6.4),  # der Rand zählt, auch wenn 6.4 − 6.0 etwas über 0.4 liegt
        (K.CHARGING, True, True),
        (K.CHARGING, False, False),
        (K.GRID_SETPOINT, 1000.0, 1000.0),
        (K.GRID_SETPOINT, 1000.0, 1001.0),
        (K.GRID_SETPOINT, 1000.0, 999.0),
        (K.GRID_SETPOINT, None, None),
        (K.MAX_DISCHARGE, -1.0, -1.0),
        (K.MAX_DISCHARGE, -1.0, -1),
        (K.MAX_DISCHARGE, 2000.0, 2001.0),
        (K.MAX_DISCHARGE, 2000.0, 1999.0),
        (K.MAX_DISCHARGE, 0.0, 1.0),  # ohne Sentinel gilt das Band um jeden Leistungswert
        (K.MIN_SOC, 20.0, 20.0),
        (K.MIN_SOC, 20.0, 20.5),
        (K.MIN_SOC, 20.0, 19.5),
    ],
)
def test_values_within_the_tolerance_match(kind, wanted, actual) -> None:
    assert matches(kind, wanted, actual) is True


@pytest.mark.parametrize(
    ("kind", "wanted", "actual"),
    [
        (K.CURRENT, 16.0, 16.41),
        (K.CURRENT, 16.0, 15.59),
        (K.CURRENT, 16.0, 17.0),
        (K.CHARGING, True, False),
        (K.CHARGING, False, True),
        (K.GRID_SETPOINT, 1000.0, 1001.5),
        (K.GRID_SETPOINT, 1000.0, 998.0),
        (K.MAX_DISCHARGE, 2000.0, 2001.5),
        # „unbegrenzt“ (−1) ist kein Leistungswert: Es passt nur zu sich selbst, nicht zu 0 W
        # (Entladen gesperrt), obwohl beide nur 1 W auseinanderliegen
        (K.MAX_DISCHARGE, -1.0, 0.0),
        (K.MAX_DISCHARGE, 0.0, -1.0),
        (K.MAX_DISCHARGE, -1.0, -0.5),
        (K.MAX_DISCHARGE, -1.0, -2.0),
        (K.MIN_SOC, 20.0, 20.6),
        (K.MIN_SOC, 20.0, 19.4),
    ],
)
def test_values_outside_the_tolerance_do_not_match(kind, wanted, actual) -> None:
    assert matches(kind, wanted, actual) is False


@pytest.mark.parametrize(
    ("kind", "wanted", "actual"),
    [
        # ``None`` passt nur zu ``None`` und gilt nur beim Netz-Sollwert (kein Override)
        (K.GRID_SETPOINT, None, 0.0),
        (K.GRID_SETPOINT, 0.0, None),
        (K.GRID_SETPOINT, None, False),
        (K.GRID_SETPOINT, None, NAN),
        (K.CURRENT, 16.0, None),
        (K.CURRENT, None, 16.0),
        (K.CURRENT, None, None),
        (K.CHARGING, None, None),
        (K.MAX_DISCHARGE, None, None),
        (K.MIN_SOC, None, None),
        # Werte vom falschen Typ passen nie, auch nicht zu sich selbst
        (K.CURRENT, True, 1.0),
        (K.CURRENT, 1.0, True),
        (K.CURRENT, True, True),
        (K.CURRENT, "16", 16.0),
        (K.CURRENT, 16.0, "16"),
        (K.CURRENT, "16", "16"),
        (K.MIN_SOC, False, 0.0),
        (K.CHARGING, True, 1),
        (K.CHARGING, 1, True),
        (K.CHARGING, 1.0, 1.0),
        (K.CHARGING, "True", True),
        (K.GRID_SETPOINT, True, 1.0),
        # NaN und ∞ sind keine Zahlen, auch nicht gleich
        (K.CURRENT, NAN, NAN),
        (K.CURRENT, 16.0, NAN),
        (K.CURRENT, INF, INF),
        (K.MAX_DISCHARGE, -INF, -INF),
    ],
)
def test_values_of_the_wrong_type_never_match(kind, wanted, actual) -> None:
    assert matches(kind, wanted, actual) is False


# Höchstens der Grenzwert (der sichere Zustand beim Strom): die Toleranz von ``matches`` nach oben


@pytest.mark.parametrize(
    ("actual", "result"),
    [
        (10.0, True),
        (8.0, True),  # weniger Strom
        (0.0, True),
        (10.4, True),  # der Rand der Toleranz gehört dazu
        (10.41, False),
        (12.0, False),
        (None, False),
        (True, False),
        (NAN, False),
        (-INF, False),
        ("8", False),
    ],
)
def test_at_most_allows_lower_values_and_the_tolerance_above(actual, result) -> None:
    assert at_most(K.CURRENT, 10.0, actual) is result


def test_at_most_needs_a_number_as_limit() -> None:
    assert at_most(K.CURRENT, None, 8.0) is False
    assert at_most(K.CURRENT, NAN, 8.0) is False
