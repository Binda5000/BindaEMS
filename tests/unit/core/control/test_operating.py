from dataclasses import FrozenInstanceError, fields
from datetime import timedelta
from typing import Any

import pytest
from tests.helpers import T0

from bindaems.core.control.operating import (
    REASON_CONTROL_OFF,
    REASON_HANDBACK,
    REASON_NOT_FRESH,
    REASON_PHASES,
    REASON_WATCHDOG,
    OperatingInputs,
    OperatingStateMachine,
    Transition,
    gate_ready_except_watchdog,
)
from bindaems.core.safety.modes import REASON_WATCHDOG_MODE
from bindaems.shared.control import OperatingState

S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
O = OperatingState  # noqa: E741


def inputs(**changes: Any) -> OperatingInputs:
    base = {
        "control_requested": True,
        "data_fresh": True,
        "phases_ok": True,
        "watchdog_required": False,
        "watchdog_armed": False,
    }
    return OperatingInputs(**(base | changes))


def in_control() -> OperatingStateMachine:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(S(1), inputs())
    return machine


def test_first_step_leaves_start() -> None:
    machine = OperatingStateMachine()
    assert (machine.state, machine.since) == (O.START, None)
    assert machine.step(T0, inputs(control_requested=False)) == Transition(
        O.START, O.OBSERVE, (REASON_CONTROL_OFF,), T0
    )


def test_control_after_gate() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    assert machine.step(S(1), inputs()) == Transition(O.OBSERVE, O.CONTROL, (), S(1))


def test_gate_reasons_in_order() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(
        S(1),
        inputs(
            data_fresh=False,
            phases_ok=False,
            watchdog_required=True,
            safe_reasons=("Netzausfall (Inselbetrieb).",),
        ),
    )
    assert (machine.state, machine.reasons) == (
        O.OBSERVE,
        ("Netzausfall (Inselbetrieb).", REASON_NOT_FRESH, REASON_PHASES, REASON_WATCHDOG),
    )


def test_degraded_reasons() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    t = machine.step(S(1), inputs(degraded_reasons=("EVCS „evcs“ nicht erreichbar.",)))
    assert (t.state, t.reasons) == (O.DEGRADED, ("EVCS „evcs“ nicht erreichbar.",))


def test_watchdog_loss_degrades() -> None:
    t = in_control().step(S(2), inputs(watchdog_required=True))
    assert (t.state, t.reasons) == (O.DEGRADED, (REASON_WATCHDOG,))


def test_safe_and_recovery_need_fresh_data() -> None:
    machine = in_control()
    assert machine.step(S(2), inputs(safe_reasons=("Netzausfall (Inselbetrieb).",))).state is O.SAFE
    assert machine.step(S(3), inputs(data_fresh=False)) is None
    assert machine.reasons == (REASON_NOT_FRESH,)
    assert machine.step(S(4), inputs()).state is O.CONTROL


def test_phase_mismatch_in_control_goes_safe() -> None:
    t = in_control().step(S(2), inputs(phases_ok=False))
    assert (t.state, t.reasons) == (O.SAFE, (REASON_PHASES,))


@pytest.mark.parametrize("trigger", [{}, {"degraded_reasons": ("x",)}, {"safe_reasons": ("x",)}])
def test_switch_off_goes_to_observe(trigger: dict[str, Any]) -> None:
    machine = in_control()
    machine.step(S(2), inputs(**trigger))
    t = machine.step(S(3), inputs(control_requested=False))
    assert (t.state, t.reasons) == (O.OBSERVE, (REASON_CONTROL_OFF,))


def test_reasons_update_without_transition() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(S(1), inputs(degraded_reasons=("A",)))
    assert machine.step(S(2), inputs(degraded_reasons=("B",))) is None
    assert machine.reasons == ("B",)


# --- Weitere Fälle: Zeilen der Zustandstabelle, die die Tests oben offen lassen -------------------


def test_initial_values() -> None:
    machine = OperatingStateMachine()
    assert (machine.state, machine.reasons, machine.since) == (O.START, (), None)


