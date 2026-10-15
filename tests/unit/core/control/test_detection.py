from datetime import timedelta

from tests.helpers import T0
from tests.unit.core.control.charge_helpers import NONE, evcs, tesla, twc

from bindaems.core.control.clues import Clue, ClueTracker, Evidence
from bindaems.core.control.detection import CANDIDATE_SUPPORTS, Detector
from bindaems.core.state.derived import PHASES

# Vorgaben des Plans

AT_EVCS = {"evcs": None}  # an der EVCS läuft ein Vorgang, sein Beginn ist unbekannt
BOTH = {"evcs": None, "twc": None}
TWO = {"L1": 10.0, "L2": 10.0, "L3": 0.0}
# Tessie und Ausschluss stützen den Tesla an der EVCS
TESLA_AT_EVCS = {"tesla_obs": tesla(plugged=True)}


def detect(
    d,
    s,
    *,
    sessions=AT_EVCS,
    evcs_obs=None,
    twc_obs=None,
    tesla_obs=None,
    homes=None,
    placed=None,
    wall_offset=timedelta(0),
):
    when = T0 + timedelta(seconds=s)
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    return d.update(
        when, when + wall_offset, sessions, wallboxes, vehicles, homes or {}, placed or {}
    )


def test_tesla_at_the_evcs_is_detected_after_60_s(cfg) -> None:
    d = Detector(cfg)
    assert all(detect(d, s, **TESLA_AT_EVCS).detected == {} for s in range(60))
    found = detect(d, 60, **TESLA_AT_EVCS)
    assert (found.detected, found.contradiction) == ({"evcs": "tesla"}, False)


def test_egolf_at_the_wall_connector_by_home_and_phases(cfg) -> None:
    egolf = twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0})
    kw = {
        "sessions": {"twc": None},
        "evcs_obs": evcs(connected=False),
        "twc_obs": egolf,
        "homes": {"egolf": True},
    }
    d = Detector(cfg)
    # Phasen ab 60 s, erkannt 60 s danach
    assert all(detect(d, s, **kw).detected == {} for s in range(120))
    assert detect(d, 120, **kw).detected == {"twc": "egolf"}


def test_guest_when_every_vehicle_is_excluded_for_60_s(cfg) -> None:
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
        "homes": {"egolf": False},
    }
    d = Detector(cfg)
    first = detect(d, 0, **kw)  # Tesla nicht angesteckt, e-Golf weg: beide sofort ausgeschlossen
    assert (first.guests, first.excluded["evcs"]) == (frozenset(), {"tesla", "egolf"})
    assert all(detect(d, s, **kw).guests == frozenset() for s in range(1, 60))
    assert detect(d, 60, **kw).guests == {"evcs"}


def test_a_vehicle_with_two_supports_at_two_wallboxes_is_a_contradiction(cfg) -> None:
    # Tesla am Wall Connector, dreiphasiger Gast an der EVCS, gleicher Strom
    same = dict.fromkeys(PHASES, 16.0)
    kw = {
        "sessions": BOTH,
        "evcs_obs": evcs(charging=True, phase_a=same),
        "twc_obs": twc(connected=True, charging=True, phase_a=same),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
    }
    d = Detector(cfg)
    assert not any(detect(d, s, **kw).contradiction for s in range(60))
    found = detect(d, 60, **kw)
    assert (found.contradiction, found.detected) == (True, {})


def test_two_supports_and_an_exclusion_are_a_contradiction(cfg) -> None:
    # die Phasen schließen den Tesla aus
    kw = {"evcs_obs": evcs(charging=True, phase_a=TWO), **TESLA_AT_EVCS}
    d = Detector(cfg)
    assert not any(detect(d, s, **kw).contradiction for s in range(60))
    found = detect(d, 60, **kw)
    # die Phasen zählen im selben Zyklus: nie erkannt
    assert (found.contradiction, found.detected) == (True, {})


