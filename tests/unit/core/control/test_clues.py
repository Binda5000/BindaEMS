from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.control.charge_helpers import NONE, evcs, tesla, twc

from bindaems.core.control.clues import (
    HOLD_S,
    MATCH_A,
    PHASE_ON_A,
    STARTS_KEEP_S,
    TESSIE_SETTLE_S,
    Clue,
    ClueTracker,
    Evidence,
    home_and_plugged,
)
from bindaems.core.state.derived import PHASES

# Vorgaben des Plans

AT_EVCS = {"evcs": None}  # an der EVCS läuft ein Vorgang, sein Beginn ist unbekannt
BOTH = {"evcs": None, "twc": None}  # an beiden Wallboxen läuft ein Vorgang
TWO = {"L1": 10.0, "L2": 10.0, "L3": 0.0}


def clues(tracker, s, *, sessions=AT_EVCS, evcs_obs=None, twc_obs=None, tesla_obs=None, homes=None):
    when = T0 + timedelta(seconds=s)
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    return set(tracker.update(when, when, sessions, wallboxes, vehicles, homes or {}))


def of(found, clue):
    return {e for e in found if e.clue is clue}


def ev(wallbox, vehicle, clue, supports=True):
    return Evidence(wallbox, vehicle, clue, supports)


def steady(tracker, s, **kw):
    """Die Wallboxen messen schon die 60 s vor ``s`` dieselben Ströme, ohne Vorgang und Tessie.

    Ihre letzte Änderung liegt so 60 s vor ``s``: „Passt nicht“ zählt mit einem Wert, den Tessie
    bei ``s`` abfragt (Gesamtreview I-2). ``kw`` sind ``evcs_obs`` und ``twc_obs`` wie bei
    ``clues``.
    """
    for before in range(s - 60, s):
        clues(tracker, before, sessions={}, tesla_obs=NONE, **kw)


def test_tessie_supports_or_excludes(cfg) -> None:
    # bei zwei Vorgängen stützt Tessie nicht (unten, Abweichung von Design 7.2)
    found = clues(ClueTracker(cfg), 0, tesla_obs=tesla(plugged=True))
    assert of(found, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE)}
    away = clues(ClueTracker(cfg), 0, tesla_obs=tesla(plugged=True, at_home=False))
    assert of(away, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}
    assert of(clues(ClueTracker(cfg), 0), Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}


def test_home_assistant_supports_or_excludes(cfg) -> None:
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": True}), Clue.HOME) == {
        ev("evcs", "egolf", Clue.HOME)
    }
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": False}), Clue.HOME) == {
        ev("evcs", "egolf", Clue.HOME, False)
    }
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": None}), Clue.HOME) == set()


def test_the_other_wallbox_without_a_car_supports_a_plugged_tesla(cfg) -> None:
    plugged = tesla(plugged=True)
    assert of(clues(ClueTracker(cfg), 0, tesla_obs=plugged), Clue.ELSEWHERE) == {
        ev("evcs", "tesla", Clue.ELSEWHERE)
    }
    unknown = clues(ClueTracker(cfg), 0, twc_obs=twc(connected=None), tesla_obs=plugged)
    assert of(unknown, Clue.ELSEWHERE) == set()


def test_phase_signature_counts_after_60_s(cfg) -> None:
    tracker, two = ClueTracker(cfg), evcs(charging=True, phase_a=TWO)
    assert all(of(clues(tracker, s, evcs_obs=two), Clue.PHASES) == set() for s in range(60))
    assert of(clues(tracker, 60, evcs_obs=two), Clue.PHASES) == {
        ev("evcs", "tesla", Clue.PHASES, False),
        ev("evcs", "egolf", Clue.PHASES),
    }


def test_a_new_phase_count_starts_the_60_s_again(cfg) -> None:
    tracker = ClueTracker(cfg)
    for s in range(30):
        clues(tracker, s, evcs_obs=evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)))
    two = evcs(charging=True, phase_a=TWO)
    assert all(of(clues(tracker, s, evcs_obs=two), Clue.PHASES) == set() for s in range(30, 90))
    assert ev("evcs", "egolf", Clue.PHASES) in clues(tracker, 90, evcs_obs=two)


def test_current_comparison_needs_60_s_without_interruption(cfg) -> None:  # Review Focus 2
    tracker = ClueTracker(cfg)
    at_16 = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0))
    car = tesla(plugged=True, charging=True, actual_current_a=15.2, phases=3)
    for s in range(59):
        clues(tracker, s, evcs_obs=at_16, tesla_obs=car)
    clues(tracker, 59, evcs_obs=at_16, tesla_obs=replace(car, actual_current_a=0.5))  # unter 1 A
    assert all(
        of(clues(tracker, s, evcs_obs=at_16, tesla_obs=car), Clue.CURRENT) == set()
        for s in range(60, 120)
    )
    assert of(clues(tracker, 120, evcs_obs=at_16, tesla_obs=car), Clue.CURRENT) == {
        ev("evcs", "tesla", Clue.CURRENT)
    }


def test_currents_that_do_not_match_exclude_after_60_s(cfg) -> None:
    tracker, two = ClueTracker(cfg), evcs(charging=True, phase_a=TWO)
    # drei Phasen, die EVCS misst zwei; ihre Ströme sind seit 60 s gleich, Tessie fragt bei 0 s ab
    steady(tracker, 0, evcs_obs=two)
    car = tesla(plugged=True, charging=True, actual_current_a=10.0, phases=3, plugged_at=T0)
    for s in range(60):
        clues(tracker, s, evcs_obs=two, tesla_obs=car)
    assert ev("evcs", "tesla", Clue.CURRENT, False) in clues(
        tracker, 60, evcs_obs=two, tesla_obs=car
    )


def test_tessie_values_from_before_the_session_do_not_count(cfg) -> None:  # Review Focus 1
    start = T0 + timedelta(seconds=100)
    # 30 s nach dem Beginn abgefragt
    early = tesla(plugged=False, plugged_at=T0 + timedelta(seconds=130))
    assert clues(ClueTracker(cfg), 200, sessions={"evcs": start}, tesla_obs=early) == set()
    settled = replace(early, plugged_at=T0 + timedelta(seconds=160))  # 60 s danach
    found = clues(ClueTracker(cfg), 200, sessions={"evcs": start}, tesla_obs=settled)
    assert of(found, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}


