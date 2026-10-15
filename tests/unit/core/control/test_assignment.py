from datetime import datetime, timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import (
    CONTRADICTION,
    NONE,
    UNCLEAR_FROM_S,
    evcs,
    tesla,
    twc,
)

from bindaems.core.control.assignment import (
    Assignment,
    AssignmentKind,
    VehicleState,
    WallboxState,
)
from bindaems.core.control.clues import Clue, Evidence
from bindaems.shared.control import Intent, VehicleIntent

# Vorgaben des Plans


def update(
    a, s, *, evcs_obs=None, twc_obs=None, tesla_obs=None, intent=None, accepted_at=T0, homes=None
):
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    when = T0 + timedelta(seconds=s)
    return a.update(when, when, intent, accepted_at, wallboxes, vehicles, homes or {})


def boosted(version: int, until: datetime) -> Intent:
    return Intent(
        version=version, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)}
    )


def test_default_assignment(cfg) -> None:
    # Der Wall Connector meldet ein Fahrzeug: Tessie sieht den Tesla angesteckt (Plan 2c-2b,
    # Präzisierung 13; „nicht angesteckt“ schlösse ihn dort aus)
    state = update(
        Assignment(cfg, started=T0), 0, twc_obs=twc(connected=True), tesla_obs=tesla(plugged=True)
    )
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
    """Ein echter Widerspruch (Plan 2c-2b, Präzisierung 13): unklar ab ``UNCLEAR_FROM_S``."""
    return update(a, s, **CONTRADICTION, intent=intent)