def test_reason_texts() -> None:
    assert REASON_CONTROL_OFF == "Regelbetrieb ist aus."
    assert REASON_NOT_FRESH == "Kritische Messwerte noch nicht 30 s aktuell."
    assert REASON_PHASES == "3 Phasen mit je einem Gerät nicht bestätigt."
    assert REASON_WATCHDOG == "Watchdog am Cerbo ist nicht scharf."
    assert REASON_WATCHDOG == REASON_WATCHDOG_MODE  # eine Quelle für den Text


def test_dataclass_fields() -> None:
    assert [f.name for f in fields(OperatingInputs)] == [
        "control_requested",
        "data_fresh",
        "phases_ok",
        "watchdog_required",
        "watchdog_armed",
        "safe_reasons",
        "degraded_reasons",
        "handback_pending",
    ]
    assert [f.name for f in fields(Transition)] == ["previous", "state", "reasons", "ts"]


def test_inputs_reasons_default_to_empty() -> None:
    i = OperatingInputs(True, True, True, False, False)
    assert (i.safe_reasons, i.degraded_reasons, i.handback_pending) == ((), (), False)


def test_inputs_and_transition_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        inputs().control_requested = False
    with pytest.raises(FrozenInstanceError):
        Transition(O.START, O.OBSERVE, (), T0).state = O.SAFE


def test_first_step_with_closed_gate_names_the_gate_reasons() -> None:
    t = OperatingStateMachine().step(T0, inputs(data_fresh=False, phases_ok=False))
    assert t == Transition(O.START, O.OBSERVE, (REASON_NOT_FRESH, REASON_PHASES), T0)


def test_first_step_never_goes_straight_to_control() -> None:
    # Auch bei offenem Gate und Gründen für DEGRADED: START führt erst nach OBSERVE, ohne Gründe.
    t = OperatingStateMachine().step(T0, inputs(degraded_reasons=("x",)))
    assert t == Transition(O.START, O.OBSERVE, (), T0)


def test_control_off_hides_the_gate_reasons() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs(control_requested=False, data_fresh=False, safe_reasons=("x",)))
    assert (machine.state, machine.reasons) == (O.OBSERVE, (REASON_CONTROL_OFF,))
    assert machine.step(S(1), inputs(control_requested=False, data_fresh=False)) is None
    assert machine.reasons == (REASON_CONTROL_OFF,)
    # Erst mit Regelbetrieb nennt OBSERVE, was am Gate fehlt.
    assert machine.step(S(2), inputs(data_fresh=False)) is None
    assert machine.reasons == (REASON_NOT_FRESH,)


@pytest.mark.parametrize(
    ("change", "reasons"),
    [
        ({"safe_reasons": ("Netzausfall (Inselbetrieb).",)}, ("Netzausfall (Inselbetrieb).",)),
        ({"data_fresh": False}, (REASON_NOT_FRESH,)),
        ({"phases_ok": False}, (REASON_PHASES,)),
        ({"watchdog_required": True}, (REASON_WATCHDOG,)),
    ],
)
def test_each_gate_reason_keeps_observe(change: dict[str, Any], reasons: tuple[str, ...]) -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    assert machine.step(S(1), inputs(**change)) is None
    assert (machine.state, machine.reasons) == (O.OBSERVE, reasons)


@pytest.mark.parametrize(
    "change",
    [{}, {"watchdog_armed": True}, {"watchdog_required": True, "watchdog_armed": True}],
)
def test_watchdog_closes_the_gate_only_when_required_and_not_armed(change: dict[str, Any]) -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    assert machine.step(S(1), inputs(**change)) == Transition(O.OBSERVE, O.CONTROL, (), S(1))


def test_observe_returns_to_control_after_switching_on_again() -> None:
    machine = in_control()
    machine.step(S(2), inputs(control_requested=False))
    assert machine.step(S(3), inputs()) == Transition(O.OBSERVE, O.CONTROL, (), S(3))