def test_timing_supports_the_session_between_two_tessie_values(cfg) -> None:
    tracker = ClueTracker(cfg)
    clues(tracker, 0, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=T0))
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))
    found = clues(tracker, 600, sessions={"evcs": T0 + timedelta(seconds=100)}, tesla_obs=plugged)
    assert ev("evcs", "tesla", Clue.TIMING) in found


def test_timing_needs_the_only_session_in_the_window(cfg) -> None:
    tracker = ClueTracker(cfg)
    clues(tracker, 0, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=T0))
    both = {"evcs": T0 + timedelta(seconds=100), "twc": T0 + timedelta(seconds=200)}
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))
    found = clues(tracker, 600, sessions=both, twc_obs=twc(connected=True), tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


def test_timing_ignores_a_window_that_runs_backwards(cfg) -> None:  # Review Focus 3
    tracker = ClueTracker(cfg)
    clues(
        tracker,
        0,
        sessions={},
        evcs_obs=evcs(connected=False),
        tesla_obs=tesla(plugged_at=T0 + timedelta(seconds=900)),
    )
    # die Wanduhr sprang zurück
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))
    found = clues(tracker, 600, sessions={"evcs": T0 + timedelta(seconds=100)}, tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


def test_unknown_values_are_no_evidence(cfg) -> None:  # Review Focus 1
    unknown = evcs(charging=None, phase_a=None)
    assert (
        clues(ClueTracker(cfg), 0, evcs_obs=unknown, twc_obs=twc(connected=None), tesla_obs=NONE)
        == set()
    )


def test_no_evidence_without_a_session(cfg) -> None:
    assert (
        clues(
            ClueTracker(cfg), 0, sessions={}, tesla_obs=tesla(plugged=True), homes={"egolf": True}
        )
        == set()
    )


def test_evidence_comes_in_a_fixed_order(cfg) -> None:
    # Tesla nicht angesteckt, e-Golf weg: Bei zwei Vorgängen schließen Tessie und Home Assistant
    # an jeder Wallbox aus
    wallboxes = {"evcs": evcs(), "twc": twc(connected=True)}
    vehicles = {"tesla": tesla(), "egolf": NONE}
    found = ClueTracker(cfg).update(T0, T0, BOTH, wallboxes, vehicles, {"egolf": False})
    assert [(e.wallbox, e.vehicle, e.clue) for e in found] == [
        ("evcs", "tesla", Clue.TESSIE),
        ("evcs", "egolf", Clue.HOME),
        ("twc", "tesla", Clue.TESSIE),
        ("twc", "egolf", Clue.HOME),
    ]


# Ergänzungen: Konstanten, Grenzen der Werte, Zustand und Uhren

AT_16 = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0))  # dreiphasig mit 16 A
# die Uhr der Safety-Schicht läuft auf einer anderen Basis als die Wanduhr
APART = timedelta(days=10)


def at(s):
    return T0 + timedelta(seconds=s)


def charging_tesla(amps, phases=3, **kw):
    return tesla(plugged=True, charging=True, actual_current_a=amps, phases=phases, **kw)


def unplugged(tracker, s=0):
    """Tessie meldet das Auto abgesteckt, und an keiner Wallbox läuft ein Vorgang."""
    clues(
        tracker, s, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=at(s))
    )


def apart(tracker, s, *, sessions=AT_EVCS, evcs_obs=None, twc_obs=None, tesla_obs=None):
    """Wie ``clues``, aber die Uhr der Safety-Schicht läuft ``APART`` vor der Wanduhr."""
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    return set(tracker.update(at(s) + APART, at(s), sessions, wallboxes, vehicles, {}))


def test_the_constants_are_the_agreed_ones() -> None:
    assert (HOLD_S, MATCH_A, PHASE_ON_A, TESSIE_SETTLE_S, STARTS_KEEP_S) == (
        60.0,
        1.0,
        1.0,
        60.0,
        86_400.0,
    )


def test_the_clues_are_named_and_ordered_like_the_design() -> None:
    names = ["tessie", "current", "timing", "phases", "home", "elsewhere"]
    assert [clue.value for clue in Clue] == names


@pytest.mark.parametrize(
    ("plugged", "at_home", "expected"),
    [
        (True, True, True),
        (False, True, False),
        (True, False, False),
        (False, False, False),
        (None, False, False),  # ein Auto, das woanders steht, hängt nicht an unserer Wallbox
        (False, None, False),
        (None, True, None),
        (True, None, None),
        (None, None, None),
    ],
)
def test_home_and_plugged_is_false_as_soon_as_one_value_says_so(plugged, at_home, expected) -> None:
    assert home_and_plugged(tesla(plugged=plugged, at_home=at_home)) is expected


def test_tessie_values_without_a_timestamp_do_not_count_at_a_known_start(cfg) -> None:
    car = tesla(plugged=True)  # plugged_at fehlt
    found = clues(ClueTracker(cfg), 200, sessions={"evcs": at(100)}, tesla_obs=car)
    assert found == set()


@pytest.mark.parametrize(
    ("polled_s", "expected"),
    [
        pytest.param(130, {Clue.TIMING, Clue.PHASES}, id="30 s nach dem Beginn"),
        pytest.param(
            160,
            {Clue.TESSIE, Clue.CURRENT, Clue.TIMING, Clue.PHASES, Clue.ELSEWHERE},
            id="60 s nach dem Beginn",
        ),
    ],
)
def test_tessie_values_of_the_first_minute_count_for_the_timing_only(
    cfg, polled_s, expected
) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    kw = {
        "sessions": {"evcs": at(100)},
        "evcs_obs": AT_16,
        "tesla_obs": charging_tesla(16.0, plugged_at=at(polled_s)),
    }
    for s in range(130, 200):
        clues(tracker, s, **kw)
    found = {e for e in clues(tracker, 200, **kw) if e.vehicle == "tesla"}
    assert found == {ev("evcs", "tesla", clue) for clue in expected}