def test_flapping_contradiction_never_turns_unclear(cfg) -> None:  # Review Focus 3
    # Je UNCLEAR_FROM_S lang das Bild, dann ebenso lang keins: Der Widerspruch beginnt erst mit der
    # Phasen-Signatur nach 60 s und hält darum je 59 s.
    a = Assignment(cfg, started=T0)
    for s in range(4 * UNCLEAR_FROM_S):
        state = at_the_evcs(a, s) if (s // UNCLEAR_FROM_S) % 2 == 0 else update(a, s)
        assert not state.unclear


def test_override_lifts_unclear_at_once(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(UNCLEAR_FROM_S):
        at_the_evcs(a, s)
    assert at_the_evcs(a, UNCLEAR_FROM_S).unclear
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    assert not at_the_evcs(a, UNCLEAR_FROM_S + 1, intent).unclear


def test_a_tesla_away_is_no_contradiction(cfg) -> None:
    away = {**CONTRADICTION, "tesla_obs": tesla(plugged=True, at_home=False)}
    a = Assignment(cfg, started=T0)
    assert not any(update(a, s, **away).unclear for s in range(2 * UNCLEAR_FROM_S))


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
    # die Fahrzeuge der Konfiguration, dahinter je Wallbox ihr Gast (Plan 2c-2b, Präzisierung 5)
    assert list(state.vehicles) == ["tesla", "egolf", "guest@evcs", "guest@twc"]
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


@pytest.mark.parametrize("twc_before", [None, False], ids=["never_known", "no_vehicle"])
def test_tessie_complete_counts_during_a_wall_connector_outage(cfg, twc_before) -> None:
    a = Assignment(cfg, started=T0)
    if twc_before is not None:
        update(a, 0, twc_obs=twc(connected=twc_before))
    outage = twc(connected=None, charging=None)
    state = update(
        a, 1, twc_obs=outage, tesla_obs=tesla(plugged=True, at_home=True, complete=True)
    ).vehicles["tesla"]
    assert (state.plugged, state.complete) == (True, True)
    # ohne laufenden Vorgang wird nichts gehalten: Meldet Tessie es nicht mehr, ist es vorbei
    state = update(a, 2, twc_obs=outage, tesla_obs=tesla(plugged=True, at_home=True)).vehicles[
        "tesla"
    ]
    assert (state.plugged, state.complete) == (True, False)


@pytest.mark.parametrize(
    ("plugged", "at_home", "complete", "expected"),
    [
        (True, True, True, True),
        (True, True, False, False),
        (True, True, None, False),
        (True, False, True, False),  # woanders „geladen“: zählt nicht für diese Wallbox
        (False, True, True, False),
        (None, True, True, False),  # offen, ob hier angesteckt: zählt nicht
        (True, None, True, False),
    ],
)
def test_tessie_complete_without_a_session_counts_only_at_home_and_plugged(
    cfg, plugged, at_home, complete, expected
) -> None:
    state = update(
        Assignment(cfg, started=T0),
        0,
        twc_obs=twc(connected=None),
        tesla_obs=tesla(plugged=plugged, at_home=at_home, complete=complete),
    )
    assert state.vehicles["tesla"].complete is expected


def test_complete_reported_by_the_wallbox_counts_without_a_session(cfg) -> None:
    # So meldet es observe nicht (die EVCS meldet mit „geladen“ immer „verbunden“); die Zuordnung
    # liest den Wert der Wallbox trotzdem ohne Vorgang, wie den von Tessie.
    state = update(Assignment(cfg, started=T0), 0, evcs_obs=evcs(connected=None, complete=True))
    assert state.vehicles["egolf"].complete


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
    # Erst meldet der Wall Connector kein Fahrzeug: Ein Vorgang dort beginnt danach bekannt, und
    # Tessie zählt für ihn erst mit einer Abfrage 60 s nach seinem Beginn. Bis dahin steht der
    # Tesla dort angenommen, auch wenn Tessie ihn nicht angesteckt sieht (Plan 2c-2b,
    # Präzisierungen 3 und 13).
    a = Assignment(cfg, started=T0)
    tessie = tesla(plugged=plugged, at_home=at_home)
    update(a, 0, twc_obs=twc(connected=False), tesla_obs=tessie)
    state = update(a, 1, twc_obs=twc(connected=twc_connected), tesla_obs=tessie)
    assert state.vehicles["tesla"].plugged is expected


@pytest.mark.parametrize("connected", [True, False, None])
def test_plugged_of_a_car_without_tessie_is_the_wallbox_value(cfg, connected) -> None:
    wallboxes = {"evcs": evcs(connected=connected), "twc": twc()}
    vehicles = {"tesla": tesla(), "egolf": tesla(plugged=True, at_home=True)}  # nicht beachtet
    state = Assignment(cfg, started=T0).update(T0, T0, None, T0, wallboxes, vehicles)
    assert state.vehicles["egolf"].plugged is connected


@pytest.mark.parametrize(
    ("wallbox_charging", "plugged", "at_home", "tessie_charging", "expected"),
    [
        (False, True, True, True, False),  # der Wert der Wallbox geht Tessie vor
        (True, True, True, False, True),
        # ohne Wert der Wallbox gilt Tessie, wenn es das Auto hier sieht
        (None, True, True, True, True),
        (None, True, True, False, False),
        (None, True, True, None, None),
        (None, False, True, True, False),  # nicht angesteckt: lädt hier sicher nicht
        (None, True, False, True, False),  # nicht zu Hause: lädt hier sicher nicht
        (None, False, False, None, False),
        (None, None, True, True, None),  # sonst offen
        (None, True, None, True, None),
        (None, None, None, True, None),
    ],
)
def test_charging_is_the_wallbox_value_with_tessie_as_the_fallback(
    cfg, wallbox_charging, plugged, at_home, tessie_charging, expected
) -> None:
    outage = wallbox_charging is None  # ohne Wert der Wallbox fehlt meist auch „Fahrzeug verbunden“
    state = update(
        Assignment(cfg, started=T0),
        0,
        twc_obs=twc(connected=None if outage else True, charging=wallbox_charging),
        tesla_obs=tesla(plugged=plugged, at_home=at_home, charging=tessie_charging),
    )
    assert state.vehicles["tesla"].charging is expected


@pytest.mark.parametrize("charging", [True, False, None])
def test_charging_of_a_car_without_tessie_is_the_wallbox_value(cfg, charging) -> None:
    wallboxes = {"evcs": evcs(charging=charging), "twc": twc()}
    vehicles = {"tesla": tesla(), "egolf": tesla(plugged=True, at_home=True, charging=True)}
    state = Assignment(cfg, started=T0).update(T0, T0, None, T0, wallboxes, vehicles)
    assert state.vehicles["egolf"].charging is charging  # die Werte von Tessie zählen nicht


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
    a = Assignment(cfg, started=T0)
    assert not any(update(a, s, **(CONTRADICTION | unknown)).unclear for s in range(300))


def test_no_contradiction_without_a_car_at_the_evcs(cfg) -> None:
    no_car = {**CONTRADICTION, "evcs_obs": evcs(connected=False)}
    a = Assignment(cfg, started=T0)
    for s in range(300):
        assert not update(a, s, **no_car).unclear


def test_an_unknown_value_interrupts_the_contradiction(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(UNCLEAR_FROM_S - 1):
        at_the_evcs(a, s)
    # ohne „kein Fahrzeug“ am Wall Connector fehlt der Ausschluss: einen Zyklus kein Widerspruch
    update(a, UNCLEAR_FROM_S - 1, **{**CONTRADICTION, "twc_obs": twc(connected=None)})
    later = range(UNCLEAR_FROM_S, UNCLEAR_FROM_S + 60)
    assert not any(at_the_evcs(a, s).unclear for s in later)
    assert at_the_evcs(a, UNCLEAR_FROM_S + 60).unclear


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
    for s in range(UNCLEAR_FROM_S + 1):
        at_the_evcs(a, s)
    assert not at_the_evcs(a, UNCLEAR_FROM_S + 1, override).unclear
    # zurückgenommen: Der Widerspruch zählt von neuem, nicht seit seinem ersten Auftreten
    withdrawn = Intent(version=2, control=True)
    later = range(UNCLEAR_FROM_S + 2, UNCLEAR_FROM_S + 62)
    assert not any(at_the_evcs(a, s, withdrawn).unclear for s in later)
    assert at_the_evcs(a, UNCLEAR_FROM_S + 62, withdrawn).unclear


# Plan 2c-2b: Erkennung, wer wo steht und „unklar“ aus einem echten Widerspruch (Präzisierungen 4,
# 5, 9 und 13)

PICTURE = {"twc_obs": twc(connected=False), "tesla_obs": tesla(plugged=True)}  # „Tesla an der EVCS“


def test_tesla_at_the_evcs_is_detected(cfg) -> None:  # Design 7.3, letzter Punkt
    a = Assignment(cfg, started=T0)
    assert update(a, 0, **PICTURE).vehicles["egolf"].wallbox == "evcs"  # angenommen
    for s in range(1, 60):
        update(a, s, **PICTURE)
    state = update(a, 60, **PICTURE)
    tesla_ = state.vehicles["tesla"]
    assert (tesla_.wallbox, tesla_.kind, tesla_.fresh, tesla_.plugged) == (
        "evcs",
        AssignmentKind.DETECTED,
        True,
        True,
    )
    assert (state.vehicles["egolf"].wallbox, state.unclear) == (None, False)
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == (
        "tesla",
        AssignmentKind.DETECTED,
    )
    assert state.wallboxes["twc"] == WallboxState(None, None, ())
    assert not update(a, 61, **PICTURE).vehicles["tesla"].fresh


def test_an_excluded_default_vehicle_makes_an_assumed_guest(cfg) -> None:
    a = Assignment(cfg, started=T0)
    state = update(a, 0, homes={"egolf": False})  # e-Golf weg, Tesla nicht angesteckt
    guest = state.vehicles["guest@evcs"]
    assert (guest.wallbox, guest.kind, guest.plugged) == ("evcs", AssignmentKind.DEFAULT, True)
    assert (state.vehicles["egolf"].wallbox, state.vehicles["guest@twc"].wallbox) == (None, None)
    for s in range(1, 60):
        update(a, s, homes={"egolf": False})
    detected = update(a, 60, homes={"egolf": False}).vehicles["guest@evcs"]
    assert (detected.kind, detected.fresh) == (AssignmentKind.DETECTED, False)  # derselbe Insasse


def test_confirming_the_assumed_vehicle_is_no_new_detection(cfg) -> None:
    egolf = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0})
    a = Assignment(cfg, started=T0)
    states = [update(a, s, evcs_obs=egolf, homes={"egolf": True}) for s in range(121)]
    assert (states[119].vehicles["egolf"].kind, states[120].vehicles["egolf"].kind) == (
        AssignmentKind.DEFAULT,
        AssignmentKind.DETECTED,
    )
    assert not any(state.vehicles["egolf"].fresh for state in states)


def test_real_contradiction_makes_it_unclear(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert not any(update(a, s, **CONTRADICTION).unclear for s in range(UNCLEAR_FROM_S))
    state = update(a, UNCLEAR_FROM_S, **CONTRADICTION)
    assert state.unclear and state.vehicles["egolf"].kind is AssignmentKind.UNCLEAR
    assert (state.vehicles["egolf"].wallbox, state.vehicles["guest@evcs"].wallbox) == (
        "evcs",
        None,
    )
    assert all(update(a, s).unclear for s in range(121, 180))
    assert not update(a, 181).unclear


def test_arriving_tesla_stays_assumed_until_tessie_polls(cfg) -> None:  # Review Focus 1
    a = Assignment(cfg, started=T0)
    stale = tesla(plugged=False, at_home=False, plugged_at=T0)  # letzte Abfrage vor dem Anstecken
    update(a, 0, twc_obs=twc(connected=False), tesla_obs=stale)
    for s in range(1, 600):
        state = update(a, s, twc_obs=twc(connected=True), tesla_obs=stale)
        assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == (
            "twc",
            AssignmentKind.DEFAULT,
        )


def test_tessie_offline_assumes_the_default_vehicle(cfg) -> None:  # Review Focus 1
    a = Assignment(cfg, started=T0)
    for s in range(300):
        state = update(a, s, twc_obs=twc(connected=True), tesla_obs=NONE)
        assert not state.unclear
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == (
        "twc",
        AssignmentKind.DEFAULT,
    )


def test_a_new_session_starts_without_the_old_detection(cfg) -> None:  # Review Focus 5
    a = Assignment(cfg, started=T0)
    for s in range(61):
        update(a, s, **PICTURE)
    gone = update(a, 61, evcs_obs=evcs(connected=False), twc_obs=twc(connected=False))
    assert (gone.vehicles["tesla"].wallbox, gone.wallboxes["evcs"].occupant) == ("twc", None)
    assert update(a, 62, **PICTURE).vehicles["egolf"].wallbox == "evcs"  # angenommen


def test_override_precedes_detection_until_unplugged(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(wallbox="evcs")})
    a = Assignment(cfg, started=T0)
    for s in range(120):
        state = update(a, s, intent=intent, **PICTURE)
    assert (state.vehicles["egolf"].kind, state.vehicles["tesla"].wallbox) == (
        AssignmentKind.OVERRIDE,
        "twc",
    )
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == (
        "egolf",
        AssignmentKind.OVERRIDE,
    )


# Ergänzungen: Indizien je Wallbox, wer wo steht, „neu erkannt“, Uhren und der Gast


def with_default(cfg, key: str, wallbox: str):
    """Die Konfiguration mit ``wallbox`` als Standard-Wallbox des Fahrzeugs ``key``."""
    vehicle = cfg.vehicles[key].model_copy(update={"default_wallbox": wallbox})
    return cfg.model_copy(update={"vehicles": {**cfg.vehicles, key: vehicle}})


def test_each_wallbox_shows_the_evidence_of_its_session(cfg) -> None:
    # Tesla nicht angesteckt, e-Golf weg: Bei zwei Vorgängen schließen Tessie und Home Assistant
    # an jeder Wallbox aus, und an beiden steht ein Gast, angenommen
    both = {"twc_obs": twc(connected=True), "tesla_obs": tesla()}
    state = update(Assignment(cfg, started=T0), 0, homes={"egolf": False}, **both)
    assert list(state.wallboxes) == ["evcs", "twc"]
    for key in ("evcs", "twc"):
        # je Fahrzeug in der Reihenfolge von ``config.yaml``, je Fahrzeug die Indizien nach ``Clue``
        evidence = (
            Evidence(key, "tesla", Clue.TESSIE, False),
            Evidence(key, "egolf", Clue.HOME, False),
        )
        guest = f"guest@{key}"
        assert state.wallboxes[key] == WallboxState(guest, AssignmentKind.DEFAULT, evidence)


def test_a_default_vehicle_standing_elsewhere_leaves_an_assumed_guest(cfg) -> None:
    # Der Tesla lädt an der EVCS auf drei Phasen; am Wall Connector steht ein Auto, das nicht lädt,
    # und der e-Golf ist weg. An der EVCS ist der Tesla ab 60 s Kandidat (Strom, Phasen), bei 120 s
    # erkannt.
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0)),
        "twc_obs": twc(connected=True),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
        "homes": {"egolf": False},
    }
    a = Assignment(cfg, started=T0)
    states = [update(a, s, **kw) for s in range(121)]
    before, after = states[119], states[120]
    # vorher: an der EVCS ein Gast angenommen, der Tesla angenommen am Wall Connector
    assert (before.wallboxes["evcs"].occupant, before.wallboxes["twc"].occupant) == (
        "guest@evcs",
        "tesla",
    )
    assert (after.vehicles["tesla"].wallbox, after.vehicles["tesla"].fresh) == ("evcs", True)
    # Das Standardfahrzeug des Wall Connectors steht schon an der EVCS: dort ein Gast, angenommen
    assert after.wallboxes["twc"] == WallboxState(
        "guest@twc", AssignmentKind.DEFAULT, before.wallboxes["twc"].evidence
    )
    assert (after.vehicles["egolf"].wallbox, after.vehicles["guest@evcs"].wallbox) == (None, None)