def test_safe_stays_while_safe_reasons_persist() -> None:
    machine = in_control()
    machine.step(S(2), inputs(safe_reasons=("A",)))
    assert machine.step(S(3), inputs(safe_reasons=("A",))) is None
    assert machine.step(S(4), inputs(safe_reasons=("B",))) is None
    assert (machine.state, machine.reasons) == (O.SAFE, ("B",))


def test_safe_recovers_straight_into_degraded() -> None:
    machine = in_control()
    machine.step(S(2), inputs(safe_reasons=("Netzausfall (Inselbetrieb).",)))
    t = machine.step(S(3), inputs(degraded_reasons=("x",)))
    assert t == Transition(O.SAFE, O.DEGRADED, ("x",), S(3))


def test_safe_recovery_needs_an_armed_watchdog() -> None:
    machine = in_control()
    machine.step(S(2), inputs(safe_reasons=("x",)))
    assert machine.step(S(3), inputs(watchdog_required=True)) is None
    assert (machine.state, machine.reasons) == (O.SAFE, (REASON_WATCHDOG,))
    t = machine.step(S(4), inputs(watchdog_required=True, watchdog_armed=True))
    assert t == Transition(O.SAFE, O.CONTROL, (), S(4))


def test_safe_after_phase_mismatch_recovers_when_phases_are_back() -> None:
    machine = in_control()
    machine.step(S(2), inputs(phases_ok=False))
    assert machine.step(S(3), inputs(phases_ok=False)) is None
    assert machine.step(S(4), inputs()) == Transition(O.SAFE, O.CONTROL, (), S(4))


def test_control_without_reasons_has_no_transition() -> None:
    machine = in_control()
    assert machine.step(S(2), inputs()) is None
    assert (machine.state, machine.reasons) == (O.CONTROL, ())


def test_degraded_returns_to_control_when_the_reasons_are_gone() -> None:
    machine = in_control()
    machine.step(S(2), inputs(degraded_reasons=("x",)))
    assert machine.step(S(3), inputs()) == Transition(O.DEGRADED, O.CONTROL, (), S(3))


def test_safe_reasons_come_before_the_phase_reason() -> None:
    t = in_control().step(
        S(2), inputs(safe_reasons=("Netzausfall (Inselbetrieb).",), phases_ok=False)
    )
    assert t == Transition(O.CONTROL, O.SAFE, ("Netzausfall (Inselbetrieb).", REASON_PHASES), S(2))


@pytest.mark.parametrize(
    ("before", "previous"),
    [({}, O.CONTROL), ({"degraded_reasons": ("x",)}, O.DEGRADED)],
)
@pytest.mark.parametrize(
    ("trigger", "reasons"),
    [({"safe_reasons": ("S",)}, ("S",)), ({"phases_ok": False}, (REASON_PHASES,))],
)
def test_control_and_degraded_go_safe(
    before: dict[str, Any],
    previous: OperatingState,
    trigger: dict[str, Any],
    reasons: tuple[str, ...],
) -> None:
    # Die Gründe für DEGRADED, die weiter anliegen, gehören nicht zu den Gründen für SAFE.
    machine = in_control()
    machine.step(S(2), inputs(**before))
    assert machine.state is previous
    assert machine.step(S(3), inputs(**(before | trigger))) == Transition(
        previous, O.SAFE, reasons, S(3)
    )


def test_stale_flag_alone_does_not_leave_control() -> None:
    # In CONTROL melden veraltete Messwerte die safe_reasons; data_fresh gilt nur am Gate.
    machine = in_control()
    assert machine.step(S(2), inputs(data_fresh=False)) is None
    assert (machine.state, machine.reasons) == (O.CONTROL, ())


def test_watchdog_reason_follows_the_degraded_reasons() -> None:
    t = in_control().step(S(2), inputs(degraded_reasons=("x", "y"), watchdog_required=True))
    assert (t.state, t.reasons) == (O.DEGRADED, ("x", "y", REASON_WATCHDOG))


