import pytest

from bindaems.core.safety.types import Finding, finite_number, invalid_value
from bindaems.shared.control import CommandKind


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10, 10.0),  # ein int ist eine Zahl; heraus kommt ein float
        (-3, -3.0),
        (0, 0.0),
        (10.5, 10.5),
        (10**300, 1e300),  # sehr groß, aber noch ein float
    ],
    ids=["int", "negative-int", "zero", "float", "huge-int"],
)
def test_finite_number_accepts_finite_numbers(value, expected) -> None:
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
def test_finite_number_rejects_everything_else(value) -> None:
    assert finite_number(value) is None


def test_invalid_value_finding_names_kind_and_value() -> None:
    assert invalid_value(CommandKind.CURRENT, True) == Finding(
        "value.invalid", "Ungültiger Wert für „current“: True."
    )
    assert invalid_value(CommandKind.MIN_SOC, "10").text == "Ungültiger Wert für „min_soc“: '10'."
    assert invalid_value("bogus", None).text == "Ungültiger Wert für „bogus“: None."
    assert invalid_value(CommandKind.GRID_SETPOINT, float("nan")).text == (
        "Ungültiger Wert für „grid_setpoint“: nan."
    )


def test_invalid_value_text_is_truncated() -> None:
    shown = repr("x" * 200)[:59] + "…"
    finding = invalid_value(CommandKind.CURRENT, "x" * 200)
    assert finding.text == f"Ungültiger Wert für „current“: {shown}."


def shown_part(value: object) -> str:
    """Die Darstellung des Werts im Text des Befunds."""
    text = invalid_value(CommandKind.CURRENT, value).text
    return text.removeprefix("Ungültiger Wert für „current“: ").removesuffix(".")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x" * 58, repr("x" * 58)),  # die Darstellung hat genau 60 Zeichen: unverändert
        ("x" * 59, repr("x" * 59)[:59] + "…"),  # 61 Zeichen: die ersten 59 und „…“
        (list(range(100)), repr(list(range(100)))[:59] + "…"),
        ("ä" * 100, repr("ä" * 100)[:59] + "…"),  # Zeichen, nicht Bytes
        (10**59, repr(10**59)),  # 60 Stellen
        (10**60, repr(10**60)[:59] + "…"),  # 61 Stellen
    ],
    ids=["60-chars", "61-chars", "list", "umlauts", "int-60", "int-61"],
)
def test_invalid_value_keeps_at_most_60_characters(value: object, expected: str) -> None:
    shown = shown_part(value)
    assert shown == expected
    assert len(shown) <= 60