@pytest.mark.parametrize(
    ("wallbox", "car", "expected", "phases"),
    [
        pytest.param(
            AT_16, charging_tesla(15.0), {ev("evcs", "tesla", Clue.CURRENT)}, 3, id="1 A Abstand"
        ),
        pytest.param(
            AT_16,
            charging_tesla(14.5),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            3,
            id="1,5 A Abstand",
        ),
        pytest.param(
            evcs(charging=True, phase_a=TWO),
            charging_tesla(10.0),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            3,
            id="Phasenzahl passt nicht",
        ),
        pytest.param(
            AT_16,
            charging_tesla(16.0, phases=2),
            set(),
            3,
            id="Tessie meldet eine andere Phasenzahl",
        ),
        pytest.param(
            evcs(charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.9}),
            charging_tesla(16.0, phases=2),
            {ev("evcs", "tesla", Clue.CURRENT)},
            2,
            id="0,9 A ist keine Phase",
        ),
        pytest.param(
            evcs(charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 1.0}),
            charging_tesla(16.0, phases=2),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            2,
            id="1 A ist eine Phase",
        ),
        pytest.param(
            evcs(charging=True, phase_a=dict.fromkeys(PHASES, 1.0)),
            charging_tesla(1.0),
            {ev("evcs", "tesla", Clue.CURRENT)},
            3,
            id="1 A lädt das Auto",
        ),
        pytest.param(AT_16, charging_tesla(0.9), set(), 3, id="0,9 A lädt das Auto nicht"),
        pytest.param(
            AT_16, replace(charging_tesla(16.0), charging=False), set(), 3, id="Tessie lädt nicht"
        ),
        pytest.param(
            AT_16, replace(charging_tesla(16.0), phases=None), set(), 3, id="Phasenzahl unbekannt"
        ),
        pytest.param(
            AT_16,
            replace(charging_tesla(16.0), actual_current_a=None),
            set(),
            3,
            id="Strom unbekannt",
        ),
        pytest.param(
            evcs(charging=False, phase_a=dict.fromkeys(PHASES, 16.0)),
            charging_tesla(16.0),
            set(),
            3,
            id="Wallbox lädt nicht",
        ),
        pytest.param(
            evcs(charging=True, phase_a=None),
            charging_tesla(16.0),
            set(),
            3,
            id="Ströme der Wallbox unbekannt",
        ),
    ],
)
def test_current_comparison_at_the_limits(cfg, wallbox, car, expected, phases) -> None:
    # ``phases`` hat der Tesla laut Konfiguration; die Ströme der EVCS sind seit 60 s gleich, und
    # Tessie fragt bei 0 s ab (Gesamtreview I-2)
    tesla_ = cfg.vehicles["tesla"].model_copy(update={"phases": phases})
    tracker = ClueTracker(cfg.model_copy(update={"vehicles": {**cfg.vehicles, "tesla": tesla_}}))
    steady(tracker, 0, evcs_obs=wallbox)
    car = replace(car, plugged_at=T0)
    for s in range(60):
        clues(tracker, s, evcs_obs=wallbox, tesla_obs=car)
    assert of(clues(tracker, 60, evcs_obs=wallbox, tesla_obs=car), Clue.CURRENT) == expected


def test_current_comparison_starts_again_when_the_result_changes(cfg) -> None:
    tracker = ClueTracker(cfg)
    steady(tracker, 0, evcs_obs=AT_16)  # die Ströme der EVCS sind seit 60 s gleich
    for s in range(30):
        clues(tracker, s, evcs_obs=AT_16, tesla_obs=charging_tesla(16.0, plugged_at=T0))  # passt
    off = charging_tesla(10.0, plugged_at=at(30))  # 6 A Abweichung
    assert all(
        of(clues(tracker, s, evcs_obs=AT_16, tesla_obs=off), Clue.CURRENT) == set()
        for s in range(30, 90)
    )
    assert of(clues(tracker, 90, evcs_obs=AT_16, tesla_obs=off), Clue.CURRENT) == {
        ev("evcs", "tesla", Clue.CURRENT, False)
    }


@pytest.mark.parametrize(
    ("at_home", "amps", "expected"),
    [
        pytest.param(True, 16.0, {ev("evcs", "tesla", Clue.CURRENT)}, id="zu Hause, passt"),
        pytest.param(None, 16.0, {ev("evcs", "tesla", Clue.CURRENT)}, id="Ort unbekannt, passt"),
        pytest.param(
            True, 10.0, {ev("evcs", "tesla", Clue.CURRENT, False)}, id="zu Hause, passt nicht"
        ),
        pytest.param(False, 16.0, set(), id="woanders, passt"),
        pytest.param(False, 10.0, set(), id="woanders, passt nicht"),
    ],
)
def test_current_comparison_says_nothing_about_a_tesla_that_is_away(
    cfg, at_home, amps, expected
) -> None:
    # ein Tesla an einer öffentlichen Säule lädt mit 16 A auf drei Phasen wie der Gast bei uns
    tracker, car = ClueTracker(cfg), charging_tesla(amps, at_home=at_home, plugged_at=T0)
    steady(tracker, 0, evcs_obs=AT_16)  # die Ströme der EVCS sind seit 60 s gleich
    seen: set[Evidence] = set()
    for s in range(121):
        seen |= of(clues(tracker, s, evcs_obs=AT_16, tesla_obs=car), Clue.CURRENT)
    assert seen == expected


def test_current_comparison_starts_again_when_the_tesla_comes_home(cfg) -> None:
    tracker = ClueTracker(cfg)
    for s in range(100):  # woanders gilt der Vergleich nicht, auch nach 60 s nicht
        clues(tracker, s, evcs_obs=AT_16, tesla_obs=charging_tesla(16.0, at_home=False))
    home = charging_tesla(16.0)
    assert all(
        of(clues(tracker, s, evcs_obs=AT_16, tesla_obs=home), Clue.CURRENT) == set()
        for s in range(100, 160)
    )
    assert of(clues(tracker, 160, evcs_obs=AT_16, tesla_obs=home), Clue.CURRENT) == {
        ev("evcs", "tesla", Clue.CURRENT)
    }


@pytest.mark.parametrize(
    "wallbox",
    [
        pytest.param(evcs(charging=False, phase_a=TWO), id="lädt nicht"),
        pytest.param(evcs(charging=None, phase_a=TWO), id="Laden unbekannt"),
        pytest.param(evcs(charging=True, phase_a=None), id="Ströme unbekannt"),
        pytest.param(evcs(charging=True, phase_a=dict.fromkeys(PHASES, 0.9)), id="unter 1 A"),
    ],
)
def test_no_phase_signature_without_a_charging_wallbox_that_measures_1_a(cfg, wallbox) -> None:
    tracker = ClueTracker(cfg)
    assert all(of(clues(tracker, s, evcs_obs=wallbox), Clue.PHASES) == set() for s in range(120))


