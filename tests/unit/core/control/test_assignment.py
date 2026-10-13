from datetime import datetime, timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import NONE, evcs, tesla, twc

from bindaems.core.control.assignment import Assignment, AssignmentKind, VehicleState
from bindaems.shared.control import Intent, VehicleIntent

# Vorgaben des Plans


def update(a, s, *, evcs_obs=None, twc_obs=None, tesla_obs=None, intent=None, accepted_at=T0):
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    when = T0 + timedelta(seconds=s)
    return a.update(when, when, intent, accepted_at, wallboxes, vehicles)


def boosted(version: int, until: datetime) -> Intent:
    return Intent(
        version=version, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)}
    )


def test_default_assignment(cfg) -> None:
    state = update(Assignment(cfg, started=T0), 0, twc_obs=twc(connected=True))
    tesla_, egolf = state.vehicles["tesla"], state.vehicles["egolf"]
    assert (tesla_.wallbox, tesla_.kind, tesla_.plugged) == ("twc", AssignmentKind.DEFAULT, True)
    assert (egolf.wallbox, egolf.plugged, state.unclear) == ("evcs", True, False)


def test_new_session_only_after_a_known_unplug(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert not update(a, 0, evcs_obs=evcs(connected=None)).vehicles["egolf"].new_session
    assert not update(a, 1).vehicles["egolf"].new_session
    update(a, 2, evcs_obs=evcs(connected=False))
    assert update(a, 3).vehicles["egolf"].new_session


def test_unknown_plug_state_keeps_the_session(cfg) -> None:  # Review Focus 3
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    update(a, 0, intent=intent)
    for s in range(1, 31):
        update(a, s, evcs_obs=evcs(connected=None), intent=intent)
    assert update(a, 31, intent=intent).vehicles["egolf"].boost_until == T0 + timedelta(hours=2)


def test_boost_ends_when_unplugged(cfg) -> None:
    a = Assignment(cfg, started=T0)
    v1 = boosted(1, T0 + timedelta(hours=2))
    assert update(a, 0, intent=v1).vehicles["egolf"].boost_until is not None
    update(a, 1, evcs_obs=evcs(connected=False), intent=v1)
    assert update(a, 2, intent=v1).vehicles["egolf"].boost_until is None
    v2 = boosted(2, T0 + timedelta(hours=2))
    assert update(a, 3, intent=v2).vehicles["egolf"].boost_until is not None


def test_boost_set_before_plugging_in_applies_to_the_next_session(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    assert (
        update(a, 0, evcs_obs=evcs(connected=False), intent=intent).vehicles["egolf"].boost_until
        is None
    )
    assert update(a, 1, intent=intent).vehicles["egolf"].boost_until is not None


def test_restored_intent_binds_only_a_plugged_car(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    earlier = T0 - timedelta(hours=1)
    update(a, 0, evcs_obs=evcs(connected=False), intent=intent, accepted_at=earlier)
    assert update(a, 1, intent=intent, accepted_at=earlier).vehicles["egolf"].boost_until is None


def test_override_moves_the_vehicle_and_frees_its_default(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    state = update(Assignment(cfg, started=T0), 0, intent=intent)
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == (
        "evcs",
        AssignmentKind.OVERRIDE,
    )
    assert state.vehicles["egolf"].wallbox is None


def at_the_evcs(a, s, intent=None):
    return update(
        a,
        s,
        twc_obs=twc(connected=False),
        tesla_obs=tesla(plugged=True, at_home=True),
        intent=intent,
    )


def test_contradiction_for_60_s_makes_it_unclear(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert not any(at_the_evcs(a, s).unclear for s in range(60))
    state = at_the_evcs(a, 60)
    assert state.unclear and state.vehicles["egolf"].kind == AssignmentKind.UNCLEAR
    assert state.vehicles["tesla"].plugged is None  # Präzisierung 4
    for s in range(61, 120):
        assert update(a, s).unclear
    assert not update(a, 121).unclear


def test_flapping_contradiction_never_turns_unclear(cfg) -> None:  # Review Focus 3
    a = Assignment(cfg, started=T0)
    for s in range(300):
        state = at_the_evcs(a, s) if (s // 30) % 2 == 0 else update(a, s)
        assert not state.unclear


def test_override_lifts_unclear_at_once(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(61):
        at_the_evcs(a, s)
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    assert not at_the_evcs(a, 61, intent).unclear


def test_a_tesla_away_is_no_contradiction(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(120):
        state = update(
            a, s, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True, at_home=False)
        )
    assert not state.unclear


def test_complete_sticks_until_unplugged(cfg) -> None:
    a = Assignment(cfg, started=T0)
    update(a, 0, evcs_obs=evcs(complete=True))
    assert update(a, 1).vehicles["egolf"].complete
    update(a, 2, evcs_obs=evcs(connected=False))
    assert not update(a, 3).vehicles["egolf"].complete


# Weitere Fälle: Festlegungen der Aufgabe


def test_entries_the_config_does_not_know_are_ignored(cfg) -> None:
    until = T0 + timedelta(hours=2)
    intent = Intent(
        version=1,
        control=True,
        vehicles={
            "ghost": VehicleIntent(boost_until=until, wallbox="evcs"),
            "egolf": VehicleIntent(boost_until=until, wallbox="nowhere"),
        },
    )
    state = update(Assignment(cfg, started=T0), 0, intent=intent)
    assert list(state.vehicles) == ["tesla", "egolf"]
    # die unbekannte Übersteuerung zählt nicht, der Boost desselben Eintrags bindet an den Standard
    egolf = state.vehicles["egolf"]
    assert (egolf.wallbox, egolf.kind, egolf.boost_until) == ("evcs", AssignmentKind.DEFAULT, until)
    # das unbekannte Fahrzeug nimmt weder der EVCS noch dem Wall Connector etwas weg
    assert (state.vehicles["tesla"].wallbox, state.unclear) == ("twc", False)


def test_new_session_is_a_one_shot_flag_per_wallbox(cfg) -> None:
    a = Assignment(cfg, started=T0)
    update(a, 0, evcs_obs=evcs(connected=False))
    state = update(a, 1, evcs_obs=evcs(connected=False), twc_obs=twc(connected=True))
    assert (state.vehicles["tesla"].new_session, state.vehicles["egolf"].new_session) == (
        True,
        False,
    )
    state = update(a, 2, evcs_obs=evcs(connected=False), twc_obs=twc(connected=True))
    assert not state.vehicles["tesla"].new_session


def test_tessie_complete_sticks_until_unplugged(cfg) -> None:
    a = Assignment(cfg, started=T0)
    update(a, 0, twc_obs=twc(connected=True), tesla_obs=tesla(plugged=True, complete=True))
    state = update(a, 1, twc_obs=twc(connected=True), tesla_obs=tesla(plugged=True, charging=True))
    assert state.vehicles["tesla"].complete and not state.vehicles["egolf"].complete
    update(a, 2)  # abgesteckt
    state = update(a, 3, twc_obs=twc(connected=True), tesla_obs=tesla(plugged=True))
    assert not state.vehicles["tesla"].complete


def test_complete_is_kept_per_vehicle(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert update(a, 0, evcs_obs=evcs(complete=True)).vehicles["egolf"].complete
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    state = update(a, 1, intent=intent)
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].complete) == ("evcs", False)


def test_a_vehicle_without_a_wallbox_has_no_values(cfg) -> None:
    intent = Intent(
        version=1,
        control=True,
        vehicles={
            "tesla": VehicleIntent(wallbox="evcs"),
            "egolf": VehicleIntent(boost_until=T0 + timedelta(hours=2)),
        },
    )
    state = update(
        Assignment(cfg, started=T0), 0, evcs_obs=evcs(charging=True, complete=True), intent=intent
    )
    assert state.vehicles["egolf"] == VehicleState(
        wallbox=None,
        kind=AssignmentKind.DEFAULT,
        plugged=None,
        charging=None,
        complete=False,
        new_session=False,
        boost_until=None,
    )
    assert state.vehicles["tesla"].charging is True  # die EVCS gehört jetzt dem Tesla


@pytest.mark.parametrize(
    ("twc_connected", "plugged", "at_home", "expected"),
    [
        (True, False, False, True),  # die Wallbox meldet ein Fahrzeug
        (True, None, None, True),
        (False, True, True, None),  # Widerspruch: unbekannt
        (False, True, False, False),  # woanders angesteckt
        (False, False, True, False),
        (False, True, None, False),  # Unbekanntes widerspricht nie
        (False, None, True, False),
        (False, None, None, False),
        (None, True, True, True),  # ohne Wert der Wallbox gilt Tessie
        (None, False, True, False),
        (None, True, False, False),
        (None, True, None, None),
        (None, None, True, None),
        (None, None, None, None),
    ],
)
def test_plugged_of_a_car_with_tessie(cfg, twc_connected, plugged, at_home, expected) -> None:
    state = update(
        Assignment(cfg, started=T0),
        0,
        twc_obs=twc(connected=twc_connected),
        tesla_obs=tesla(plugged=plugged, at_home=at_home),
    )
    assert state.vehicles["tesla"].plugged is expected


@pytest.mark.parametrize("connected", [True, False, None])
def test_plugged_of_a_car_without_tessie_is_the_wallbox_value(cfg, connected) -> None:
    wallboxes = {"evcs": evcs(connected=connected), "twc": twc()}
    vehicles = {"tesla": tesla(), "egolf": tesla(plugged=True, at_home=True)}  # nicht beachtet
    state = Assignment(cfg, started=T0).update(T0, T0, None, T0, wallboxes, vehicles)
    assert state.vehicles["egolf"].plugged is connected


@pytest.mark.parametrize(
    ("wallbox_charging", "tessie_charging"), [(False, True), (None, True), (True, False)]
)
def test_charging_comes_from_the_wallbox_not_from_tessie(
    cfg, wallbox_charging, tessie_charging
) -> None:
    state = update(
        Assignment(cfg, started=T0),
        0,
        twc_obs=twc(connected=True, charging=wallbox_charging),
        tesla_obs=tesla(plugged=True, charging=tessie_charging),
    )
    assert state.vehicles["tesla"].charging is wallbox_charging


def test_boost_ends_at_its_time_on_the_wall_clock(cfg) -> None:
    a = Assignment(cfg, started=T0)
    until = T0 + timedelta(seconds=10)
    intent = boosted(1, until)
    assert update(a, 9, intent=intent).vehicles["egolf"].boost_until == until
    assert update(a, 10, intent=intent).vehicles["egolf"].boost_until is None


def test_boost_follows_the_wall_clock_not_the_clock_of_the_safety_layer(cfg) -> None:
    wallboxes = {"evcs": evcs(), "twc": twc()}
    vehicles = {"tesla": tesla(), "egolf": NONE}
    intent = boosted(1, T0 + timedelta(hours=1))
    a = Assignment(cfg, started=T0)
    state = a.update(T0, T0 + timedelta(hours=2), intent, T0, wallboxes, vehicles)
    assert state.vehicles["egolf"].boost_until is None


def test_a_boost_in_the_past_is_no_boost(cfg) -> None:
    intent = boosted(1, T0 - timedelta(hours=1))
    state = update(Assignment(cfg, started=T0), 0, intent=intent)
    assert state.vehicles["egolf"].boost_until is None


def test_override_set_before_plugging_in_waits_for_the_session_and_ends_with_it(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})

    def seen(s, **kw):  # Wallbox und Art des Tesla, Wallbox des e-Golf, neuer Vorgang des Tesla
        state = update(a, s, intent=intent, **kw)
        tesla_, egolf = state.vehicles["tesla"], state.vehicles["egolf"]
        return tesla_.wallbox, tesla_.kind, egolf.wallbox, tesla_.new_session

    default = ("twc", AssignmentKind.DEFAULT, "evcs", False)
    assert seen(0, evcs_obs=evcs(connected=False)) == default  # wartet auf den nächsten Vorgang
    assert seen(1) == ("evcs", AssignmentKind.OVERRIDE, None, True)
    assert seen(2) == ("evcs", AssignmentKind.OVERRIDE, None, False)
    assert seen(3, evcs_obs=evcs(connected=False)) == default
    assert seen(4) == default  # die Absicht nennt die Übersteuerung noch, aber ihr Vorgang ist um


@pytest.mark.parametrize("accepted_at", [T0 - timedelta(hours=1), None])
def test_a_restored_override_binds_only_to_a_plugged_wallbox(cfg, accepted_at) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})

    def kind(first_evcs):
        a = Assignment(cfg, started=T0)
        update(a, 0, evcs_obs=first_evcs, intent=intent, accepted_at=accepted_at)
        return update(a, 1, intent=intent, accepted_at=accepted_at).vehicles["tesla"].kind

    assert kind(evcs(connected=True)) == AssignmentKind.OVERRIDE
    assert kind(evcs(connected=False)) == AssignmentKind.DEFAULT
    # unbekannt bei der ersten Auswertung: auch beim späteren Anstecken keine Bindung
    assert kind(evcs(connected=None)) == AssignmentKind.DEFAULT


@pytest.mark.parametrize(
    "unknown",
    [
        {"twc_obs": twc(connected=None)},
        {"evcs_obs": evcs(connected=None)},
        {"tesla_obs": tesla(plugged=None, at_home=True)},
        {"tesla_obs": tesla(plugged=True, at_home=None)},
    ],
)
def test_unknown_values_never_contradict(cfg, unknown) -> None:
    contradiction = {
        "twc_obs": twc(connected=False),
        "evcs_obs": evcs(),
        "tesla_obs": tesla(plugged=True, at_home=True),
    }
    a = Assignment(cfg, started=T0)
    assert not any(update(a, s, **(contradiction | unknown)).unclear for s in range(300))


def test_no_contradiction_without_a_car_at_the_evcs(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(300):
        state = update(
            a,
            s,
            twc_obs=twc(connected=False),
            evcs_obs=evcs(connected=False),
            tesla_obs=tesla(plugged=True, at_home=True),
        )
        assert not state.unclear


def test_an_unknown_value_interrupts_the_contradiction(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(59):
        at_the_evcs(a, s)
    update(a, 59, twc_obs=twc(connected=None), tesla_obs=tesla(plugged=True, at_home=True))
    assert not any(at_the_evcs(a, s).unclear for s in range(60, 120))
    assert at_the_evcs(a, 120).unclear


def test_boost_set_while_the_plug_state_is_unknown_binds_to_the_first_car(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    state = update(a, 0, evcs_obs=evcs(connected=None), intent=intent)
    assert state.vehicles["egolf"].boost_until is None
    egolf = update(a, 1, intent=intent).vehicles["egolf"]
    # das erste bekannte Fahrzeug ist der nächste Vorgang, aber kein neuer nach bekanntem Abstecken
    assert (egolf.boost_until, egolf.new_session) == (T0 + timedelta(hours=2), False)


def test_an_override_interrupts_the_contradiction(cfg) -> None:
    a = Assignment(cfg, started=T0)
    override = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    for s in range(61):
        at_the_evcs(a, s)
    assert not at_the_evcs(a, 61, override).unclear
    # zurückgenommen: Der Widerspruch zählt von neuem, nicht seit seinem ersten Auftreten
    withdrawn = Intent(version=2, control=True)
    assert not any(at_the_evcs(a, s, withdrawn).unclear for s in range(62, 122))
    assert at_the_evcs(a, 122, withdrawn).unclear