def test_an_exclusion_next_to_a_detection_is_no_contradiction(cfg) -> None:  # Präzisierung 4
    # Der Tesla lädt am Wall Connector, dessen Vorgang schon vor dem Start des core lief. Tessie
    # meldet ihn bei 0 s abgesteckt, dann nichts und bei 61 s angesteckt. Bei 1 s kommt ein
    # dreiphasiger Gast an die EVCS, der ab 61 s mit 10 A lädt. Bei zwei Vorgängen stützt Tessie
    # nicht; ab 121 s stützen den Tesla an der EVCS die zeitliche Korrelation und die Phasen, und
    # der Stromvergleich schließt ihn dort aus. Am Wall Connector stützen ihn Strom und Phasen.
    started = {"evcs": T0 + timedelta(seconds=1), "twc": None}
    tesla_twc = twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0))
    d = Detector(cfg)
    alone = {"sessions": {"twc": None}, "evcs_obs": evcs(connected=False), "twc_obs": tesla_twc}
    detect(d, 0, tesla_obs=tesla(plugged_at=T0), **alone)
    assert not any(
        detect(d, s, sessions=started, twc_obs=tesla_twc, tesla_obs=NONE).contradiction
        for s in range(1, 61)
    )
    kw = {
        "sessions": started,
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)),
        "twc_obs": tesla_twc,
        "tesla_obs": tesla(
            plugged=True,
            charging=True,
            actual_current_a=16.0,
            phases=3,
            plugged_at=T0 + timedelta(seconds=61),
        ),
    }
    for s in range(61, 181):
        found = detect(d, s, **kw)
        assert (found.detected, found.contradiction) == ({}, False)
    found = detect(d, 181, **kw)
    assert (found.detected, found.guests, found.contradiction) == (
        {"twc": "tesla"},
        {"evcs"},
        False,
    )
    # an der EVCS zwei Stützen und ein Ausschluss, neben der Erkennung am Wall Connector
    at_the_evcs = {
        (e.clue, e.supports) for e in found.evidence if (e.wallbox, e.vehicle) == ("evcs", "tesla")
    }
    assert at_the_evcs == {(Clue.TIMING, True), (Clue.PHASES, True), (Clue.CURRENT, False)}


def test_an_override_pins_its_wallbox(cfg) -> None:
    kw = {"sessions": BOTH, "twc_obs": twc(connected=True), "placed": {"egolf": "evcs"}}
    d = Detector(cfg)
    for s in range(60):
        detect(d, s, **kw)
    # am Wall Connector: Tesla nicht angesteckt, e-Golf an der EVCS übersteuert
    found = detect(d, 60, **kw)
    assert (found.detected, found.guests, found.excluded["twc"]) == (
        {},
        {"twc"},
        {"tesla", "egolf"},
    )


def test_detection_restarts_after_an_interruption(cfg) -> None:  # Review Focus 2
    d = Detector(cfg)
    for s in range(59):
        detect(d, s, **TESLA_AT_EVCS)
    detect(d, 59, tesla_obs=NONE)  # Tessie unbekannt: einen Zyklus kein Kandidat
    assert all(detect(d, s, **TESLA_AT_EVCS).detected == {} for s in range(60, 120))
    assert detect(d, 120, **TESLA_AT_EVCS).detected == {"evcs": "tesla"}


def test_holds_end_with_the_session(cfg) -> None:  # Review Focus 5
    d = Detector(cfg)
    for s in range(61):
        detect(d, s, **TESLA_AT_EVCS)
    detect(d, 61, sessions={}, evcs_obs=evcs(connected=False), **TESLA_AT_EVCS)
    # neuer Vorgang: die 60 s von vorn
    assert detect(d, 62, **TESLA_AT_EVCS).detected == {}


# Ergänzungen: Schwellen, Übersteuerung, Gast, Uhren

# Tesla nicht angesteckt, e-Golf weg: an der EVCS ist jedes Fahrzeug ausgeschlossen
GUEST_AT_EVCS = {
    "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
    "homes": {"egolf": False},
}


def test_the_constants_are_the_agreed_ones() -> None:
    assert CANDIDATE_SUPPORTS == 2