def test_a_phase_counts_from_1_a(cfg) -> None:
    tracker = ClueTracker(cfg)
    wallbox = evcs(charging=True, phase_a={"L1": 1.0, "L2": 1.0, "L3": 0.9})
    for s in range(60):
        clues(tracker, s, evcs_obs=wallbox)
    assert of(clues(tracker, 60, evcs_obs=wallbox), Clue.PHASES) == {
        ev("evcs", "tesla", Clue.PHASES, False),
        ev("evcs", "egolf", Clue.PHASES),
    }


def test_phase_signature_starts_again_after_an_interruption(cfg) -> None:
    tracker, two = ClueTracker(cfg), evcs(charging=True, phase_a=TWO)
    for s in range(59):
        clues(tracker, s, evcs_obs=two)
    clues(tracker, 59, evcs_obs=evcs(charging=False, phase_a=TWO))  # einen Zyklus lädt sie nicht
    assert all(of(clues(tracker, s, evcs_obs=two), Clue.PHASES) == set() for s in range(60, 120))
    assert ev("evcs", "egolf", Clue.PHASES) in clues(tracker, 120, evcs_obs=two)


def test_the_holds_end_with_the_session(cfg) -> None:
    tracker, car = ClueTracker(cfg), charging_tesla(16.0)
    for s in range(60):
        clues(tracker, s, evcs_obs=AT_16, tesla_obs=car)
    clues(tracker, 60, sessions={}, evcs_obs=AT_16, tesla_obs=car)  # der Vorgang endet ...
    for s in range(61, 121):  # ... und ein neuer beginnt: die 60 s von vorn
        found = clues(tracker, s, evcs_obs=AT_16, tesla_obs=car)
        assert of(found, Clue.CURRENT) | of(found, Clue.PHASES) == set()
    found = clues(tracker, 121, evcs_obs=AT_16, tesla_obs=car)
    assert {ev("evcs", "tesla", Clue.CURRENT), ev("evcs", "tesla", Clue.PHASES)} <= found


@pytest.mark.parametrize(
    ("start_s", "supports"),
    [(-61, False), (-60, True), (0, True), (600, True), (601, False)],
)
def test_timing_window_reaches_from_60_s_before_the_unplugged_value_to_the_plugged_one(
    cfg, start_s, supports
) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 700, sessions={"evcs": at(start_s)}, tesla_obs=plugged)
    assert (ev("evcs", "tesla", Clue.TIMING) in found) is supports


def test_timing_needs_a_known_start(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    found = clues(tracker, 600, sessions=AT_EVCS, tesla_obs=tesla(plugged=True, plugged_at=at(600)))
    assert of(found, Clue.TIMING) == set()


@pytest.mark.parametrize(
    ("at_home", "supports"),
    [
        pytest.param(True, True, id="zu Hause"),
        pytest.param(None, True, id="Ort unbekannt"),
        pytest.param(False, False, id="woanders"),
    ],
)
def test_timing_says_nothing_about_a_tesla_that_is_away(cfg, at_home, supports) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    # das Fenster von Tessie reicht über den Beginn des Vorgangs
    car = tesla(plugged=True, at_home=at_home, plugged_at=at(600))
    found = clues(tracker, 700, sessions={"evcs": at(100)}, tesla_obs=car)
    assert (ev("evcs", "tesla", Clue.TIMING) in found) is supports


def test_unknown_tessie_values_leave_the_window_alone(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    clues(tracker, 300, sessions={}, tesla_obs=NONE)  # Tessie ist nicht erreichbar
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 600, sessions={"evcs": at(100)}, tesla_obs=plugged)
    assert ev("evcs", "tesla", Clue.TIMING) in found


def test_timing_counts_a_session_at_the_other_wallbox_that_has_ended(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    away = {"evcs_obs": evcs(connected=False), "tesla_obs": tesla(plugged_at=at(0))}
    # ein Auto kommt an den Wall Connector und fährt wieder
    clues(tracker, 50, sessions={"twc": at(50)}, twc_obs=twc(connected=True), **away)
    clues(tracker, 80, sessions={}, **away)
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 600, sessions={"evcs": at(100)}, tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


@pytest.mark.parametrize(
    ("other_s", "supports"),
    [(-100, True), (-61, True), (-60, False), (600, False), (601, True), (700, True)],
)
def test_timing_counts_a_session_at_the_other_wallbox_only_inside_the_window(
    cfg, other_s, supports
) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    sessions = {"evcs": at(100), "twc": at(other_s)}
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 900, sessions=sessions, twc_obs=twc(connected=True), tesla_obs=plugged)
    assert (ev("evcs", "tesla", Clue.TIMING) in found) is supports


def test_timing_counts_every_session_at_the_other_wallbox(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    away = {"evcs_obs": evcs(connected=False), "tesla_obs": tesla(plugged_at=at(0))}
    # ein Vorgang am Wall Connector endet, ein späterer liegt außerhalb des Fensters
    clues(tracker, 50, sessions={"twc": at(50)}, twc_obs=twc(connected=True), **away)
    clues(tracker, 80, sessions={}, **away)
    sessions = {"evcs": at(100), "twc": at(800)}
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 800, sessions=sessions, twc_obs=twc(connected=True), tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


def test_a_newer_unplugged_value_starts_a_new_window(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = clues(tracker, 600, sessions={"evcs": at(100)}, tesla_obs=plugged)
    assert ev("evcs", "tesla", Clue.TIMING) in found
    # Tessie meldet das Auto wieder abgesteckt: das Fenster ist offen
    again = tesla(plugged_at=at(900))
    found = clues(tracker, 900, sessions={"evcs": at(100)}, tesla_obs=again)
    assert of(found, Clue.TIMING) == set()
    replugged = tesla(plugged=True, plugged_at=at(1000))  # angesteckt zu einem neuen Vorgang
    found = clues(tracker, 1000, sessions={"evcs": at(950)}, tesla_obs=replugged)
    assert of(found, Clue.TIMING) == {ev("evcs", "tesla", Clue.TIMING)}


def test_a_plugged_value_from_before_the_unplugged_one_is_not_the_first_plugged_one(cfg) -> None:
    tracker = ClueTracker(cfg)
    away = {"evcs_obs": evcs(connected=False), "tesla_obs": tesla(plugged_at=at(900))}
    clues(tracker, 0, sessions={}, **away)
    # die Wanduhr ist zurückgesprungen und hat dann aufgeholt
    clues(tracker, 600, sessions={}, tesla_obs=tesla(plugged=True, plugged_at=at(600)))
    caught_up = tesla(plugged=True, plugged_at=at(1000))
    found = clues(tracker, 1000, sessions={"evcs": at(950)}, tesla_obs=caught_up)
    assert ev("evcs", "tesla", Clue.TIMING) in found


def test_later_plugged_values_do_not_move_the_end_of_the_window(cfg) -> None:
    tracker = ClueTracker(cfg)
    unplugged(tracker)
    clues(tracker, 600, sessions={}, tesla_obs=tesla(plugged=True, plugged_at=at(600)))
    later = tesla(plugged=True, plugged_at=at(2000))
    found = clues(tracker, 2000, sessions={"evcs": at(1500)}, tesla_obs=later)
    assert of(found, Clue.TIMING) == set()


def test_session_starts_are_forgotten_after_a_day(cfg) -> None:
    tracker, mine = ClueTracker(cfg), {"evcs": at(100)}
    away = {"evcs_obs": evcs(connected=False), "tesla_obs": tesla(plugged_at=at(0))}
    apart(tracker, 0, sessions={}, **away)
    apart(tracker, 50, sessions={"twc": at(50)}, twc_obs=twc(connected=True), **away)  # endet bald
    apart(tracker, 80, sessions={}, **away)
    plugged = tesla(plugged=True, plugged_at=at(600))
    for s in (600, 80_000):  # der Beginn bei 50 s ist noch keinen Tag alt
        assert of(apart(tracker, s, sessions=mine, tesla_obs=plugged), Clue.TIMING) == set()
    # jetzt ist er älter als ein Tag
    found = apart(tracker, 87_000, sessions=mine, tesla_obs=plugged)
    assert ev("evcs", "tesla", Clue.TIMING) in found


def test_the_start_of_a_running_session_is_never_forgotten(cfg) -> None:
    tracker, both = ClueTracker(cfg), {"evcs": at(100), "twc": at(50)}
    unplugged(tracker)
    plugged = tesla(plugged=True, plugged_at=at(600))
    for s in (600, 87_000):  # beide Vorgänge laufen länger als einen Tag
        found = clues(tracker, s, sessions=both, twc_obs=twc(connected=True), tesla_obs=plugged)
        assert of(found, Clue.TIMING) == set()


@pytest.mark.parametrize(
    ("plugged", "at_home", "supports"),
    [
        pytest.param(True, True, True, id="zu Hause und angesteckt"),
        pytest.param(True, False, False, id="angesteckt, aber nicht zu Hause"),
        pytest.param(True, None, False, id="angesteckt, Ort unbekannt"),
        pytest.param(False, True, False, id="zu Hause, aber nicht angesteckt"),
        pytest.param(None, True, False, id="zu Hause, Anstecken unbekannt"),
    ],
)
def test_elsewhere_needs_a_tesla_at_home_and_plugged(cfg, plugged, at_home, supports) -> None:
    # die andere Wallbox ist leer; an einer fremden Ladesäule stünde das Auto an keiner von ihnen
    car = tesla(plugged=plugged, at_home=at_home)
    expected = {ev("evcs", "tesla", Clue.ELSEWHERE)} if supports else set()
    assert of(clues(ClueTracker(cfg), 0, tesla_obs=car), Clue.ELSEWHERE) == expected


def test_every_other_wallbox_must_report_no_car(cfg) -> None:
    three = cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "twc2": cfg.wallboxes["twc"]}})
    vehicles = {"tesla": tesla(plugged=True), "egolf": NONE}

    def elsewhere(second):
        wallboxes = {"evcs": evcs(), "twc": twc(), "twc2": second}
        found = ClueTracker(three).update(T0, T0, AT_EVCS, wallboxes, vehicles, {})
        return of(set(found), Clue.ELSEWHERE)

    assert elsewhere(twc()) == {ev("evcs", "tesla", Clue.ELSEWHERE)}
    assert elsewhere(twc(connected=True)) == set()
    assert elsewhere(twc(connected=None)) == set()