def test_a_wallbox_without_a_default_vehicle_assumes_its_guest(cfg) -> None:
    shared = with_default(cfg, "tesla", "evcs")  # beide Fahrzeuge an der EVCS
    plugged = {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True)}
    state = update(Assignment(shared, started=T0), 0, **plugged)
    # das Standardfahrzeug der EVCS ist das erste der Konfiguration mit dieser Wallbox
    assert (state.wallboxes["evcs"].occupant, state.vehicles["egolf"].wallbox) == ("tesla", None)
    assert (state.wallboxes["twc"].occupant, state.wallboxes["twc"].kind) == (
        "guest@twc",
        AssignmentKind.DEFAULT,
    )


def test_while_unclear_a_wallbox_nobody_stands_at_has_no_occupant(cfg) -> None:
    # Beide Fahrzeuge haben die EVCS als Standard. Beide Wallboxen laden auf drei Phasen mit dem
    # Strom des Tesla: Strom und Phasen stützen ihn ab 60 s an beiden, ein Widerspruch; unklar wie
    # bei CONTRADICTION.
    shared = with_default(cfg, "tesla", "evcs")
    three = dict.fromkeys(("L1", "L2", "L3"), 16.0)
    both = {
        "evcs_obs": evcs(charging=True, phase_a=three),
        "twc_obs": twc(connected=True, charging=True, phase_a=three),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
    }
    a = Assignment(shared, started=T0)
    assert not any(update(a, s, **both).unclear for s in range(UNCLEAR_FROM_S))
    state = update(a, UNCLEAR_FROM_S, **both)
    assert state.unclear
    assert [(v.wallbox, v.kind) for v in state.vehicles.values()] == [
        ("evcs", AssignmentKind.UNCLEAR),
        ("evcs", AssignmentKind.UNCLEAR),
        (None, AssignmentKind.DEFAULT),  # kein Gast, solange es unklar ist
        (None, AssignmentKind.DEFAULT),
    ]
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == (
        "tesla",
        AssignmentKind.UNCLEAR,
    )
    nobody = state.wallboxes["twc"]
    assert (nobody.occupant, nobody.kind) == (None, AssignmentKind.UNCLEAR)
    assert Evidence("twc", "tesla", Clue.PHASES, True) in nobody.evidence


