from bindaems.core.safety import types as safety_types
from bindaems.shared.numbers import de


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
