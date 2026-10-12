import math
from datetime import datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.control.phase_guard import (
    ALL_STOP_AFTER_S,
    CLEAR_AFTER_S,
    EVCS_STOP_AFTER_S,
    MIN_AFTER_S,
    PHASES,
    Claim,
    Grant,
    GuardCause,
    GuardLimits,
    OverloadGuard,
    OverloadLevel,
    SlidingMin,
    allocate,
    phase_loads,
    phase_target,
)
from bindaems.shared.config import Config, Phase

LIMITS = GuardLimits(
    import_target_a=19.0, fuse_target_a=33.0, fuse_a=35.0, reserve_guard_soc_pct=25.0
)
ALL = ("L1", "L2", "L3")
N, M, E, A = (
    OverloadLevel.NONE,
    OverloadLevel.MINIMUM,
    OverloadLevel.EVCS_STOP,
    OverloadLevel.ALL_STOP,
)

Currents = dict[Phase, float | None]


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def amps(l1: float | None, l2: float | None = 0.0, l3: float | None = 0.0) -> Currents:
    """Netzströme der drei Phasen; ohne Angabe 0 A."""
    return {"L1": l1, "L2": l2, "L3": l3}


CALM = amps(30.0, 10.0, 10.0)  # unter dem Sicherungsziel (33 A)
OVER = amps(34.0, 10.0, 10.0)  # über dem Sicherungsziel, unter der Sicherung (35 A)
OVER_FUSE = amps(36.0)  # über der Sicherung
UNKNOWN = [amps(None), amps(None, None, None), {}]  # eine Phase, alle Phasen, gar keine Eingabe


def run(guard: OverloadGuard, steps: list[tuple[float, Currents]]) -> list[OverloadLevel]:
    """Die Stufe nach jedem Wert; ``steps`` sind Sekunden seit ``T0`` und die Netzströme."""
    return [guard.update(at(second), values) for second, values in steps]


# --- Grenzen und Ziele ----------------------------------------------------------------------------


def test_limits_from_the_example_config(cfg) -> None:
    assert GuardLimits.from_config(cfg) == LIMITS


def test_limits_follow_the_configured_margins(cfg: Config) -> None:
    changed = cfg.model_copy(
        update={
            "grid": cfg.grid.model_copy(
                update={"fuse_a": 40.0, "fuse_margin_a": 3.0, "import_limit_a": 25.0}
            ),
            "battery": cfg.battery.model_copy(update={"reserve_soc_pct": 15.0}),
            "control": cfg.control.model_copy(
                update={"import_margin_a": 2.5, "reserve_guard_pct": 6.0}
            ),
        }
    )
    assert GuardLimits.from_config(changed) == GuardLimits(
        import_target_a=22.5, fuse_target_a=37.0, fuse_a=40.0, reserve_guard_soc_pct=21.0
    )


@pytest.mark.parametrize(
    ("full", "soc", "expected"),
    [
        (False, 80.0, (19.0, GuardCause.IMPORT_LIMIT)),
        (False, 10.0, (19.0, GuardCause.IMPORT_LIMIT)),
        (False, None, (19.0, GuardCause.IMPORT_LIMIT)),
        (True, 80.0, (33.0, GuardCause.PHASE_LIMIT)),
        (True, 25.0, (33.0, GuardCause.PHASE_LIMIT)),
        (True, 24.9, (19.0, GuardCause.RESERVE_GUARD)),
        (True, None, (19.0, GuardCause.RESERVE_GUARD)),
    ],
)
def test_target_per_mode(full, soc, expected) -> None:
    assert phase_target(LIMITS, full_power=full, soc_pct=soc) == expected


def test_causes_are_the_reason_codes() -> None:
    assert [str(cause) for cause in GuardCause] == ["import_limit", "reserve_guard", "phase_limit"]


# --- Last je Phase --------------------------------------------------------------------------------


def test_load_adds_battery_help_and_removes_regulated_wallboxes() -> None:
    loads = phase_loads(
        {"L1": 2.0, "L2": 18.0, "L3": -3.0},
        {"L1": 10.0, "L2": 0.0, "L3": 4.0},
        [(("L1", "L2"), {"L1": 6.0, "L2": 6.0, "L3": 0.0})],
        {"L3": 6.0},
    )
    assert loads == {"L1": 6.0, "L2": 12.0, "L3": 7.0}


def test_unknown_values_make_the_phase_full() -> None:
    loads = phase_loads(
        {"L1": 2.0, "L2": None, "L3": 1.0},
        {"L1": None, "L2": 0.0, "L3": 0.0},
        [(("L3",), None)],
    )
    assert loads == {"L1": None, "L2": None, "L3": None}