def test_detected_again_after_unclear_is_a_new_detection(cfg) -> None:  # Review Focus 4
    a = Assignment(cfg, started=T0)
    for s in range(60):
        update(a, s, **PICTURE)
    assert update(a, 60, **PICTURE).vehicles["tesla"].fresh
    # Die EVCS lädt auf zwei Phasen: der Widerspruch ab 121, unklar ab 181
    for s in range(61, 181):
        update(a, s, **CONTRADICTION)
    unclear = update(a, 181, **CONTRADICTION)
    assert (unclear.unclear, unclear.wallboxes["evcs"].occupant) == (True, "egolf")
    assert (unclear.vehicles["tesla"].wallbox, unclear.vehicles["tesla"].plugged) == ("twc", None)
    # Ohne die Phasen ist der Tesla gleich wieder Kandidat: 60 s später klar und erkannt, neu
    states = [update(a, s, **PICTURE) for s in range(182, 243)]
    assert [state.unclear for state in states] == [True] * 60 + [False]
    assert [state.vehicles["tesla"].fresh for state in states] == [False] * 60 + [True]


def test_a_running_session_keeps_the_detection_while_its_plug_state_is_unknown(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(60):
        update(a, s, evcs_obs=evcs(connected=None) if s == 30 else evcs(), **PICTURE)
    assert update(a, 60, **PICTURE).vehicles["tesla"].kind is AssignmentKind.DETECTED


def test_the_start_of_a_session_is_on_the_wall_clock(cfg) -> None:  # Präzisierungen 9 und 10
    # Die Wanduhr geht einen Tag nach. Tessies Zeitstempel und der Beginn des Vorgangs zählen auf
    # ihr: Der Tesla kommt bei 1 s an den Wall Connector, Tessie fragt bei 61 s „angesteckt“ ab.
    # Tessie, zeitliche Korrelation und Ausschluss (die EVCS ist leer) stützen ihn dann, erkannt
    # ist er nach 60 s auf der Uhr der Safety-Schicht.
    wall = T0 - timedelta(days=1)
    a = Assignment(cfg, started=T0)
    kinds = []
    for s in range(122):
        polled = 61 if s >= 61 else 0
        tessie = tesla(plugged=s >= 61, plugged_at=wall + timedelta(seconds=polled))
        wallboxes = {"evcs": evcs(connected=False), "twc": twc(connected=s > 0)}
        when = timedelta(seconds=s)
        state = a.update(
            T0 + when, wall + when, None, T0, wallboxes, {"tesla": tessie, "egolf": NONE}
        )
        kinds.append((state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind))
    assert kinds == [("twc", AssignmentKind.DEFAULT)] * 121 + [("twc", AssignmentKind.DETECTED)]


def test_an_assumed_guest_has_the_values_of_its_wallbox_but_no_boost(cfg) -> None:
    a = Assignment(cfg, started=T0)
    away = {"homes": {"egolf": False}, "intent": boosted(1, T0 + timedelta(hours=2))}
    update(a, 0, evcs_obs=evcs(connected=False), **away)
    # Der e-Golf ist weg; sein Boost gilt an der EVCS, aber dort steht ein Gast
    guest = update(a, 1, evcs_obs=evcs(charging=True), **away).vehicles["guest@evcs"]
    assert guest == VehicleState("evcs", AssignmentKind.DEFAULT, True, True, False, True, None)
    update(a, 2, evcs_obs=evcs(complete=True), **away)
    state = update(a, 3, **away)
    assert state.vehicles["guest@evcs"].complete  # bis zum Abstecken
    nowhere = VehicleState(None, AssignmentKind.DEFAULT, None, None, False, False, None)
    assert (state.vehicles["egolf"], state.vehicles["guest@twc"]) == (nowhere, nowhere)


def test_a_guest_never_takes_the_values_of_tessie(cfg) -> None:
    # Der Tesla steht am Wall Connector und ist laut Tessie geladen; an der EVCS steht ein Gast
    full = tesla(plugged=True, charging=True, complete=True)
    away = {"homes": {"egolf": False}}
    state = update(
        Assignment(cfg, started=T0), 0, twc_obs=twc(connected=True), tesla_obs=full, **away
    )
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].complete) == ("twc", True)
    guest = state.vehicles["guest@evcs"]
    assert (guest.wallbox, guest.charging, guest.complete) == ("evcs", False, False)


