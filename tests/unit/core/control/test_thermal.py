"""Betriebsart der Klimaanlage aus ihren Zuständen (Plan 6a, Entscheidung 11)."""

import pytest
from tests.helpers import snap

from bindaems.core.control.thermal import thermal_mode
from bindaems.shared.domain import Quality, SignalKind, Value

P = "thermal.klima."


def states(**values: Value) -> dict[str, Value]:
    return {P + name: value for name, value in values.items()}


def mode(**values: Value) -> str:
    return thermal_mode(snap(states(**values), kind=SignalKind.STATE, source="klima"), "klima")


def test_not_running_is_off() -> None:
    assert mode(running=False, cooling=True) == "aus"
    assert mode(running=False) == "aus"


@pytest.mark.parametrize(
    ("heating", "venting", "cooling", "expected"),
    [
        (True, False, False, "heizen"),
        (False, True, False, "lueften"),
        (False, False, True, "kuehlen"),
        # widersprüchliche Bits: die Reihenfolge der Vorgabe entscheidet (Review Focus 2)
        (True, False, True, "heizen"),
        (True, True, True, "heizen"),
        (False, True, True, "lueften"),
        # läuft ohne gesetzte Betriebsart
        (False, False, False, "unbekannt"),
    ],
)
def test_running_follows_the_order(
    heating: bool, venting: bool, cooling: bool, expected: str
) -> None:
    assert mode(running=True, heating=heating, venting=venting, cooling=cooling) == expected


def test_unknown_values_give_unknown() -> None:
    assert mode() == "unbekannt"
    assert mode(heating=True) == "unbekannt"  # ``running`` fehlt
    assert mode(running=True) == "unbekannt"  # alle drei fehlen
    # ein unbekanntes Bit vor dem gesetzten: es könnte vorgehen
    assert mode(running=True, venting=True) == "unbekannt"
    assert mode(running=True, heating=False, venting=False) == "unbekannt"
    # ein gesetztes Bit vor dem unbekannten entscheidet
    assert mode(running=True, heating=True) == "heizen"


def test_only_ok_booleans_count() -> None:
    stale = snap(
        states(running=True, cooling=True),
        kind=SignalKind.STATE,
        quality=Quality.STALE,
        source="klima",
    )
    assert thermal_mode(stale, "klima") == "unbekannt"
    assert mode(running=1, heating=False, venting=False, cooling=True) == "unbekannt"
    assert mode(running=True, heating=0, venting=False, cooling=True) == "unbekannt"


def test_other_device_is_not_read() -> None:
    assert thermal_mode(snap(states(running=True, cooling=True)), "andere") == "unbekannt"