def test_a_vehicle_without_tessie_has_no_tessie_clues(cfg) -> None:
    tracker = ClueTracker(cfg)
    wallboxes = {"evcs": AT_16, "twc": twc()}
    # der e-Golf hat keinen Tessie-Abschnitt und dürfte keine solchen Werte haben
    vehicles = {"tesla": tesla(), "egolf": charging_tesla(16.0)}
    for s in range(61):
        found = tracker.update(at(s), at(s), AT_EVCS, wallboxes, vehicles, {})
    assert [e for e in found if e.vehicle == "egolf"] == [ev("evcs", "egolf", Clue.PHASES, False)]


def test_the_six_clues_come_in_the_order_of_the_design(cfg) -> None:
    tracker = ClueTracker(cfg)
    away = {"evcs": evcs(connected=False), "twc": twc()}
    tracker.update(at(0), at(0), {}, away, {"tesla": tesla(plugged_at=at(0)), "egolf": NONE}, {})
    wallboxes = {"evcs": AT_16, "twc": twc()}
    vehicles = {"tesla": charging_tesla(16.0, plugged_at=at(160)), "egolf": NONE}
    for s in range(160, 221):
        found = tracker.update(
            at(s), at(s), {"evcs": at(100)}, wallboxes, vehicles, {"tesla": True}
        )
    assert [(e.vehicle, e.clue, e.supports) for e in found] == [
        ("tesla", Clue.TESSIE, True),
        ("tesla", Clue.CURRENT, True),
        ("tesla", Clue.TIMING, True),
        ("tesla", Clue.PHASES, True),
        ("tesla", Clue.HOME, True),
        ("tesla", Clue.ELSEWHERE, True),
        ("egolf", Clue.PHASES, False),
    ]


def test_evidence_follows_the_configuration_and_not_the_input(cfg) -> None:
    wallboxes = {"twc": twc(connected=True), "evcs": evcs()}
    vehicles = {"egolf": NONE, "tesla": tesla()}  # wie oben: Ausschlüsse an beiden Wallboxen
    sessions = {"twc": None, "evcs": None}
    found = ClueTracker(cfg).update(T0, T0, sessions, wallboxes, vehicles, {"egolf": False})
    assert [(e.wallbox, e.vehicle, e.clue) for e in found] == [
        ("evcs", "tesla", Clue.TESSIE),
        ("evcs", "egolf", Clue.HOME),
        ("twc", "tesla", Clue.TESSIE),
        ("twc", "egolf", Clue.HOME),
    ]