def test_after_an_override_a_new_session_starts_assumed(cfg) -> None:  # Review Focus 5
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(wallbox="evcs")})
    a = Assignment(cfg, started=T0)
    for s in range(120):
        update(a, s, intent=intent, **PICTURE)
    update(a, 120, evcs_obs=evcs(connected=False), intent=intent, **PICTURE)
    # Die Absicht nennt die Übersteuerung noch, aber ihr Vorgang ist um
    state = update(a, 121, intent=intent, **PICTURE)
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == (
        "egolf",
        AssignmentKind.DEFAULT,
    )


def test_an_overridden_vehicle_is_never_detected_elsewhere(cfg) -> None:  # Design 7.1
    # Der Tesla ist an die EVCS übersteuert. Am Wall Connector lädt ein Auto auf drei Phasen mit
    # dem Strom, den Tessie für den Tesla meldet: Strom und Phasen stützen ihn dort ab 60 s, und er
    # ist dort trotzdem ausgeschlossen.
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    three = twc(connected=True, charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0))
    car = tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3)
    a = Assignment(cfg, started=T0)
    states = [update(a, s, intent=intent, twc_obs=three, tesla_obs=car) for s in range(121)]
    placed = {(state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) for state in states}
    assert placed == {("evcs", AssignmentKind.OVERRIDE)}
    # Am Wall Connector ist jedes Fahrzeug ausgeschlossen, der e-Golf ab 60 s nach den Phasen: ein
    # Gast, erkannt bei 120 s
    assert [(s.wallboxes["twc"].occupant, s.wallboxes["twc"].kind) for s in states[119:]] == [
        ("guest@twc", AssignmentKind.DEFAULT),
        ("guest@twc", AssignmentKind.DETECTED),
    ]