def test_unknown_currents_of_a_loadpoint_fill_only_its_own_phases() -> None:
    zero = dict.fromkeys(PHASES, 0.0)
    loads = phase_loads({"L1": 1.0, "L2": 2.0, "L3": 3.0}, zero, [(("L1", "L2"), None)])
    assert loads == {"L1": None, "L2": None, "L3": 3.0}


def test_missing_grid_or_battery_entry_is_unknown() -> None:
    loads = phase_loads({"L1": 1.0, "L2": 2.0}, {"L1": 0.0, "L3": 0.0}, [])
    assert loads == {"L1": 1.0, "L2": None, "L3": None}


def test_regulated_loadpoints_add_up_on_a_shared_phase() -> None:
    loads = phase_loads(
        dict.fromkeys(PHASES, 20.0),
        dict.fromkeys(PHASES, 0.0),
        [(("L1",), {"L1": 6.0}), (ALL, {"L1": 8.0, "L2": 8.0, "L3": 8.0})],
    )
    assert loads == {"L1": 6.0, "L2": 12.0, "L3": 12.0}


def test_currents_count_on_every_phase_they_have_a_value_for() -> None:
    loads = phase_loads(
        dict.fromkeys(PHASES, 10.0),
        dict.fromkeys(PHASES, 0.0),
        [(("L1",), {"L1": 6.0, "L2": 2.0})],
    )
    assert loads == {"L1": 4.0, "L2": 8.0, "L3": 10.0}


def test_reserved_allowances_add_without_regulated_loadpoints() -> None:
    loads = phase_loads(
        dict.fromkeys(PHASES, 1.0), dict.fromkeys(PHASES, 2.0), [], {"L1": 6.0, "L3": 16.0}
    )
    assert loads == {"L1": 9.0, "L2": 3.0, "L3": 19.0}


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_value_counts_as_unknown(bad: float) -> None:
    loads = phase_loads(
        {"L1": bad, "L2": 0.0, "L3": 0.0},
        {"L1": 0.0, "L2": bad, "L3": 0.0},
        [(("L1",), {"L3": bad})],
    )
    assert loads == {"L1": None, "L2": None, "L3": None}


# --- Verteilung -----------------------------------------------------------------------------------


def test_allocation_follows_priority_and_holds_measured_current() -> None:
    grants = allocate(
        {"L1": 10.0, "L2": 4.0, "L3": 4.0},
        [
            Claim("tesla", ALL, 33.0, 16.0, holding_a=12.0),
            Claim("egolf", ("L1", "L2"), 19.0, 16.0),
        ],
    )
    assert grants == {"tesla": Grant(23.0, 16), "egolf": Grant(-7.0, 0)}


def test_lower_priority_gets_what_is_left() -> None:
    grants = allocate(
        {"L1": 2.0, "L2": 2.0, "L3": 2.0},
        [
            Claim("tesla", ALL, 19.0, 10.0),
            Claim("egolf", ("L1", "L2"), 19.0, 16.0),
        ],
    )
    assert grants["egolf"] == Grant(7.0, 7)
    assert list(grants) == ["tesla", "egolf"]


def test_unknown_load_grants_nothing() -> None:
    grants = allocate(
        {"L1": None, "L2": 2.0, "L3": 2.0}, [Claim("egolf", ("L1", "L2"), 19.0, 16.0)]
    )
    assert grants == {"egolf": Grant(None, 0)}


@pytest.mark.parametrize(
    ("wanted", "granted"),
    [(20.9, 16), (16.0, 16), (10.9, 10), (0.9, 0), (0.0, 0)],
)
def test_grant_is_whole_amperes_below_both_the_wish_and_the_room(wanted, granted) -> None:
    # Platz: 19 − 2,5 = 16,5 A, gerundet nach unten 16 A; der Wunsch wird abgeschnitten
    grants = allocate({"L1": 2.5, "L2": 0.0, "L3": 0.0}, [Claim("car", ("L1",), 19.0, wanted)])
    assert grants == {"car": Grant(16.5, granted)}


def test_holding_above_the_grant_stays_reserved() -> None:
    grants = allocate(
        dict.fromkeys(PHASES, 0.0),
        [
            Claim("tesla", ALL, 19.0, 8.0, holding_a=12.0),
            Claim("egolf", ("L1",), 19.0, 16.0),
        ],
    )
    assert grants == {"tesla": Grant(19.0, 8), "egolf": Grant(7.0, 7)}


