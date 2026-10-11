import pytest

from bindaems.core.safety import types as safety_types
from bindaems.shared.numbers import de, finite_number


def test_de_formats_german() -> None:
    assert (de(10.7), de(-300.0), de(2070.0), de(22770.0)) == ("10,7", "−300", "2070", "22770")


def test_de_keeps_ten_significant_digits() -> None:
    assert de(1 / 3) == "0,3333333333"
    assert de(0.1 + 0.2) == "0,3"
    assert de(20.5) == "20,5"
    assert de(0.0) == "0"


def test_de_uses_the_real_minus_sign() -> None:
    assert de(-1.5) == "\N{MINUS SIGN}1,5"
    assert "-" not in de(-1.5)


def test_safety_types_still_offers_the_same_de() -> None:
    # Bestehende Importe ``from bindaems.core.safety.types import de`` bleiben gültig.
    assert safety_types.de is de


# finite_number: die Zahlenregel, wie sie die Safety-Regeln und die Entscheidungen nutzen


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, 10.0),  # ein int ist eine Zahl; heraus kommt ein float
        (-3, -3.0),
        (0, 0.0),
        (10.5, 10.5),
        (-0.0, -0.0),
        (10**300, 1e300),  # sehr groß, aber noch ein float
    ],
    ids=["int", "negative-int", "zero", "float", "negative-zero", "huge-int"],
)
def test_finite_number_accepts_finite_numbers(value: object, expected: float) -> None:
    number = finite_number(value)
    assert number == expected
    assert type(number) is float


@pytest.mark.parametrize(
    "value",
    [
        True,  # bool ist keine Zahl
        False,
        None,
        "10",
        b"10",
        [10],
        float("nan"),
        float("inf"),
        float("-inf"),
        pytest.param(10**400, id="int-too-large"),  # größer als jedes float
        pytest.param(-(10**400), id="int-too-small"),
    ],
)
def test_finite_number_rejects_everything_else(value: object) -> None:
    assert finite_number(value) is None


def test_safety_types_still_offers_the_same_finite_number() -> None:
    # Bestehende Importe ``from bindaems.core.safety.types import finite_number`` bleiben gültig.
    assert safety_types.finite_number is finite_number