# Bindung des Boost an den Vorgang, an dem das Fahrzeug steht


def test_a_boost_binds_to_the_session_where_the_vehicle_stands(cfg) -> None:
    # Der Tesla ist an der EVCS erkannt: Ein Boost gilt dort und endet mit dem Abstecken, statt
    # beim nächsten Anstecken am Wall Connector anzuspringen.
    a = Assignment(cfg, started=T0)
    for s in range(61):
        update(a, s, **PICTURE)
    until = T0 + timedelta(hours=2)
    boost = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(boost_until=until)})
    there = update(a, 61, intent=boost, **PICTURE).vehicles["tesla"]
    assert (there.wallbox, there.kind, there.boost_until) == (
        "evcs",
        AssignmentKind.DETECTED,
        until,
    )
    gone = update(a, 62, evcs_obs=evcs(connected=False), intent=boost, **PICTURE)
    assert gone.vehicles["tesla"].boost_until is None
    # später am Wall Connector angesteckt, Tessie: zu Hause und angesteckt
    home = {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True, at_home=True)}
    later = update(a, 63, evcs_obs=evcs(connected=False), intent=boost, **home).vehicles["tesla"]
    assert (later.wallbox, later.boost_until) == ("twc", None)


def test_a_boost_without_a_session_waits_for_the_default_wallbox_as_before(cfg) -> None:
    # Der e-Golf steht an keiner Wallbox mit Vorgang: Sein Boost bindet wie bisher an den nächsten
    # Vorgang seiner Standard-Wallbox.
    a = Assignment(cfg, started=T0)
    unplugged = {"evcs_obs": evcs(connected=False)}
    update(a, 0, **unplugged)
    intent = boosted(1, T0 + timedelta(hours=2))
    assert update(a, 1, intent=intent, **unplugged).vehicles["egolf"].boost_until is None
    assert update(a, 2, intent=intent).vehicles["egolf"].boost_until == T0 + timedelta(hours=2)