def test_armed_or_unrequested_watchdog_is_no_reason() -> None:
    machine = in_control()
    assert machine.step(S(2), inputs(watchdog_required=True, watchdog_armed=True)) is None
    assert machine.step(S(3), inputs(watchdog_armed=True)) is None
    assert (machine.state, machine.reasons) == (O.CONTROL, ())


def test_control_off_wins_over_safe_reasons_and_phases() -> None:
    t = in_control().step(
        S(2), inputs(control_requested=False, safe_reasons=("x",), phases_ok=False)
    )
    assert t == Transition(O.CONTROL, O.OBSERVE, (REASON_CONTROL_OFF,), S(2))


def test_since_changes_only_with_a_transition() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    assert machine.since == T0
    machine.step(S(1), inputs())
    machine.step(S(2), inputs())
    assert machine.since == S(1)
    machine.step(S(3), inputs(degraded_reasons=("x",)))
    machine.step(S(4), inputs(degraded_reasons=("y",)))
    assert machine.since == S(3)


def test_a_run_through_all_states() -> None:
    machine = OperatingStateMachine()
    island = ("Netzausfall (Inselbetrieb).",)
    armed = {"watchdog_required": True, "watchdog_armed": True}
    run = [
        # (Eingaben, Zustand danach, Gründe danach)
        ({"control_requested": False}, O.OBSERVE, (REASON_CONTROL_OFF,)),
        ({"data_fresh": False}, O.OBSERVE, (REASON_NOT_FRESH,)),
        ({}, O.CONTROL, ()),
        ({"watchdog_required": True}, O.DEGRADED, (REASON_WATCHDOG,)),
        ({"watchdog_required": True, "safe_reasons": island}, O.SAFE, island),
        (armed, O.CONTROL, ()),
        ({"control_requested": False}, O.OBSERVE, (REASON_CONTROL_OFF,)),
    ]
    previous, since = O.START, None
    for second, (change, state, reasons) in enumerate(run):
        changed = state != previous
        expected = Transition(previous, state, reasons, S(second)) if changed else None
        assert machine.step(S(second), inputs(**change)) == expected
        since = S(second) if changed else since
        assert (machine.state, machine.reasons, machine.since) == (state, reasons, since)
        previous = state


# --- Übergabe am Ende des Live-Betriebs (Plan 2b) ----------------------------------------------


def test_control_off_waits_for_handback() -> None:
    machine = in_control()  # vorhandener Helfer: Automat in CONTROL
    machine.step(T0, inputs(control_requested=False, handback_pending=True))
    assert (machine.state, machine.reasons) == (
        OperatingState.DEGRADED,
        ("Regelbetrieb wird beendet: Übergabe der Aktoren läuft.",),
    )
    machine.step(T0, inputs(control_requested=False))
    assert machine.state == OperatingState.OBSERVE


def test_gate_ready_except_watchdog() -> None:
    assert gate_ready_except_watchdog(inputs(watchdog_required=True, watchdog_armed=False))
    assert not gate_ready_except_watchdog(inputs(data_fresh=False))


# Weitere Fälle zur Übergabe und zum Gate ohne Watchdog.

OFF = {"control_requested": False, "handback_pending": True}


def test_handback_reason_text() -> None:
    assert REASON_HANDBACK == "Regelbetrieb wird beendet: Übergabe der Aktoren läuft."


def test_handback_from_degraded_hides_the_degraded_reasons() -> None:
    machine = in_control()
    machine.step(S(2), inputs(degraded_reasons=("x",), watchdog_required=True))
    assert machine.state is O.DEGRADED
    assert (
        machine.step(S(3), inputs(**OFF, degraded_reasons=("x",), watchdog_required=True)) is None
    )
    assert (machine.state, machine.reasons) == (O.DEGRADED, (REASON_HANDBACK,))


