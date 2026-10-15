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


def test_tessie_supports_at_every_session_or_excludes(cfg) -> None:
    both = {"evcs": None, "twc": None}
    found = clues(
        ClueTracker(cfg),
        0,
        sessions=both,
        twc_obs=twc(connected=True),
        tesla_obs=tesla(plugged=True),
    )
    assert of(found, Clue.TESSIE) == {
        ev("evcs", "tesla", Clue.TESSIE),
        ev("twc", "tesla", Clue.TESSIE),
    }
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
    # drei Phasen, die EVCS misst zwei
    car = tesla(plugged=True, charging=True, actual_current_a=10.0, phases=3)
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
    wallboxes = {"evcs": evcs(), "twc": twc(connected=True)}
    vehicles = {"tesla": tesla(plugged=True), "egolf": NONE}
    found = ClueTracker(cfg).update(
        T0, T0, {"evcs": None, "twc": None}, wallboxes, vehicles, {"egolf": True}
    )
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
    ("wallbox", "car", "expected"),
    [
        pytest.param(
            AT_16, charging_tesla(15.0), {ev("evcs", "tesla", Clue.CURRENT)}, id="1 A Abstand"
        ),
        pytest.param(
            AT_16,
            charging_tesla(14.5),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            id="1,5 A Abstand",
        ),
        pytest.param(
            AT_16,
            charging_tesla(16.0, phases=2),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            id="Phasenzahl passt nicht",
        ),
        pytest.param(
            evcs(charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.9}),
            charging_tesla(16.0, phases=2),
            {ev("evcs", "tesla", Clue.CURRENT)},
            id="0,9 A ist keine Phase",
        ),
        pytest.param(
            evcs(charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 1.0}),
            charging_tesla(16.0, phases=2),
            {ev("evcs", "tesla", Clue.CURRENT, False)},
            id="1 A ist eine Phase",
        ),
        pytest.param(
            evcs(charging=True, phase_a=dict.fromkeys(PHASES, 1.0)),
            charging_tesla(1.0),
            {ev("evcs", "tesla", Clue.CURRENT)},
            id="1 A lädt das Auto",
        ),
        pytest.param(AT_16, charging_tesla(0.9), set(), id="0,9 A lädt das Auto nicht"),
        pytest.param(
            AT_16, replace(charging_tesla(16.0), charging=False), set(), id="Tessie lädt nicht"
        ),
        pytest.param(
            AT_16, replace(charging_tesla(16.0), phases=None), set(), id="Phasenzahl unbekannt"
        ),
        pytest.param(
            AT_16, replace(charging_tesla(16.0), actual_current_a=None), set(), id="Strom unbekannt"
        ),
        pytest.param(
            evcs(charging=False, phase_a=dict.fromkeys(PHASES, 16.0)),
            charging_tesla(16.0),
            set(),
            id="Wallbox lädt nicht",
        ),
        pytest.param(
            evcs(charging=True, phase_a=None),
            charging_tesla(16.0),
            set(),
            id="Ströme der Wallbox unbekannt",
        ),
    ],
)
def test_current_comparison_at_the_limits(cfg, wallbox, car, expected) -> None:
    tracker = ClueTracker(cfg)
    for s in range(60):
        clues(tracker, s, evcs_obs=wallbox, tesla_obs=car)
    assert of(clues(tracker, 60, evcs_obs=wallbox, tesla_obs=car), Clue.CURRENT) == expected


def test_current_comparison_starts_again_when_the_result_changes(cfg) -> None:
    tracker = ClueTracker(cfg)
    for s in range(30):
        clues(tracker, s, evcs_obs=AT_16, tesla_obs=charging_tesla(16.0))  # passt
    off = charging_tesla(10.0)  # 6 A Abweichung
    assert all(
        of(clues(tracker, s, evcs_obs=AT_16, tesla_obs=off), Clue.CURRENT) == set()
        for s in range(30, 90)
    )
    assert of(clues(tracker, 90, evcs_obs=AT_16, tesla_obs=off), Clue.CURRENT) == {
        ev("evcs", "tesla", Clue.CURRENT, False)
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
    vehicles = {"egolf": NONE, "tesla": tesla(plugged=True)}
    sessions = {"twc": None, "evcs": None}
    found = ClueTracker(cfg).update(T0, T0, sessions, wallboxes, vehicles, {"egolf": True})
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