def test_an_override_still_binds_to_its_own_wallbox(cfg) -> None:
    # Der Tesla ist an der EVCS erkannt und wird an den Wall Connector übersteuert, an dem noch kein
    # Fahrzeug steht: Die Übersteuerung wartet auf den nächsten Vorgang dort.
    a = Assignment(cfg, started=T0)
    for s in range(61):
        update(a, s, **PICTURE)
    to_twc = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="twc")})
    waiting = update(a, 61, intent=to_twc, **PICTURE).vehicles["tesla"]
    assert (waiting.wallbox, waiting.kind) == ("evcs", AssignmentKind.DETECTED)
    update(a, 62, evcs_obs=evcs(connected=False), intent=to_twc, **PICTURE)
    plugged = {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True)}
    moved = update(a, 63, evcs_obs=evcs(connected=False), intent=to_twc, **plugged)
    assert (moved.vehicles["tesla"].wallbox, moved.vehicles["tesla"].kind) == (
        "twc",
        AssignmentKind.OVERRIDE,
    )


def test_a_boost_set_as_the_session_ends_binds_as_before(cfg) -> None:
    # Der Tesla war an der EVCS erkannt; der Boost kommt im Aufruf, in dem die EVCS kein Fahrzeug
    # mehr meldet. Dieser Vorgang läuft nicht mehr: Der Boost bindet wie bisher an die
    # Standard-Wallbox, an den nächsten Vorgang am Wall Connector.
    a = Assignment(cfg, started=T0)
    for s in range(61):
        update(a, s, **PICTURE)
    until = T0 + timedelta(hours=2)
    boost = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(boost_until=until)})
    update(a, 61, evcs_obs=evcs(connected=False), intent=boost, **PICTURE)
    home = {"twc_obs": twc(connected=True), "tesla_obs": tesla(plugged=True, at_home=True)}
    later = update(a, 62, evcs_obs=evcs(connected=False), intent=boost, **home).vehicles["tesla"]
    assert (later.wallbox, later.boost_until) == ("twc", until)


def test_a_boost_set_while_plugging_in_binds_to_the_new_session(cfg) -> None:
    # Der e-Golf stand an der EVCS ohne Vorgang; der Boost kommt im Aufruf, in dem er ansteckt
    a = Assignment(cfg, started=T0)
    update(a, 0, evcs_obs=evcs(connected=False))
    egolf = update(a, 1, intent=boosted(1, T0 + timedelta(hours=2))).vehicles["egolf"]
    assert (egolf.wallbox, egolf.new_session, egolf.boost_until) == (
        "evcs",
        True,
        T0 + timedelta(hours=2),
    )


# Zwei Vorgänge: Tessie und Home Assistant sagen nicht, an welcher Wallbox ein Fahrzeug steht. Sie
# stützen erst, wenn Strom oder Phasen es an der anderen Wallbox ausschließen (Elimination).


