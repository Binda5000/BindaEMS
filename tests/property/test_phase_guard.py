"""Eigenschaften des Phasenwächters (Design 5): Verteilung, Last je Phase, Minimum und Überlast.

hypothesis erzeugt Lasten, Ansprüche und Verläufe. Die Orakel rechnen aus der Definition noch
einmal, nicht schrittweise wie der Code:

- ``allocate``: Kein Anspruch bekommt mehr, als sein Ziel auf seinen Phasen lässt, und keiner hängt
  von den Ansprüchen hinter ihm ab.
- ``phase_loads``: Eine Phase ist voll genau dann, wenn eine ihrer Größen fehlt.
- ``SlidingMin``: der einfache Durchlauf über alle Werte im Fenster.
- ``OverloadGuard``: die Stufe aus dem ganzen Verlauf. Die ununterbrochenen Läufe werden von hinten
  gezählt, nicht mit den drei Zeitpunkten, die der Wächter sich merkt.
"""

import math
from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import T0

from bindaems.core.control.phase_guard import (
    ALL_STOP_AFTER_S,
    CLEAR_AFTER_S,
    EVCS_STOP_AFTER_S,
    MIN_AFTER_S,
    PHASES,
    Claim,
    Grant,
    GuardLimits,
    OverloadGuard,
    OverloadLevel,
    SlidingMin,
    allocate,
    phase_loads,
)
from bindaems.shared.config import Phase

LIMITS = GuardLimits(
    import_target_a=19.0, fuse_target_a=33.0, fuse_a=35.0, reserve_guard_soc_pct=25.0
)

Currents = dict[Phase, float | None]


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- Verteilung -----------------------------------------------------------------------------------

LOADS = st.fixed_dictionaries({phase: st.none() | st.floats(-50.0, 60.0) for phase in PHASES})
PHASE_SETS = st.sets(st.sampled_from(PHASES), min_size=1).map(
    lambda chosen: tuple(phase for phase in PHASES if phase in chosen)
)
AMPERES = st.floats(0.0, 32.0)
CLAIMS = st.lists(
    st.tuples(PHASE_SETS, st.sampled_from([19.0, 33.0]), AMPERES, AMPERES), max_size=5
).map(
    lambda parts: [  # die Schlüssel sind eindeutig, weil sie aus der Stelle entstehen
        Claim(f"car{index}", phases, target, wanted, holding_a=holding)
        for index, (phases, target, wanted, holding) in enumerate(parts)
    ]
)


@given(loads=LOADS, claims=CLAIMS)
def test_no_grant_exceeds_its_target(loads, claims) -> None:
    grants = allocate(loads, claims)
    used = dict.fromkeys(PHASES, 0.0)
    for claim in claims:
        grant = grants[claim.key]
        assert grant.granted_a >= 0
        if grant.granted_a > 0:
            for p in claim.phases:
                assert loads[p] is not None
                assert loads[p] + used[p] + grant.granted_a <= claim.target_a + 1e-9
        for p in claim.phases:
            used[p] += max(grant.granted_a, claim.holding_a)


@given(loads=LOADS, claims=CLAIMS)
def test_unknown_load_grants_nothing_and_a_known_one_reports_its_room(loads, claims) -> None:
    grants = allocate(loads, claims)
    assert list(grants) == [claim.key for claim in claims]
    for claim in claims:
        grant = grants[claim.key]
        assert 0 <= grant.granted_a <= int(claim.wanted_a)
        if any(loads[p] is None for p in claim.phases):
            assert grant == Grant(None, 0)
        else:
            assert grant.free_a is not None
            assert grant.granted_a <= max(math.floor(grant.free_a), 0)


@given(loads=LOADS, claims=CLAIMS)
def test_a_claim_does_not_depend_on_the_claims_behind_it(loads, claims) -> None:
    grants = allocate(loads, claims)
    for count in range(1, len(claims) + 1):
        assert allocate(loads, claims[:count]) == dict(list(grants.items())[:count])


# --- Last je Phase --------------------------------------------------------------------------------

CURRENTS = st.dictionaries(st.sampled_from(PHASES), AMPERES)  # Strom auf einem Teil der Phasen
LOADPOINTS = st.tuples(PHASE_SETS, st.none() | CURRENTS)


@given(
    grid=LOADS,
    battery=LOADS,
    regulated=st.lists(LOADPOINTS, max_size=3),
    reserved=CURRENTS,
)
def test_a_phase_is_full_exactly_when_one_of_its_inputs_is_unknown(
    grid, battery, regulated, reserved
) -> None:
    loads = phase_loads(grid, battery, regulated, reserved)
    assert list(loads) == list(PHASES)
    for phase in PHASES:
        unknown = (
            grid[phase] is None
            or battery[phase] is None
            or any(currents is None and phase in phases for phases, currents in regulated)
        )
        if unknown:
            assert loads[phase] is None
            continue
        drawn = sum(c.get(phase, 0.0) for _, c in regulated if c is not None)
        expected = grid[phase] + battery[phase] - drawn + reserved.get(phase, 0.0)
        assert loads[phase] == pytest.approx(expected, abs=1e-9)