def test_one_supporting_clue_is_not_enough(cfg) -> None:
    # der Wall Connector meldet ein Fahrzeug: Der Ausschluss entfällt, es bleibt Tessie allein
    kw = {"twc_obs": twc(connected=True), **TESLA_AT_EVCS}
    d = Detector(cfg)
    for s in range(121):
        found = detect(d, s, **kw)
        assert (found.detected, found.guests, found.contradiction) == ({}, frozenset(), False)


def test_a_vehicle_is_detected_as_soon_as_it_is_the_candidate_of_one_wallbox_only(cfg) -> None:
    # Beide Wallboxen laden dreiphasig mit dem Strom des Tesla: Strom und Phasen stützen ihn ab
    # 60 s an beiden
    kw = {
        "sessions": BOTH,
        "twc_obs": twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
    }
    same = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0))
    d = Detector(cfg)
    for s in range(60):
        assert not detect(d, s, evcs_obs=same, **kw).contradiction
    # Bis 150 s ist er an beiden Kandidat und nirgends erkannt, obwohl er am Wall Connector schon
    # länger als 60 s zählt
    for s in range(60, 150):
        found = detect(d, s, evcs_obs=same, **kw)
        assert (found.contradiction, found.detected) == (True, {})
    # Dann lädt die EVCS mit 10 A, und der Strom stützt ihn dort nicht mehr: Er ist nur noch am
    # Wall Connector Kandidat und sofort erkannt, seine 60 s liefen schon
    other = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0))
    found = detect(d, 150, evcs_obs=other, **kw)
    assert (found.contradiction, found.detected) == (False, {"twc": "tesla"})


def test_two_detected_vehicles_at_one_wallbox_cancel_each_other(cfg) -> None:
    egolf = cfg.vehicles["egolf"].model_copy(update={"phases": 3})
    three = cfg.model_copy(update={"vehicles": {**cfg.vehicles, "egolf": egolf}})
    # Home Assistant und die Phasen stützen beide Fahrzeuge; Tessie weiß nicht, ob der Tesla steckt
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
        "tesla_obs": tesla(plugged=None),
    }
    d = Detector(three)
    for s in range(121):
        found = detect(d, s, homes={"tesla": True, "egolf": True}, **kw)
        assert (found.detected, found.contradiction) == ({}, False)
    # ist der e-Golf nicht mehr zu Hause, ist der Tesla allein erkannt: Seine 60 s liefen schon
    found = detect(d, 121, homes={"tesla": True, "egolf": False}, **kw)
    assert found.detected == {"evcs": "tesla"}


def test_no_guest_while_a_vehicle_is_not_excluded(cfg) -> None:
    d = Detector(cfg)  # der Tesla ist nicht angesteckt, über den e-Golf weiß niemand etwas
    for s in range(121):
        found = detect(d, s)
        assert (found.guests, found.excluded["evcs"]) == (frozenset(), {"tesla"})


def test_the_guest_restarts_after_an_interruption(cfg) -> None:
    d = Detector(cfg)
    for s in range(59):
        detect(d, s, **GUEST_AT_EVCS)
    # einen Zyklus ist der e-Golf nicht mehr ausgeschlossen
    detect(d, 59, **{**GUEST_AT_EVCS, "homes": {"egolf": True}})
    assert all(detect(d, s, **GUEST_AT_EVCS).guests == frozenset() for s in range(60, 120))
    assert detect(d, 120, **GUEST_AT_EVCS).guests == {"evcs"}


def test_the_guest_ends_with_the_session(cfg) -> None:
    d = Detector(cfg)
    for s in range(61):
        detect(d, s, **GUEST_AT_EVCS)
    gone = detect(d, 61, sessions={}, **GUEST_AT_EVCS)
    assert (gone.guests, gone.excluded, gone.detected) == (frozenset(), {}, {})
    # neuer Vorgang: die 60 s von vorn
    assert all(detect(d, s, **GUEST_AT_EVCS).guests == frozenset() for s in range(62, 122))
    assert detect(d, 122, **GUEST_AT_EVCS).guests == {"evcs"}