def test_unknown_load_still_reserves_the_holding_current() -> None:
    grants = allocate(
        {"L1": None, "L2": 2.0, "L3": 2.0},
        [
            Claim("a", ("L1", "L2"), 19.0, 16.0, holding_a=10.0),
            Claim("b", ("L2", "L3"), 19.0, 16.0),
        ],
    )
    assert grants == {"a": Grant(None, 0), "b": Grant(7.0, 7)}


def test_a_phase_missing_from_the_loads_is_unknown() -> None:
    grants = allocate({"L1": 1.0}, [Claim("car", ("L1", "L2"), 19.0, 5.0)])
    assert grants == {"car": Grant(None, 0)}


def test_a_non_finite_load_is_unknown() -> None:
    claims = [Claim("car", ("L1", "L2"), 19.0, 5.0)]
    for bad in (math.nan, math.inf, -math.inf):
        assert allocate({"L1": bad, "L2": 0.0, "L3": 0.0}, claims) == {"car": Grant(None, 0)}


def test_a_claim_without_phases_gets_nothing() -> None:
    grants = allocate(dict.fromkeys(PHASES, 0.0), [Claim("car", (), 19.0, 5.0)])
    assert grants == {"car": Grant(None, 0)}


def test_no_claims_no_grants() -> None:
    assert allocate(dict.fromkeys(PHASES, 0.0), []) == {}


# --- Überlast -------------------------------------------------------------------------------------


def test_overload_escalates_and_holds_for_60_s() -> None:
    guard = OverloadGuard(LIMITS)
    over = {"L1": 34.0, "L2": 10.0, "L3": 10.0}
    levels = [guard.update(T0 + timedelta(seconds=s), over) for s in range(8)]
    assert levels == [N, N, N, M, M, M, E, E]
    calm = {"L1": 30.0, "L2": 10.0, "L3": 10.0}
    assert guard.update(T0 + timedelta(seconds=8), calm) == E
    assert guard.update(T0 + timedelta(seconds=67), calm) == E
    assert guard.update(T0 + timedelta(seconds=68), calm) == N


def test_ten_seconds_over_the_fuse_stops_everything() -> None:
    guard = OverloadGuard(LIMITS)
    over = {"L1": 36.0, "L2": 0.0, "L3": 0.0}
    assert [guard.update(T0 + timedelta(seconds=s), over) for s in (0, 9, 10)] == [
        OverloadLevel.NONE,
        OverloadLevel.EVCS_STOP,
        OverloadLevel.ALL_STOP,
    ]


def test_unknown_phase_neither_escalates_nor_clears() -> None:
    guard = OverloadGuard(LIMITS)
    for s in range(4):
        guard.update(T0 + timedelta(seconds=s), {"L1": 34.0, "L2": 0.0, "L3": 0.0})
    unknown = {"L1": None, "L2": 0.0, "L3": 0.0}
    assert guard.update(T0 + timedelta(seconds=100), unknown) == OverloadLevel.MINIMUM


def test_overload_times_are_the_design_values() -> None:
    assert (MIN_AFTER_S, EVCS_STOP_AFTER_S, ALL_STOP_AFTER_S, CLEAR_AFTER_S) == (
        3.0,
        6.0,
        10.0,
        60.0,
    )
    assert [int(level) for level in OverloadLevel] == [0, 1, 2, 3]
    assert N < M < E < A


def test_a_phase_exactly_at_the_target_is_not_over_and_counts_as_calm() -> None:
    guard = OverloadGuard(LIMITS)
    at_target = amps(33.0)
    steps = [(0, OVER), (6, OVER), (7, at_target), (66, at_target), (67, at_target)]
    assert run(guard, steps) == [N, E, E, E, N]


def test_a_phase_exactly_at_the_fuse_is_not_over_the_fuse() -> None:
    guard = OverloadGuard(LIMITS)
    at_fuse = amps(35.0)
    assert run(guard, [(s, at_fuse) for s in (0, 3, 6, 10, 600)]) == [N, M, E, E, E]


def test_all_stop_holds_until_60_s_calm() -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER_FUSE), (10, OVER_FUSE), (11, CALM), (70, CALM), (71, CALM)]
    assert run(guard, steps) == [N, A, A, A, N]


def test_short_excursions_do_not_add_up() -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER), (2, OVER), (3, CALM), (4, OVER), (6, OVER), (7, OVER)]
    assert run(guard, steps) == [N, N, N, N, N, M]