# --- Gleitendes Minimum ---------------------------------------------------------------------------


@given(
    window_s=st.floats(0.0, 120.0),
    steps=st.lists(st.tuples(st.integers(0, 40), st.floats(-100.0, 100.0)), max_size=30),
)
def test_sliding_min_is_what_a_plain_scan_finds(window_s, steps) -> None:
    def scan(now: int) -> float | None:
        return min((v for s, v in history if now - s <= window_s), default=None)

    window = SlidingMin(window_s)
    history: list[tuple[int, float]] = []
    second = 0
    for gap, value in steps:
        second += gap
        window.add(at(second), value)
        history.append((second, value))
        assert window.value(at(second)) == scan(second)
    for later in range(second, second + 200, 13):  # auch lange nach dem letzten Wert
        assert window.value(at(later)) == scan(later)


# --- Überlast -------------------------------------------------------------------------------------

SCENES: list[Currents] = [
    {"L1": 10.0, "L2": 12.0, "L3": 8.0},  # ruhig
    {"L1": 33.0, "L2": 5.0, "L3": 5.0},  # genau am Sicherungsziel: ruhig
    {"L1": 34.0, "L2": 5.0, "L3": 5.0},  # über dem Sicherungsziel
    {"L1": 5.0, "L2": 35.0, "L3": 5.0},  # genau an der Sicherung: über dem Ziel, nicht darüber
    {"L1": 5.0, "L2": 5.0, "L3": 36.0},  # über der Sicherung
    {"L1": None, "L2": 5.0, "L3": 5.0},  # eine Phase unbekannt
    {"L1": None, "L2": 36.0, "L3": 5.0},  # eine unbekannt, eine andere über der Sicherung
]
# Eine Szene hält mehrere Werte lang, damit Läufe von 3, 6, 10 und 60 s zustande kommen.
SEGMENTS = st.lists(
    st.tuples(st.sampled_from(SCENES), st.integers(1, 12), st.sampled_from([1, 2, 3, 5, 10, 30])),
    min_size=1,
    max_size=10,
)


def over_target(values: Currents) -> bool:
    return any(a is not None and a > LIMITS.fuse_target_a for a in values.values())


def over_fuse(values: Currents) -> bool:
    return any(a is not None and a > LIMITS.fuse_a for a in values.values())


def calm(values: Currents) -> bool:
    return all((a := values[p]) is not None and a <= LIMITS.fuse_target_a for p in PHASES)


def uninterrupted_s(seconds: list[int], holds: list[bool], index: int) -> int:
    """Wie lange die Bedingung bis einschließlich ``index`` ohne Unterbrechung gilt, sonst 0."""
    if not holds[index]:
        return 0
    first = index
    while first > 0 and holds[first - 1]:
        first -= 1
    return seconds[index] - seconds[first]


def expected_levels(samples: list[tuple[int, Currents]]) -> list[OverloadLevel]:
    """Die Stufe nach jedem Wert: 60 s Ruhe hebt sie auf, sonst bleibt die höchste erreichte."""
    seconds = [second for second, _ in samples]
    over_target_flags = [over_target(values) for _, values in samples]
    over_fuse_flags = [over_fuse(values) for _, values in samples]
    calm_flags = [calm(values) for _, values in samples]
    held, levels = OverloadLevel.NONE, []
    for index in range(len(samples)):
        over_s = uninterrupted_s(seconds, over_target_flags, index)
        if uninterrupted_s(seconds, calm_flags, index) >= CLEAR_AFTER_S:
            held = OverloadLevel.NONE
        elif uninterrupted_s(seconds, over_fuse_flags, index) >= ALL_STOP_AFTER_S:
            held = OverloadLevel.ALL_STOP
        elif over_s >= EVCS_STOP_AFTER_S:
            held = max(held, OverloadLevel.EVCS_STOP)
        elif over_s >= MIN_AFTER_S:
            held = max(held, OverloadLevel.MINIMUM)
        levels.append(held)
    return levels


@settings(max_examples=300, deadline=None)
@given(SEGMENTS)
def test_levels_follow_the_uninterrupted_runs(segments) -> None:
    samples: list[tuple[int, Currents]] = []
    second = 0
    for scene, repeats, gap in segments:
        for _ in range(repeats):
            samples.append((second, scene))
            second += gap
    guard = OverloadGuard(LIMITS)
    levels = [guard.update(at(second), values) for second, values in samples]
    assert levels == expected_levels(samples)