def test_excluded_lists_every_wallbox_with_a_session_also_a_pinned_one(cfg) -> None:
    kw = {"twc_obs": twc(connected=True), "placed": {"egolf": "evcs"}}
    both = detect(Detector(cfg), 0, sessions=BOTH, **kw)
    assert both.excluded == {"evcs": {"tesla"}, "twc": {"tesla", "egolf"}}
    assert detect(Detector(cfg), 0, sessions={"twc": None}, **kw).excluded == {
        "twc": {"tesla", "egolf"}
    }
    assert detect(Detector(cfg), 0, sessions={}, **kw).excluded == {}


# Übersteuerung


def test_a_pinned_wallbox_has_no_detection(cfg) -> None:
    d = Detector(cfg)  # der Tesla wäre an der EVCS erkannt: Dort ist der e-Golf übersteuert
    for s in range(121):
        found = detect(d, s, placed={"egolf": "evcs"}, **TESLA_AT_EVCS)
        assert (found.detected, found.guests, found.contradiction) == ({}, frozenset(), False)
        assert found.excluded == {"evcs": frozenset()}
    # die Indizien stehen trotzdem im Ergebnis
    assert {(e.clue, e.supports) for e in found.evidence if e.vehicle == "tesla"} == {
        (Clue.TESSIE, True),
        (Clue.ELSEWHERE, True),
    }


def test_a_pinned_wallbox_has_no_guest_even_if_everyone_is_excluded(cfg) -> None:
    d = Detector(cfg)
    for s in range(121):
        found = detect(d, s, placed={"egolf": "evcs"}, homes={"egolf": False})
        assert found.guests == frozenset()
    assert found.excluded["evcs"] == {"tesla", "egolf"}


def test_a_pinned_wallbox_contradicts_nothing(cfg) -> None:
    # wie bei zwei Stützen und einem Ausschluss ohne Übersteuerung, aber die EVCS ist übersteuert
    kw = {"evcs_obs": evcs(charging=True, phase_a=TWO), **TESLA_AT_EVCS}
    d = Detector(cfg)
    assert not any(detect(d, s, placed={"egolf": "evcs"}, **kw).contradiction for s in range(121))


def test_an_overridden_vehicle_is_excluded_elsewhere_and_contradicts_nothing(cfg) -> None:
    # Der Tesla lädt am Wall Connector, an der EVCS ein dreiphasiger Gast mit demselben Strom:
    # Strom und Phasen stützen den Tesla ab 60 s an beiden Wallboxen. Am Wall Connector ist er
    # übersteuert.
    same = dict.fromkeys(PHASES, 16.0)
    kw = {
        "sessions": BOTH,
        "evcs_obs": evcs(charging=True, phase_a=same),
        "twc_obs": twc(connected=True, charging=True, phase_a=same),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
    }
    unpinned = Detector(cfg)
    for s in range(60):
        detect(unpinned, s, **kw)
    assert detect(unpinned, 60, **kw).contradiction  # ohne Übersteuerung: zwei Kandidaten
    d = Detector(cfg)
    for s in range(121):
        found = detect(d, s, placed={"tesla": "twc"}, **kw)
        assert (found.detected, found.contradiction) == ({}, False)
    # An der EVCS ist jedes Fahrzeug ausgeschlossen, der e-Golf ab 60 s nach den Phasen: ein Gast
    assert found.excluded == {"evcs": {"tesla", "egolf"}, "twc": {"egolf"}}
    assert found.guests == {"evcs"}


def test_a_pinned_wallbox_decides_again_when_the_override_ends(cfg) -> None:
    d = Detector(cfg)
    for s in range(100):
        detect(d, s, placed={"egolf": "evcs"}, **TESLA_AT_EVCS)
    # die 60 s als Kandidat beginnen erst jetzt
    assert all(detect(d, s, **TESLA_AT_EVCS).detected == {} for s in range(100, 160))
    assert detect(d, 160, **TESLA_AT_EVCS).detected == {"evcs": "tesla"}


# Indizien und Uhren