def test_the_fuse_timer_restarts_when_the_phase_falls_back_below_the_fuse() -> None:
    guard = OverloadGuard(LIMITS)
    between = amps(34.0)  # über dem Ziel, unter der Sicherung: der Lauf zum Ziel geht weiter
    steps = [
        (0, OVER_FUSE),
        (8, OVER_FUSE),
        (9, between),
        (10, OVER_FUSE),
        (19, OVER_FUSE),
        (20, OVER_FUSE),
    ]
    assert run(guard, steps) == [N, E, E, E, E, A]


def test_another_phase_over_keeps_the_run_going() -> None:
    guard = OverloadGuard(LIMITS)
    handover = {"L1": 10.0, "L2": 34.0, "L3": 0.0}  # nun liegt L2 darüber, vorher L1
    assert run(guard, [(0, OVER), (3, handover)]) == [N, M]


@pytest.mark.parametrize("unknown", UNKNOWN, ids=["one phase", "all phases", "no input"])
def test_unknown_values_interrupt_the_over_limit_run(unknown: Currents) -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER), (2, OVER), (3, unknown), (4, OVER), (6, OVER), (7, OVER)]
    assert run(guard, steps) == [N, N, N, N, N, M]


def test_an_unknown_phase_does_not_hide_another_phase_that_is_over() -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER), (3, {"L1": None, "L2": 34.0, "L3": 0.0})]
    assert run(guard, steps) == [N, M]


@pytest.mark.parametrize("unknown", UNKNOWN, ids=["one phase", "all phases", "no input"])
def test_unknown_values_restart_the_calm_timer(unknown: Currents) -> None:
    guard = OverloadGuard(LIMITS)
    steps = [
        (0, OVER),
        (3, OVER),
        (10, CALM),
        (40, unknown),
        (41, CALM),
        (70, CALM),
        (100, CALM),
        (101, CALM),
    ]
    assert run(guard, steps) == [N, M, M, M, M, M, M, N]


def test_an_excursion_restarts_the_calm_timer() -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER), (3, OVER), (4, CALM), (40, OVER), (41, CALM), (100, CALM), (101, CALM)]
    assert run(guard, steps) == [N, M, M, M, M, M, N]


def test_after_clearing_a_new_overload_starts_from_the_beginning() -> None:
    guard = OverloadGuard(LIMITS)
    steps = [(0, OVER), (6, OVER), (7, CALM), (67, CALM), (68, OVER), (70, OVER), (71, OVER)]
    assert run(guard, steps) == [N, E, E, N, N, N, M]
    assert run(guard, [(74, OVER)]) == [E]


def test_a_phase_missing_from_the_input_counts_as_unknown() -> None:
    guard = OverloadGuard(LIMITS)
    partial: Currents = {"L1": 10.0, "L2": 10.0}  # L3 fehlt
    assert run(guard, [(0, OVER), (3, OVER), (10, partial), (200, partial)]) == [N, M, M, M]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_current_counts_as_unknown(bad: float) -> None:
    guard = OverloadGuard(LIMITS)
    assert run(guard, [(0, amps(bad)), (1000, amps(bad))]) == [N, N]  # weder darüber
    steps = [(1001, OVER), (1004, OVER), (1100, amps(bad)), (1200, amps(bad))]
    assert run(guard, steps) == [N, M, M, M]  # noch darunter


# --- Gleitendes Minimum ---------------------------------------------------------------------------


def test_sliding_min_keeps_the_lowest_of_the_window() -> None:
    window = SlidingMin(60.0)
    for s, value in ((0, 12.0), (10, 8.0), (30, 14.0)):
        window.add(T0 + timedelta(seconds=s), value)
    assert window.value(T0 + timedelta(seconds=70)) == 8.0
    assert window.value(T0 + timedelta(seconds=71)) == 14.0


def test_sliding_min_without_values_is_unknown() -> None:
    assert SlidingMin(60.0).value(T0) is None


def test_sliding_min_forgets_everything_after_the_window() -> None:
    window = SlidingMin(60.0)
    window.add(T0, 5.0)
    assert window.value(at(60)) == 5.0
    assert window.value(at(61)) is None


def test_sliding_min_passes_on_to_the_next_value() -> None:
    window = SlidingMin(60.0)
    window.add(at(0), 10.0)
    window.add(at(10), 20.0)
    assert [window.value(at(s)) for s in (10, 60, 61, 70, 71)] == [10.0, 10.0, 20.0, 20.0, None]


def test_sliding_min_reading_does_not_consume_the_window() -> None:
    window = SlidingMin(60.0)
    window.add(at(0), 5.0)
    window.add(at(30), 9.0)
    assert window.value(at(61)) == 9.0
    assert window.value(at(60)) == 5.0