def test_the_holds_run_on_the_safety_clock(cfg) -> None:  # Präzisierung 10
    tracker, sessions = ClueTracker(cfg), {"evcs": at(100)}
    car = charging_tesla(16.0, plugged_at=at(160))
    for s in range(100, 160):
        found = apart(tracker, s, sessions=sessions, evcs_obs=AT_16, tesla_obs=car)
        assert of(found, Clue.CURRENT) | of(found, Clue.PHASES) == set()
    found = apart(tracker, 160, sessions=sessions, evcs_obs=AT_16, tesla_obs=car)
    assert {ev("evcs", "tesla", Clue.CURRENT), ev("evcs", "tesla", Clue.PHASES)} <= found


# Elimination: Tessie und Home Assistant stützen an einer Wallbox nur, wenn Strom oder Phasen das
# Fahrzeug an jeder anderen Wallbox mit Vorgang ausschließen (Abweichung von Design 7.2)


def test_tessie_and_home_need_the_vehicle_excluded_at_every_other_session(cfg) -> None:
    # „Zu Hause und angesteckt“ und „zu Hause“ sagen bei zwei Vorgängen nicht, an welcher Wallbox
    # das Fahrzeug steht. Bei einem Vorgang gibt es keine andere Wallbox; lädt an der anderen
    # nichts, schließt dort nichts das Fahrzeug aus.
    home = {"tesla_obs": tesla(plugged=True), "homes": {"egolf": True}}
    one = clues(ClueTracker(cfg), 0, **home)
    assert of(one, Clue.TESSIE) | of(one, Clue.HOME) == {
        ev("evcs", "tesla", Clue.TESSIE),
        ev("evcs", "egolf", Clue.HOME),
    }
    tracker = ClueTracker(cfg)
    for s in range(121):
        two = clues(tracker, s, sessions=BOTH, twc_obs=twc(connected=True), **home)
        assert of(two, Clue.TESSIE) | of(two, Clue.HOME) == set()


def test_current_and_phases_elsewhere_let_tessie_and_home_support(cfg) -> None:
    # Der Tesla lädt laut Tessie mit 16 A am Wall Connector, der seine Ströme nicht meldet; an der
    # EVCS lädt etwas dreiphasig mit 10 A. Ab 60 s schließen dort der Stromvergleich den Tesla und
    # die Phasen den e-Golf aus: Tessie und Home Assistant stützen beide am Wall Connector, an der
    # EVCS keins von beiden.
    kw = {
        "sessions": BOTH,
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)),
        "twc_obs": twc(connected=True, charging=True, phase_a=None),
        "tesla_obs": charging_tesla(16.0, plugged_at=T0),
        "homes": {"egolf": True},
    }
    tracker = ClueTracker(cfg)
    # die Ströme sind seit 60 s gleich, Tessie fragt bei 0 s ab (Gesamtreview I-2)
    steady(tracker, 0, evcs_obs=kw["evcs_obs"], twc_obs=kw["twc_obs"])
    for s in range(60):
        found = clues(tracker, s, **kw)
        assert of(found, Clue.TESSIE) | of(found, Clue.HOME) == set()
    found = clues(tracker, 60, **kw)
    eliminated = {ev("evcs", "tesla", Clue.CURRENT, False), ev("evcs", "egolf", Clue.PHASES, False)}
    assert eliminated <= found
    assert of(found, Clue.TESSIE) | of(found, Clue.HOME) == {
        ev("twc", "tesla", Clue.TESSIE),
        ev("twc", "egolf", Clue.HOME),
    }


def test_the_elimination_needs_every_other_session(cfg) -> None:
    # Drei Wallboxen mit Vorgang; der Tesla lädt laut Tessie mit 16 A am Wall Connector, der seine
    # Ströme nicht meldet. Schließt ihn nur die EVCS aus, bleibt der zweite Wall Connector, und
    # Tessie stützt nicht; schließt ihn auch dieser aus, stützt Tessie am Wall Connector.
    three = cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "twc2": cfg.wallboxes["twc"]}})
    sessions = {"evcs": None, "twc": None, "twc2": None}
    at_10 = dict.fromkeys(PHASES, 10.0)
    vehicles = {"tesla": charging_tesla(16.0, plugged_at=T0), "egolf": NONE}

    def tessie(second):
        tracker = ClueTracker(three)
        wallboxes = {
            "evcs": evcs(charging=True, phase_a=at_10),
            "twc": twc(connected=True, charging=True, phase_a=None),
            "twc2": second,
        }
        # die Ströme sind seit 60 s gleich, Tessie fragt bei 0 s ab (Gesamtreview I-2)
        unknown = {"tesla": NONE, "egolf": NONE}
        for s in range(-60, 0):
            tracker.update(at(s), at(s), {}, wallboxes, unknown, {})
        for s in range(61):
            found = tracker.update(at(s), at(s), sessions, wallboxes, vehicles, {})
        return of(set(found), Clue.TESSIE)

    assert tessie(twc(connected=True)) == set()
    assert tessie(twc(connected=True, charging=True, phase_a=at_10)) == {
        ev("twc", "tesla", Clue.TESSIE)
    }


@pytest.mark.parametrize(
    "car",
    [
        pytest.param(tesla(), id="Tesla nicht angesteckt"),
        pytest.param(tesla(plugged=True, at_home=False), id="Tesla nicht zu Hause"),
    ],
)
def test_tessie_and_home_assistant_still_exclude_at_every_session(cfg, car) -> None:
    found = clues(
        ClueTracker(cfg),
        0,
        sessions=BOTH,
        twc_obs=twc(connected=True),
        tesla_obs=car,
        homes={"egolf": False},
    )
    assert of(found, Clue.TESSIE) | of(found, Clue.HOME) == {
        ev(wallbox, vehicle, clue, False)
        for wallbox in ("evcs", "twc")
        for vehicle, clue in (("tesla", Clue.TESSIE), ("egolf", Clue.HOME))
    }


# Abfahrt: Endet an einer Wallbox ein Vorgang, stützen Tessie und Ausschluss ein Fahrzeug an einer
# anderen Wallbox erst wieder mit einem Wert, der mindestens 60 s nach dem Ende abgefragt wurde
# (Präzisierung 3, auf Enden angewandt; Gesamtreview I-1)

LEFT_S = 100  # da steckt der Tesla am Wall Connector ab und fährt
PARKED = {"evcs": None, "twc": None}  # der e-Golf an der EVCS, der Tesla am Wall Connector