def test_the_evidence_is_the_one_of_the_clues(cfg) -> None:
    wallboxes = {"evcs": evcs(), "twc": twc(connected=True)}
    vehicles = {"tesla": tesla(plugged=True), "egolf": NONE}
    homes = {"egolf": False}  # bei zwei Vorgängen schließt Home Assistant nur aus
    expected = tuple(ClueTracker(cfg).update(T0, T0, BOTH, wallboxes, vehicles, homes))
    found = Detector(cfg).update(T0, T0, BOTH, wallboxes, vehicles, homes, {"egolf": "evcs"})
    assert found.evidence == expected
    # auch die der übersteuerten EVCS
    assert Evidence("evcs", "egolf", Clue.HOME, False) in found.evidence


def test_a_tesla_charging_away_is_no_contradiction_next_to_a_three_phase_guest(cfg) -> None:
    # an einer öffentlichen Säule mit 16 A auf drei Phasen wie der Gast an unserer EVCS
    kw = {
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
        "tesla_obs": tesla(
            plugged=True, at_home=False, charging=True, actual_current_a=16.0, phases=3
        ),
    }
    d = Detector(cfg)
    for s in range(121):
        found = detect(d, s, **kw)
        assert (found.contradiction, found.detected) == (False, {})
    # Tessie schließt den Tesla aus, die Phasen den e-Golf
    assert (found.guests, found.excluded["evcs"]) == ({"evcs"}, {"tesla", "egolf"})


def test_the_wall_clock_reaches_the_clues(cfg) -> None:  # Präzisierung 10
    # die Uhr der Safety-Schicht läuft zehn Tage vor der Wanduhr
    d, tracker, apart = Detector(cfg), ClueTracker(cfg), timedelta(days=10)
    away = {"evcs": evcs(connected=False), "twc": twc()}

    def at(s):
        return T0 + timedelta(seconds=s)

    def step(s, sessions, wallboxes, car):
        vehicles = {"tesla": car, "egolf": NONE}
        found = d.update(at(s) + apart, at(s), sessions, wallboxes, vehicles, {}, {})
        assert found.evidence == tuple(
            tracker.update(at(s) + apart, at(s), sessions, wallboxes, vehicles, {})
        )
        return found

    step(0, {}, away, tesla(plugged_at=at(0)))
    # ein Auto kommt an den Wall Connector und fährt wieder
    step(
        50,
        {"twc": at(50)},
        {"evcs": evcs(connected=False), "twc": twc(connected=True)},
        tesla(plugged_at=at(0)),
    )
    step(80, {}, away, tesla(plugged_at=at(0)))
    plugged = tesla(plugged=True, plugged_at=at(600))
    found = step(600, {"evcs": at(100)}, {"evcs": evcs(), "twc": twc()}, plugged)
    # der Beginn bei 50 s ist noch keinen Tag alt: kein Vorgang begann allein im Fenster
    assert Clue.TIMING not in {e.clue for e in found.evidence}


def jumping(s):
    """Die Wanduhr springt bei 30 s einen Tag vor (NTP); die Uhr der Safety-Schicht nicht."""
    return timedelta(days=1) if s >= 30 else timedelta(0)


def test_the_60_s_of_the_detection_run_on_the_safety_clock(cfg) -> None:  # Präzisierung 10
    # Home Assistant und die Phasen (ab 60 s) stützen den e-Golf: erkannt 60 s danach
    kw = {"evcs_obs": evcs(charging=True, phase_a=TWO), "homes": {"egolf": True}}
    d = Detector(cfg)
    assert all(detect(d, s, wall_offset=jumping(s), **kw).detected == {} for s in range(120))
    assert detect(d, 120, wall_offset=jumping(120), **kw).detected == {"evcs": "egolf"}


def test_the_60_s_of_the_guest_run_on_the_safety_clock(cfg) -> None:  # Präzisierung 10
    d = Detector(cfg)
    for s in range(60):
        found = detect(d, s, wall_offset=jumping(s), **GUEST_AT_EVCS)
        assert found.guests == frozenset()
    assert detect(d, 60, wall_offset=jumping(60), **GUEST_AT_EVCS).guests == {"evcs"}