def test_a_three_phase_guest_is_not_the_tesla_plugged_in_elsewhere(cfg) -> None:
    # An der EVCS lädt ein Gast auf drei Phasen. Der Tesla steckt zu Hause am Wall Connector und
    # lädt nicht, der e-Golf ist weg. Stützte Tessie den Tesla an jeder Wallbox mit Vorgang, wäre
    # er mit der Phasen-Signatur an der EVCS erkannt und am Wall Connector ein Gast angenommen.
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0)),
        "twc_obs": twc(connected=True),
        "tesla_obs": tesla(plugged=True),
        "homes": {"egolf": False},
    }
    a = Assignment(cfg, started=T0)
    for s in range(181):
        state = update(a, s, **kw)
        assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == (
            "twc",
            AssignmentKind.DEFAULT,
        )
        assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == (
            "guest@evcs",
            AssignmentKind.DEFAULT,
        )
        assert not state.unclear


def test_a_two_phase_guest_is_not_the_egolf_charged_elsewhere(cfg) -> None:
    # Der e-Golf steckt „geladen“ an der EVCS und ist laut Home Assistant zu Hause; am Wall
    # Connector lädt ein Gast auf zwei Phasen. Stützte Home Assistant den e-Golf an jeder Wallbox
    # mit Vorgang, wäre er mit der Phasen-Signatur am Wall Connector erkannt.
    kw = {
        "evcs_obs": evcs(complete=True),
        "twc_obs": twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}),
        "homes": {"egolf": True},
    }
    a = Assignment(cfg, started=T0)
    for s in range(181):
        state = update(a, s, **kw)
        assert (state.vehicles["egolf"].wallbox, state.vehicles["egolf"].kind) == (
            "evcs",
            AssignmentKind.DEFAULT,
        )
        assert (state.wallboxes["twc"].occupant, state.wallboxes["twc"].kind) == (
            "guest@twc",
            AssignmentKind.DEFAULT,
        )


def test_egolf_at_the_wall_connector_and_tesla_at_the_evcs_are_both_detected(cfg) -> None:
    # Der Kernfall: Der e-Golf lädt am Wall Connector auf zwei Phasen, der Tesla an der EVCS auf
    # drei mit dem Strom, den Tessie meldet. Ab 60 s schließen die Phasen jeden an der Wallbox des
    # anderen aus: Home Assistant stützt den e-Golf am Wall Connector, Tessie den Tesla an der EVCS.
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0)),
        "twc_obs": twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
        "homes": {"egolf": True},
    }
    a = Assignment(cfg, started=T0)
    states = [update(a, s, **kw) for s in range(121)]
    assert not any(state.unclear for state in states)
    early = [w.kind for state in states[:120] for w in state.wallboxes.values()]
    assert AssignmentKind.DETECTED not in early
    last = states[120]
    assert [(key, w.occupant, w.kind) for key, w in last.wallboxes.items()] == [
        ("evcs", "tesla", AssignmentKind.DETECTED),
        ("twc", "egolf", AssignmentKind.DETECTED),
    ]


def test_a_second_session_suspends_a_detection_resting_on_home_assistant(cfg) -> None:
    # Der e-Golf lädt allein am Wall Connector und ist dort bei 120 s erkannt (Home Assistant und
    # Phasen). Bei 200 s steckt an der EVCS ein dreiphasiger Gast an und lädt. Home Assistant stützt
    # den e-Golf erst wieder, wenn die EVCS ihn nach 60 s Laden ausschließt; nach weiteren 60 s
    # Kandidatur ist er wieder erkannt. Bis dahin ist er angenommen.
    egolf = twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0})
    guest = evcs(charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0))
    a = Assignment(cfg, started=T0)
    states = [
        update(
            a,
            s,
            evcs_obs=guest if s >= 200 else evcs(connected=False),
            twc_obs=egolf,
            # Tessie fragt den abgesteckten Tesla in jedem Zyklus ab: Ab 260 s zählt der Wert auch
            # für den Vorgang an der EVCS und schließt den Tesla dort aus
            tesla_obs=tesla(plugged_at=T0 + timedelta(seconds=s)),
            homes={"egolf": True},
        )
        for s in range(321)
    ]
    assert not any(state.unclear for state in states)
    egolf_at = [(state.vehicles["egolf"].wallbox, state.vehicles["egolf"].kind) for state in states]
    detected = ("twc", AssignmentKind.DETECTED)
    # der Rückfall: von 200 s bis 319 s, also 2 min
    assert [s for s, place in enumerate(egolf_at) if place == detected] == [*range(120, 200), 320]
    assert {kind for _, kind in egolf_at[200:320]} == {AssignmentKind.DEFAULT}
    # an der EVCS ist dann jedes Fahrzeug ausgeschlossen: ein erkannter Gast
    evcs_ = states[320].wallboxes["evcs"]
    assert (evcs_.occupant, evcs_.kind) == ("guest@evcs", AssignmentKind.DETECTED)