def departure_step(tracker, s, value, homes=None):
    """Die Sekunde ``s`` der Abfahrt: Bis ``LEFT_S`` stehen beide Autos, dann ist der Wall
    Connector leer; der e-Golf lädt nicht. ``value(s)`` ist der Wert, den Tessie meldet."""
    there = s < LEFT_S
    return clues(
        tracker,
        s,
        sessions=PARKED if there else AT_EVCS,
        twc_obs=twc(connected=there),
        tesla_obs=value(s),
        homes=homes,
    )


def departure(tracker, until_s, value, homes=None):
    """Die Abfahrt von 0 s bis ``until_s``; zurück kommen die Indizien der letzten Sekunde."""
    for s in range(until_s + 1):
        found = departure_step(tracker, s, value, homes)
    return found


def stale(polled_s):
    """Tessie meldet den Tesla zu Hause und angesteckt: ab ``polled_s`` mit dem Wert dieser
    Abfrage, davor mit dem von 0 s."""
    return lambda s: tesla(plugged=True, plugged_at=at(polled_s if s >= polled_s else 0))


def supported(found):
    """Was Tessie und Ausschluss an der EVCS über den Tesla sagen."""
    return {e for e in of(found, Clue.TESSIE) | of(found, Clue.ELSEWHERE) if e.wallbox == "evcs"}


@pytest.mark.parametrize(
    ("polled_s", "supports"),
    [
        pytest.param(LEFT_S - 30, False, id="vor dem Ende abgefragt"),
        pytest.param(LEFT_S + 59, False, id="59 s nach dem Ende"),
        pytest.param(LEFT_S + 60, True, id="60 s nach dem Ende"),
        pytest.param(LEFT_S + 90, True, id="90 s nach dem Ende"),
    ],
)
def test_after_a_departure_tessie_supports_only_with_a_value_polled_60_s_later(
    cfg, polled_s, supports
) -> None:
    # Der Tesla steckt bei 100 s am Wall Connector ab und fährt; Tessie meldet ihn noch 90 s zu
    # Hause und angesteckt. An der EVCS steht der e-Golf und lädt nicht.
    found = departure(ClueTracker(cfg), LEFT_S + 90, stale(polled_s))
    both = {ev("evcs", "tesla", Clue.TESSIE), ev("evcs", "tesla", Clue.ELSEWHERE)}
    assert supported(found) == (both if supports else set())


def test_after_a_departure_tessie_never_supports_within_60_s(cfg) -> None:
    # Tessie fragt in jedem Zyklus ab und meldet den Tesla weiter angesteckt
    tracker, value = ClueTracker(cfg), lambda s: tesla(plugged=True, plugged_at=at(s))
    for s in range(LEFT_S + 60):
        found = departure_step(tracker, s, value)
        if s >= LEFT_S:
            assert supported(found) == set()
    assert ev("evcs", "tesla", Clue.ELSEWHERE) in departure_step(tracker, LEFT_S + 60, value)


def test_after_a_departure_tessie_still_excludes_at_once(cfg) -> None:
    # 10 s nach dem Ende meldet Tessie den Tesla abgesteckt: Das schließt ihn an der EVCS sofort aus
    def value(s):
        if s < LEFT_S + 10:
            return tesla(plugged=True, plugged_at=at(0))
        return tesla(plugged_at=at(LEFT_S + 10))

    found = departure(ClueTracker(cfg), LEFT_S + 10, value)
    assert supported(found) == {ev("evcs", "tesla", Clue.TESSIE, False)}


def test_without_a_timestamp_tessie_supports_no_more_after_an_end(cfg) -> None:
    found = departure(ClueTracker(cfg), LEFT_S + 300, lambda s: tesla(plugged=True))
    assert supported(found) == set()


def test_a_new_start_at_the_same_wallbox_ends_its_session(cfg) -> None:
    # Am Wall Connector lädt ein Auto auf zwei Phasen: Ab 60 s schließen die Phasen den Tesla dort
    # aus, und Tessie stützt ihn an der EVCS. Bei 100 s beginnt am Wall Connector ein neuer
    # Vorgang, ohne einen Zyklus ohne Fahrzeug dazwischen. Auch das ist ein Ende: Tessies Wert von
    # 90 s stützt nicht mehr, einer von 160 s wieder.
    tracker = ClueTracker(cfg)
    two = twc(connected=True, charging=True, phase_a=TWO)

    def step(s, start_s, polled_s):
        sessions = {"evcs": None, "twc": at(start_s)}
        car = tesla(plugged=True, plugged_at=at(polled_s))
        return supported(clues(tracker, s, sessions=sessions, twc_obs=two, tesla_obs=car))

    for s in range(100):
        found = step(s, 10, 0)
    assert found == {ev("evcs", "tesla", Clue.TESSIE)}
    assert all(step(s, 100, 90) == set() for s in range(100, 160))
    assert step(160, 100, 160) == {ev("evcs", "tesla", Clue.TESSIE)}


def test_home_assistant_needs_no_settling(cfg) -> None:
    # Home Assistant meldet jede Änderung sofort: Es stützt den e-Golf an einer Wallbox, deren
    # Vorgang gerade begann (Präzisierung 3), und gleich nach dem Ende eines Vorgangs an der anderen
    begun = clues(ClueTracker(cfg), 200, sessions={"evcs": at(200)}, homes={"egolf": True})
    assert of(begun, Clue.HOME) == {ev("evcs", "egolf", Clue.HOME)}
    tracker, home = ClueTracker(cfg), {"egolf": True}
    before = departure(tracker, LEFT_S - 1, stale(0), homes=home)
    assert of(before, Clue.HOME) == set()  # bei zwei Vorgängen erst nach der Elimination
    after = departure_step(tracker, LEFT_S, stale(0), homes=home)
    assert of(after, Clue.HOME) == {ev("evcs", "egolf", Clue.HOME)}


# Stromvergleich am eigenen Wall Connector (Gesamtreview I-2): Eine unplausible Phasenzahl von
# Tessie zählt als unbekannt, und „passt nicht“ zählt erst mit einem Wert, den Tessie mindestens
# 60 s nach der letzten Änderung der Ströme der Wallbox abgefragt hat

TWC_16 = twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0))
TWC_10 = twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 10.0))
AT_TWC = {"twc": None}  # am Wall Connector läuft ein Vorgang, sein Beginn ist unbekannt
JUMP_S = 200  # da springt der Strom des Wall Connectors von 16 auf 10 A