@pytest.mark.parametrize(
    ("trigger", "reasons"),
    [
        ({"safe_reasons": ("Netzausfall (Inselbetrieb).",)}, ("Netzausfall (Inselbetrieb).",)),
        ({"phases_ok": False}, (REASON_PHASES,)),
        ({"safe_reasons": ("S",), "phases_ok": False}, ("S", REASON_PHASES)),
    ],
)
@pytest.mark.parametrize("before", [{}, {"degraded_reasons": ("x",)}])
def test_handback_goes_safe_on_safe_reasons(
    before: dict[str, Any], trigger: dict[str, Any], reasons: tuple[str, ...]
) -> None:
    machine = in_control()
    machine.step(S(2), inputs(**before))
    previous = machine.state
    t = machine.step(S(3), inputs(**OFF, **trigger))
    assert t == Transition(previous, O.SAFE, (*reasons, REASON_HANDBACK), S(3))


def test_safe_keeps_waiting_for_the_handback() -> None:
    machine = in_control()
    machine.step(S(2), inputs(safe_reasons=("S",)))
    assert machine.step(S(3), inputs(**OFF, safe_reasons=("S",))) is None
    assert (machine.state, machine.reasons) == (O.SAFE, (REASON_HANDBACK,))
    assert machine.step(S(4), inputs(**OFF)) is None  # auch ohne Gründe für SAFE
    assert (machine.state, machine.reasons) == (O.SAFE, (REASON_HANDBACK,))
    t = machine.step(S(5), inputs(control_requested=False))
    assert t == Transition(O.SAFE, O.OBSERVE, (REASON_CONTROL_OFF,), S(5))


def test_degraded_handback_ends_in_observe_with_the_control_off_reason() -> None:
    machine = in_control()
    machine.step(S(2), inputs(**OFF))
    t = machine.step(S(3), inputs(control_requested=False))
    assert t == Transition(O.DEGRADED, O.OBSERVE, (REASON_CONTROL_OFF,), S(3))


@pytest.mark.parametrize("first", [{}, {"control_requested": False}])
def test_observe_does_not_wait_for_a_handback(first: dict[str, Any]) -> None:
    # In START und OBSERVE schreibt der core nicht und übergibt deshalb nichts.
    machine = OperatingStateMachine()
    machine.step(T0, inputs(**first))
    assert machine.step(S(1), inputs(**OFF)) is None
    assert (machine.state, machine.reasons) == (O.OBSERVE, (REASON_CONTROL_OFF,))


def test_first_step_with_a_pending_handback_goes_to_observe() -> None:
    t = OperatingStateMachine().step(T0, inputs(**OFF))
    assert t == Transition(O.START, O.OBSERVE, (REASON_CONTROL_OFF,), T0)


def test_handback_with_control_on_changes_nothing() -> None:
    # Ein Aktor geht in den Dry-Run, der Regelbetrieb läuft weiter: Zustand wie in Plan 2a.
    machine = in_control()
    assert machine.step(S(2), inputs(handback_pending=True)) is None
    assert (machine.state, machine.reasons) == (O.CONTROL, ())
    t = machine.step(S(3), inputs(handback_pending=True, safe_reasons=("S",)))
    assert t == Transition(O.CONTROL, O.SAFE, ("S",), S(3))
    t = machine.step(S(4), inputs(handback_pending=True))
    assert t == Transition(O.SAFE, O.CONTROL, (), S(4))


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"watchdog_required": True},
        {"watchdog_armed": True},
        {"degraded_reasons": ("x",)},
        {"handback_pending": True},
    ],
)
def test_gate_ready_ignores_watchdog_and_degraded_reasons(change: dict[str, Any]) -> None:
    assert gate_ready_except_watchdog(inputs(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"control_requested": False},
        {"safe_reasons": ("Netzausfall (Inselbetrieb).",)},
        {"data_fresh": False},
        {"phases_ok": False},
    ],
)
def test_gate_not_ready_without_one_of_the_other_conditions(change: dict[str, Any]) -> None:
    assert not gate_ready_except_watchdog(inputs(**change, watchdog_required=True))
