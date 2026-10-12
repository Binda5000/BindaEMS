from bindaems.core.control.ownership import Ownership
from bindaems.core.control.safe_state import SafeTarget, safe_values_reached
from bindaems.shared.control import CommandKind

EVCS = "wallbox.evcs"
CUR = (EVCS, CommandKind.CURRENT)


def test_handback_completes_only_after_safe_values_confirmed(limits_live) -> None:
    owners = Ownership({EVCS})
    target = SafeTarget(EVCS, "handback")
    assert owners.handing_back(frozenset()) == {EVCS}
    assert not safe_values_reached(limits_live, target, {CUR: 16.0})
    assert safe_values_reached(limits_live, target, {CUR: 6})
    owners.release(EVCS)
    assert owners.handing_back(frozenset()) == frozenset()


def test_drop_returns_only_owned() -> None:
    owners = Ownership({EVCS})
    assert owners.drop({EVCS, "victron"}) == {EVCS} and owners.owned == frozenset()


# --- Weitere Fälle ---------------------------------------------------------------------------


def test_nothing_owned_at_first() -> None:
    owners = Ownership()
    assert owners.owned == frozenset() and owners.handing_back(frozenset()) == frozenset()


def test_sent_value_makes_the_actuator_owned() -> None:
    owners = Ownership()
    owners.note_sent(EVCS)
    owners.note_sent(EVCS)
    assert owners.owned == {EVCS}
    assert isinstance(owners.owned, frozenset)


def test_wanted_actuators_are_not_handed_back() -> None:
    owners = Ownership([EVCS, "victron"])
    assert owners.handing_back(frozenset({EVCS})) == {"victron"}
    assert owners.handing_back(frozenset({EVCS, "victron", "vehicle.tesla"})) == frozenset()
    assert owners.owned == {EVCS, "victron"}  # die Abfrage ändert nichts


def test_release_of_an_actuator_that_is_not_owned_is_harmless() -> None:
    owners = Ownership({EVCS})
    owners.release("victron")
    assert owners.owned == {EVCS}


def test_drop_takes_any_iterable_and_keeps_the_rest() -> None:
    owners = Ownership({EVCS, "victron"})
    assert owners.drop(a for a in ("vehicle.tesla", "victron")) == {"victron"}
    assert owners.owned == {EVCS}
    assert owners.drop(()) == frozenset() and owners.owned == {EVCS}


def test_owned_is_a_snapshot() -> None:
    owners = Ownership({EVCS})
    before = owners.owned
    owners.release(EVCS)
    assert before == {EVCS} and owners.owned == frozenset()