def at_the_twc(tracker, s, wallbox, car, sessions=AT_TWC, evcs_obs=None):
    """Die Indizien am Wall Connector in der Sekunde ``s``."""
    found = clues(
        tracker,
        s,
        sessions=sessions,
        evcs_obs=evcs_obs or evcs(connected=False),
        twc_obs=wallbox,
        tesla_obs=car,
    )
    return {e for e in found if e.wallbox == "twc"}


def test_a_phase_count_from_tessie_other_than_the_configured_one_says_nothing(cfg) -> None:
    # Der Tesla lädt am Wall Connector mit 3 × 16 A, Tessie meldet zwei Phasen
    tracker = ClueTracker(cfg)
    for s in range(200):
        car = charging_tesla(16.0, phases=2, plugged_at=at(s))
        assert of(at_the_twc(tracker, s, TWC_16, car), Clue.CURRENT) == set()


def lagging(s, lag_s=70):
    """Tessie fragt in jedem Zyklus ab und meldet bis ``lag_s`` nach ``JUMP_S`` noch 16 A."""
    return charging_tesla(16.0 if s < JUMP_S + lag_s else 10.0, plugged_at=at(s))


def test_a_lagging_tessie_never_excludes_the_tesla_at_its_wall_connector(cfg) -> None:
    tracker, seen = ClueTracker(cfg), []
    for s in range(JUMP_S + 200):
        found = at_the_twc(tracker, s, TWC_16 if s < JUMP_S else TWC_10, lagging(s))
        seen.append(of(found, Clue.CURRENT))
    assert ev("twc", "tesla", Clue.CURRENT, False) not in set().union(*seen)
    # 60 s nach Tessies erstem Wert mit 10 A passt der Strom wieder
    current = ev("twc", "tesla", Clue.CURRENT)
    assert [s for s, found in enumerate(seen) if current in found][-1] == JUMP_S + 199
    assert seen[JUMP_S + 70 + 59] == set() and seen[JUMP_S + 70 + 60] == {current}


def test_a_guest_with_a_steady_other_current_is_still_excluded(cfg) -> None:
    # Der Tesla lädt an der EVCS mit 3 × 16 A, am Wall Connector lädt ein Gast stabil mit 3 × 10 A.
    # Tessie fragt alle 30 s ab: Der Wert von 60 s ist der erste, der 60 s nach dem ersten Zyklus
    # der Wallbox abgefragt wurde; 60 s später schließt der Stromvergleich den Tesla dort aus.
    tracker, excluded = ClueTracker(cfg), []
    for s in range(200):
        car = charging_tesla(16.0, plugged_at=at(s - s % 30))
        found = at_the_twc(tracker, s, TWC_10, car, sessions=BOTH, evcs_obs=AT_16)
        if ev("twc", "tesla", Clue.CURRENT, False) in found:
            excluded.append(s)
    assert excluded == list(range(120, 200))


def test_currents_that_match_right_after_a_change_still_support(cfg) -> None:
    # Der Strom springt bei 200 s auf 10 A, und Tessie meldet gleich 10 A, abgefragt bei 205 s:
    # „passt“ zählt ohne die 60 s nach der Änderung
    tracker = ClueTracker(cfg)
    for s in range(JUMP_S):
        at_the_twc(tracker, s, TWC_16, NONE)
    car = charging_tesla(10.0, plugged_at=at(JUMP_S + 5))
    for s in range(JUMP_S, JUMP_S + 65):
        found = of(at_the_twc(tracker, s, TWC_10, car), Clue.CURRENT)
        assert found == (set() if s < JUMP_S + 60 else {ev("twc", "tesla", Clue.CURRENT)})


@pytest.mark.parametrize(
    ("before", "after", "changed"),
    [
        pytest.param(
            {"L1": 16.0, "L2": 16.0, "L3": 16.0},
            {"L1": 15.0, "L2": 16.0, "L3": 16.0},
            False,
            id="1 A weniger",
        ),
        pytest.param(
            {"L1": 16.0, "L2": 16.0, "L3": 16.0},
            {"L1": 14.9, "L2": 16.0, "L3": 16.0},
            True,
            id="mehr als 1 A weniger",
        ),
        pytest.param(
            {"L1": 16.0, "L2": 16.0, "L3": 1.5},
            {"L1": 16.0, "L2": 16.0, "L3": 0.9},
            True,
            id="eine Phase hört auf zu laden",
        ),
        pytest.param(
            {"L1": 16.0, "L2": 16.0, "L3": 0.9},
            {"L1": 16.0, "L2": 16.0, "L3": 1.0},
            True,
            id="eine Phase beginnt zu laden",
        ),
    ],
)
def test_what_counts_as_a_change_of_the_currents(cfg, before, after, changed) -> None:
    # Die Wallbox misst 200 s lang ``before``, dann ``after``; Tessie meldet 3 × 20 A, abgefragt
    # bei 230 s. Ohne Änderung schließt der Stromvergleich ab 260 s aus; nach einer Änderung bei
    # 200 s nicht, denn der Wert lag keine 60 s danach.
    tracker = ClueTracker(cfg)
    for s in range(200):
        at_the_twc(tracker, s, replace(TWC_16, phase_a=before), NONE)
    car = charging_tesla(20.0, plugged_at=at(230))
    for s in range(200, 261):
        found = of(at_the_twc(tracker, s, replace(TWC_16, phase_a=after), car), Clue.CURRENT)
    assert (found == {ev("twc", "tesla", Clue.CURRENT, False)}) is not changed


@pytest.mark.parametrize(
    "before",
    [
        pytest.param(replace(TWC_16, charging=False), id="Laden beginnt"),
        pytest.param(replace(TWC_16, charging=None), id="Laden wieder bekannt"),
        pytest.param(replace(TWC_16, phase_a=None), id="Ströme wieder bekannt"),
    ],
)
def test_charging_that_begins_is_a_change(cfg, before) -> None:
    # Ab 200 s lädt der Wall Connector mit 3 × 16 A; Tessies Wert von 230 s lag keine 60 s danach.
    # Dass das Ende des Ladens zählt, zeigt sich erst, wenn es wieder beginnt.
    tracker = ClueTracker(cfg)
    for s in range(200):
        at_the_twc(tracker, s, before, NONE)
    car = charging_tesla(20.0, plugged_at=at(230))
    seen = set()
    for s in range(200, 291):
        seen |= of(at_the_twc(tracker, s, TWC_16, car), Clue.CURRENT)
    assert seen == set()
