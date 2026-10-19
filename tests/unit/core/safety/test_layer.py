import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from tests.helpers import T0
from tests.unit.core.safety.conftest import PHASES, make_ctx

from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.types import Finding, InputStatus, Outcome
from bindaems.shared.control import ActuatorMode, Command, CommandKind, OperatingState
from bindaems.shared.domain import Alarm, Severity

K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def budget(tmp_path: Path) -> PersistentWriteBudget:
    return PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0)


@pytest.fixture
def layer(limits, tmp_path: Path) -> SafetyLayer:
    return SafetyLayer(limits, budget(tmp_path))


def test_unknown_actuator_rejected(layer) -> None:
    v = layer.authorize(Command("wallbox.twc", K.CURRENT, 10.0), make_ctx())
    assert (v.outcome, v.value, v.findings[0].text) == (
        Outcome.REJECTED,
        None,
        "Unbekannter Aktor „wallbox.twc“.",
    )


def test_unsupported_kind_rejected(layer) -> None:
    v = layer.authorize(Command("victron", K.CURRENT, 10.0), make_ctx())
    assert v.findings[0].text == "„Victron (ESS)“ kennt den Befehl „current“ nicht."


def test_observe_rejects_normal_commands(layer) -> None:
    v = layer.authorize(
        Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(operating=OperatingState.OBSERVE)
    )
    assert (v.outcome, v.findings[-1].text) == (
        Outcome.REJECTED,
        "Regelbetrieb nicht aktiv (Zustand OBSERVE).",
    )


def test_urgent_toward_safe_passes_in_safe_state(layer) -> None:
    ctx = make_ctx(operating=OperatingState.SAFE, present={("wallbox.evcs", K.CURRENT): 16.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 6.0, True)


def test_urgent_toward_safe_rejected_in_observe(layer) -> None:
    ctx = make_ctx(operating=OperatingState.OBSERVE, present={("wallbox.evcs", K.CURRENT): 16.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, v.findings[-1].text) == (
        Outcome.REJECTED,
        "Regelbetrieb nicht aktiv (Zustand OBSERVE).",
    )


def test_urgent_without_safer_value_is_checked_normally(layer) -> None:
    ctx = make_ctx(operating=OperatingState.SAFE, present={("wallbox.evcs", K.CURRENT): 6.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 16.0, urgent=True), ctx)
    assert (v.outcome, [f.code for f in v.findings]) == (
        Outcome.REJECTED,
        ["urgent.ignored", "state.inactive"],
    )


def test_lock_rejects_even_urgent(layer) -> None:
    reason = "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."
    ctx = make_ctx(locks={"wallbox.evcs": (reason,)}, present={("wallbox.evcs", K.CURRENT): 16.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, v.findings[-1].text) == (Outcome.REJECTED, f"Gesperrt: {reason}")


def test_dry_run_evaluates_but_does_not_send(layer) -> None:
    v = layer.authorize(
        Command("wallbox.evcs", K.CURRENT, 10.0),
        make_ctx(modes={"wallbox.evcs": ActuatorMode.DRY_RUN}),
    )
    assert (v.outcome, v.dry_run, v.sendable, v.send) == (Outcome.ACCEPTED, True, True, False)


def test_inputs_not_ok_rejects_increase_allows_urgent_decrease(layer) -> None:
    ctx = make_ctx(
        inputs=InputStatus(False, ("Messwert fehlt oder ist veraltet: grid.l1.power_w.",)),
        present={("wallbox.evcs", K.CURRENT): 10.0},
    )
    up = layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), ctx)
    assert up.findings[-1].text == (
        "Eingangsdaten unplausibel: Messwert fehlt oder ist veraltet: grid.l1.power_w."
    )
    assert (
        layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx).outcome
        is Outcome.ACCEPTED
    )


def test_clamp_then_phase_reduction(layer) -> None:
    ctx = make_ctx(
        phase_import_a={"L1": 30.0, "L2": 20.0, "L3": 10.0},
        wallbox_phase_a={"evcs": {p: 6.0 for p in PHASES}},
        present={("wallbox.evcs", K.CURRENT): 6.0},
    )
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 20.0), ctx)
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.REDUCED,
        9.0,
        ["range.max", "phase.reduced"],
    )


def test_deadband_gives_unchanged(layer) -> None:
    ctx = make_ctx(present={("victron", K.GRID_SETPOINT): 1000.0})
    v = layer.authorize(Command("victron", K.GRID_SETPOINT, 1030.0), ctx)
    assert (v.outcome, v.send) == (Outcome.UNCHANGED, False)


def test_interval_defers_and_urgent_bypasses(layer) -> None:
    def ctx(at: int, present: float):
        return make_ctx(now=S(at), present={("wallbox.evcs", K.CURRENT): present})

    assert (
        layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), ctx(0, 8.0)).outcome
        is Outcome.ACCEPTED
    )
    assert (
        layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), ctx(2, 10.0)).outcome
        is Outcome.DEFERRED
    )
    urgent = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx(3, 10.0))
    assert (urgent.outcome, urgent.findings[-1].code) == (Outcome.ACCEPTED, "urgent.bypass")


def test_budget_rejects_and_raises_alarm(layer) -> None:
    def at(i: int, present: float = 20.0):
        return make_ctx(now=S(i), present={("victron", K.MIN_SOC): present})

    for i in range(12):
        assert layer.authorize(Command("victron", K.MIN_SOC, 30.0), at(i)).send
    rejected = layer.authorize(Command("victron", K.MIN_SOC, 30.0), at(12))
    assert (rejected.outcome, rejected.findings[-1].code) == (Outcome.REJECTED, "budget.exhausted")
    assert [a.id for a in layer.alarms(S(12))] == ["safety.write_budget"]
    assert layer.authorize(Command("victron", K.MIN_SOC, 20.0, urgent=True), at(13, 30.0)).send


def test_dry_run_counts_for_rate_limits(layer) -> None:
    dry = {"wallbox.evcs": ActuatorMode.DRY_RUN}
    layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(modes=dry))
    later = layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), make_ctx(now=S(1), modes=dry))
    assert later.outcome is Outcome.DEFERRED


def test_rejected_and_deferred_are_not_recorded(layer) -> None:
    layer.authorize(
        Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(operating=OperatingState.OBSERVE)
    )
    assert (
        layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx()).outcome
        is Outcome.ACCEPTED
    )


# Weitere Fälle: Pfade und Zusagen von ``authorize``, die die Tests oben nicht berühren
# (Reihenfolge der Schritte, Erfassen, Ausnahmen für dringende Befehle, Wert im Urteil).

EVCS = "wallbox.evcs"
IMP = {"L1": 30.0, "L2": 20.0, "L3": 10.0}
URGENT_IGNORED = Finding(
    "urgent.ignored",
    "Als dringend markiert, aber nicht Richtung sicherer Zustand; es gelten die normalen Regeln.",
)
URGENT_BYPASS = Finding(
    "urgent.bypass", "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
)


def evcs_at(at: float, present: float, **changes):
    return make_ctx(now=S(at), present={(EVCS, K.CURRENT): present}, **changes)


def test_catalog_is_checked_before_everything_else(layer) -> None:
    hostile = make_ctx(operating=OperatingState.OBSERVE, locks={"wallbox.twc": ("x",)})
    unknown = layer.authorize(Command("wallbox.twc", K.CURRENT, 6.0, urgent=True), hostile)
    assert unknown.findings == (Finding("actuator.unknown", "Unbekannter Aktor „wallbox.twc“."),)
    foreign = layer.authorize(Command("vehicle.tesla", K.MIN_SOC, 20.0, urgent=True), hostile)
    assert (foreign.outcome, foreign.value, foreign.findings) == (
        Outcome.REJECTED,
        None,
        (Finding("kind.unsupported", "„Tesla Model 3“ kennt den Befehl „min_soc“ nicht."),),
    )


@pytest.mark.parametrize(
    "state", [OperatingState.START, OperatingState.OBSERVE, OperatingState.SAFE]
)
def test_normal_commands_need_control_or_degraded(layer, state) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(operating=state))
    assert (v.outcome, v.value, v.findings) == (
        Outcome.REJECTED,
        None,
        (Finding("state.inactive", f"Regelbetrieb nicht aktiv (Zustand {state.value})."),),
    )


def test_degraded_allows_normal_commands(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(operating=OperatingState.DEGRADED))
    assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 10.0, True)


def test_urgent_toward_safe_rejected_in_start(layer) -> None:
    ctx = evcs_at(0, 16.0, operating=OperatingState.START)
    v = layer.authorize(Command(EVCS, K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, [f.code for f in v.findings]) == (Outcome.REJECTED, ["state.inactive"])


def test_urgent_text_when_not_toward_safe(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.0, urgent=True), evcs_at(0, 8.0))
    assert (v.outcome, v.findings) == (Outcome.ACCEPTED, (URGENT_IGNORED,))


def test_lock_without_reasons_does_not_lock(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(locks={EVCS: ()}))
    assert v.outcome is Outcome.ACCEPTED


def test_lock_lists_all_reasons(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(locks={EVCS: ("A.", "B.")}))
    assert v.findings == (Finding("lock", "Gesperrt: A.; B."),)


def test_only_the_live_member_sends(layer) -> None:
    missing = layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(modes={}))
    assert (missing.outcome, missing.dry_run, missing.send) == (Outcome.ACCEPTED, True, False)
    plain = layer.authorize(
        Command(EVCS, K.CURRENT, 12.0), make_ctx(now=S(5), modes={EVCS: "live"})
    )
    assert (plain.outcome, plain.dry_run, plain.send) == (Outcome.ACCEPTED, True, False)


def test_dry_run_flag_follows_the_mode_also_when_rejected(layer) -> None:
    observe = make_ctx(operating=OperatingState.OBSERVE)
    assert layer.authorize(Command(EVCS, K.CURRENT, 10.0), observe).dry_run is False
    dry = make_ctx(operating=OperatingState.OBSERVE, modes={EVCS: ActuatorMode.DRY_RUN})
    assert layer.authorize(Command(EVCS, K.CURRENT, 10.0), dry).dry_run is True


def test_inputs_not_ok_reject_also_urgent_commands_not_toward_safe(layer) -> None:
    ctx = evcs_at(0, 10.0, inputs=InputStatus(False, ("A.", "B.")))
    v = layer.authorize(Command(EVCS, K.CURRENT, 12.0, urgent=True), ctx)
    assert v.findings == (URGENT_IGNORED, Finding("inputs", "Eingangsdaten unplausibel: A.; B."))
    decrease = layer.authorize(Command(EVCS, K.CURRENT, 6.0), ctx)
    assert (decrease.outcome, decrease.findings[-1].code) == (Outcome.REJECTED, "inputs")


def test_urgent_with_bad_inputs_passes_in_safe_state(layer) -> None:
    ctx = evcs_at(0, 10.0, operating=OperatingState.SAFE, inputs=InputStatus(False, ("A.",)))
    assert layer.authorize(Command(EVCS, K.CURRENT, 6.0, urgent=True), ctx).send


@pytest.mark.parametrize(
    ("command", "codes"),
    [
        (Command(EVCS, K.CURRENT, 5.0), ["range.min"]),
        (Command(EVCS, K.CURRENT, float("nan")), ["value.invalid"]),
        (Command(EVCS, K.CURRENT, 10**400, urgent=True), ["urgent.ignored", "value.invalid"]),
        (Command(EVCS, K.CURRENT, -(10**400), urgent=True), ["urgent.ignored", "value.invalid"]),
        (Command("victron", K.MIN_SOC, 15.0, urgent=True), ["urgent.ignored", "range.reserve"]),
    ],
)
def test_rejected_by_ranges_carries_no_value(layer, command, codes) -> None:
    v = layer.authorize(command, make_ctx())
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (Outcome.REJECTED, None, codes)


def test_rejected_by_phases_carries_no_value(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 20.0), make_ctx(phase_import_a=IMP))
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.REJECTED,
        None,
        ["range.max", "phase.rejected"],
    )


@pytest.mark.parametrize(
    ("command", "value", "codes"),
    [
        (Command(EVCS, K.CURRENT, 10.7), 10.0, ["range.integer"]),
        (Command("victron", K.GRID_SETPOINT, -300.0), 0.0, ["range.negative"]),
        (Command("victron", K.MAX_DISCHARGE, 15000.0), 12000.0, ["range.max"]),
    ],
)
def test_changed_by_ranges_is_clamped(layer, command, value, codes) -> None:
    v = layer.authorize(command, make_ctx())
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (Outcome.CLAMPED, value, codes)


def test_int_value_unchanged_by_ranges_is_accepted(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10), make_ctx())
    assert (v.outcome, v.value, v.findings) == (Outcome.ACCEPTED, 10.0, ())


@pytest.mark.parametrize(
    ("requested", "outcome"), [(6.0, Outcome.ACCEPTED), (10.0, Outcome.REDUCED)]
)
def test_reduced_only_when_rule_4_changes_the_value(layer, requested, outcome) -> None:
    ctx = make_ctx(
        phase_import_a={"L1": 45.0, "L2": 20.0, "L3": 10.0},
        wallbox_phase_a={"evcs": dict.fromkeys(PHASES, 16.0)},
        present={(EVCS, K.CURRENT): 16.0},
    )
    v = layer.authorize(Command(EVCS, K.CURRENT, requested), ctx)
    text = "Phase L1 würde über 33 A steigen; nur noch Mindeststrom 6 A möglich."
    assert (v.outcome, v.value, v.findings) == (outcome, 6.0, (Finding("phase.minimum", text),))


def test_findings_keep_their_order_and_deferred_keeps_the_value(layer) -> None:
    ctx = make_ctx(
        phase_import_a=IMP,
        wallbox_phase_a={"evcs": dict.fromkeys(PHASES, 6.0)},
        present={(EVCS, K.CURRENT): 6.0},
    )
    command = Command(EVCS, K.CURRENT, 20.0, urgent=True)
    first = layer.authorize(command, ctx)
    assert (first.command, first.outcome, first.value, [f.code for f in first.findings]) == (
        command,
        Outcome.REDUCED,
        9.0,
        ["urgent.ignored", "range.max", "phase.reduced"],
    )
    again = layer.authorize(command, ctx)
    assert (again.outcome, again.value, [f.code for f in again.findings]) == (
        Outcome.DEFERRED,
        9.0,
        ["urgent.ignored", "range.max", "phase.reduced", "rate.interval"],
    )


def test_deadband_sees_the_value_after_the_rules(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 10.4), evcs_at(0, 10.0))
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.UNCHANGED,
        10.0,
        ["range.integer", "rate.unchanged"],
    )


def test_same_value_is_unchanged_even_when_urgent(layer) -> None:
    v = layer.authorize(Command(EVCS, K.CURRENT, 6.0, urgent=True), evcs_at(0, 6.0))
    assert (v.outcome, v.value, v.findings) == (
        Outcome.UNCHANGED,
        6.0,
        (Finding("rate.unchanged", "Wert unverändert."),),
    )


def test_urgent_ignores_the_deadband_below_its_width(layer) -> None:
    ctx = make_ctx(present={("victron", K.GRID_SETPOINT): 1000.0})
    normal = layer.authorize(Command("victron", K.GRID_SETPOINT, 980.0), ctx)
    assert (normal.outcome, [f.code for f in normal.findings]) == (
        Outcome.UNCHANGED,
        ["rate.deadband"],
    )
    urgent = layer.authorize(Command("victron", K.GRID_SETPOINT, 980.0, urgent=True), ctx)
    assert (urgent.outcome, urgent.value, urgent.findings) == (Outcome.ACCEPTED, 980.0, ())


def test_deferred_is_not_recorded(layer) -> None:
    assert (
        layer.authorize(Command(EVCS, K.CURRENT, 10.0), evcs_at(0, 8.0)).outcome is Outcome.ACCEPTED
    )
    assert (
        layer.authorize(Command(EVCS, K.CURRENT, 12.0), evcs_at(2, 10.0)).outcome
        is Outcome.DEFERRED
    )
    assert (
        layer.authorize(Command(EVCS, K.CURRENT, 12.0), evcs_at(5, 10.0)).outcome
        is Outcome.ACCEPTED
    )


def test_unchanged_is_not_recorded(layer) -> None:
    assert (
        layer.authorize(Command(EVCS, K.CURRENT, 10.0), evcs_at(0, 10.0)).outcome
        is Outcome.UNCHANGED
    )
    assert (
        layer.authorize(Command(EVCS, K.CURRENT, 12.0), evcs_at(1, 10.0)).outcome
        is Outcome.ACCEPTED
    )


def test_urgent_bypass_is_recorded(layer) -> None:
    layer.authorize(Command(EVCS, K.CURRENT, 10.0), evcs_at(0, 8.0))
    urgent = layer.authorize(Command(EVCS, K.CURRENT, 6.0, urgent=True), evcs_at(3, 10.0))
    assert (urgent.outcome, urgent.findings) == (Outcome.ACCEPTED, (URGENT_BYPASS,))
    later = layer.authorize(Command(EVCS, K.CURRENT, 8.0), evcs_at(6, 6.0))
    assert (later.outcome, later.findings[-1].code) == (Outcome.DEFERRED, "rate.interval")


def test_budget_exception_needs_urgent_and_the_exact_safe_value(layer, tmp_path: Path) -> None:
    def at(i: int):
        present = {("victron", K.MIN_SOC): 30.0, ("victron", K.MAX_DISCHARGE): 5000.0}
        return make_ctx(now=S(i), present=present)

    for i in range(12):
        assert layer.authorize(Command("victron", K.MIN_SOC, 40.0), at(i)).send
    blocked = [
        Command("victron", K.MIN_SOC, 20.0),  # Sicherwert, aber nicht dringend
        Command("victron", K.MIN_SOC, 25.0, urgent=True),  # Richtung sicher, kein Sicherwert
        Command("victron", K.MAX_DISCHARGE, 8000.0, urgent=True),
    ]
    for command in blocked:
        v = layer.authorize(command, at(12))
        assert (v.outcome, v.value, v.findings[-1].code) == (
            Outcome.REJECTED,
            None,
            "budget.exhausted",
        )
    assert len(read_json(tmp_path / "budget.json")["writes"]) == 12
    for command in (
        Command("victron", K.MAX_DISCHARGE, -1.0, urgent=True),
        Command("victron", K.MIN_SOC, 20.0, urgent=True),
    ):
        v = layer.authorize(command, at(13))
        assert (v.outcome, v.send, v.findings) == (Outcome.ACCEPTED, True, ())
    assert len(read_json(tmp_path / "budget.json")["writes"]) == 14  # mitgezählt


def test_dry_run_uses_the_write_budget(layer) -> None:
    dry = {"victron": ActuatorMode.DRY_RUN}
    for i in range(12):
        v = layer.authorize(Command("victron", K.MIN_SOC, 30.0 + i), make_ctx(now=S(i), modes=dry))
        assert (v.sendable, v.send) == (True, False)
    v = layer.authorize(Command("victron", K.MIN_SOC, 50.0), make_ctx(now=S(12), modes=dry))
    assert v.findings[-1].code == "budget.exhausted"


def test_kinds_compare_by_value(layer, tmp_path: Path) -> None:
    v = layer.authorize(Command("victron", "min_soc", 30.0), make_ctx())  # type: ignore[arg-type]
    assert (v.outcome, v.value) == (Outcome.ACCEPTED, 30.0)
    assert len(read_json(tmp_path / "budget.json")["writes"]) == 1  # persistente Art erkannt


def test_alarms_and_block_come_from_the_budget(limits, tmp_path: Path) -> None:
    fresh = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "frisch.json", T0))
    assert fresh.alarms(T0) == []
    (tmp_path / "budget.json").write_text("kaputt")
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0))
    assert layer.alarms(T0) == [
        Alarm(
            "safety.write_budget_file",
            Severity.WARNING,
            "Datei des Schreibbudgets unlesbar; persistente Einstellungen bleiben 1 h gesperrt.",
            T0,
        )
    ]
    blocked = layer.authorize(Command("victron", K.MIN_SOC, 30.0), make_ctx())
    assert (blocked.outcome, blocked.findings[-1].code) == (Outcome.REJECTED, "budget.exhausted")
    assert layer.authorize(Command("victron", K.GRID_SETPOINT, 500.0), make_ctx()).send


def test_clock_alarm_of_the_budget_comes_through_the_layer(limits, tmp_path: Path) -> None:
    write_json_atomic(tmp_path / "budget.json", {"writes": [S(600).isoformat()]})
    layer = SafetyLayer(limits, budget(tmp_path))
    assert [(a.id, a.since) for a in layer.alarms(T0)] == [("safety.clock", T0)]
    assert layer.alarms(S(300)) == []


def test_urgent_bypass_keeps_the_checked_value(layer) -> None:
    layer.authorize(Command(EVCS, K.CURRENT, 10.0), evcs_at(0, 8.0))
    urgent = layer.authorize(Command(EVCS, K.CURRENT, 8.7, urgent=True), evcs_at(1, 10.0))
    assert (urgent.outcome, urgent.value, [f.code for f in urgent.findings]) == (
        Outcome.CLAMPED,
        8.0,
        ["range.integer", "urgent.bypass"],
    )


def test_volatile_kinds_do_not_use_the_write_budget(layer, tmp_path: Path) -> None:
    for i in range(13):
        v = layer.authorize(
            Command("victron", K.GRID_SETPOINT, 100.0 + 100 * i), make_ctx(now=S(2 * i))
        )
        assert v.send
    assert not (tmp_path / "budget.json").exists()
    assert layer.authorize(Command("victron", K.MIN_SOC, 30.0), make_ctx(now=S(30))).send


def test_start_without_headroom_is_rejected(layer) -> None:
    present = {(EVCS, K.CURRENT): 10.0, (EVCS, K.CHARGING): False}
    v = layer.authorize(
        Command(EVCS, K.CHARGING, True), make_ctx(phase_import_a=IMP, present=present)
    )
    assert (v.outcome, v.value, v.findings[-1].text) == (
        Outcome.REJECTED,
        None,
        "Phase L1 würde über 33 A steigen.",
    )


def test_reduction_to_the_present_value_is_unchanged(layer) -> None:
    ctx = make_ctx(
        phase_import_a=IMP,
        wallbox_phase_a={"evcs": dict.fromkeys(PHASES, 6.0)},
        present={(EVCS, K.CURRENT): 9.0},
    )
    v = layer.authorize(Command(EVCS, K.CURRENT, 20.0), ctx)
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.UNCHANGED,
        9.0,
        ["range.max", "phase.reduced", "rate.unchanged"],
    )


def test_budget_exception_needs_the_safe_value_of_the_same_kind(layer) -> None:
    for i in range(12):
        layer.authorize(Command("victron", K.MIN_SOC, 30.0 + i), make_ctx(now=S(i)))
    present = {("victron", K.MAX_DISCHARGE): 10.0}
    v = layer.authorize(  # 20 ist der Sicherwert des Min-SOC, nicht der Entladegrenze
        Command("victron", K.MAX_DISCHARGE, 20.0, urgent=True), make_ctx(now=S(12), present=present)
    )
    assert (v.outcome, v.findings[-1].code) == (Outcome.REJECTED, "budget.exhausted")


def test_urgent_setpoint_clamped_away_from_safe_is_not_urgent(layer) -> None:
    # −300 W ginge Richtung sicher; die Regeln 1–3 machen daraus 0 W, 200 W mehr Bezug als −200 W.
    ctx = make_ctx(
        operating=OperatingState.SAFE,
        inputs=InputStatus(False, ("A.",)),
        base_grid_setpoint_w=-200.0,
        present={("victron", K.GRID_SETPOINT): None},
    )
    v = layer.authorize(Command("victron", K.GRID_SETPOINT, -300.0, urgent=True), ctx)
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.REJECTED,
        None,
        ["urgent.ignored", "state.inactive"],
    )


def test_urgent_discharge_limit_clamped_to_a_tightening_is_not_urgent(layer) -> None:
    # 15 000 W lockert die Grenze von 14 000 W; auf 12 000 W geklemmt verschärft sie aber.
    command = Command("victron", K.MAX_DISCHARGE, 15000.0, urgent=True)
    changes = {"battery_discharge_w": 13500.0, "present": {("victron", K.MAX_DISCHARGE): 14000.0}}
    in_safe = layer.authorize(command, make_ctx(operating=OperatingState.SAFE, **changes))
    assert (in_safe.outcome, [f.code for f in in_safe.findings]) == (
        Outcome.REJECTED,
        ["urgent.ignored", "state.inactive"],
    )
    bad_inputs = layer.authorize(command, make_ctx(inputs=InputStatus(False, ("A.",)), **changes))
    assert (bad_inputs.outcome, [f.code for f in bad_inputs.findings]) == (
        Outcome.REJECTED,
        ["urgent.ignored", "inputs"],
    )
    normal = layer.authorize(command, make_ctx(**changes))  # nur nach den normalen Regeln
    assert (normal.outcome, normal.value, [f.code for f in normal.findings]) == (
        Outcome.CLAMPED,
        12000.0,
        ["urgent.ignored", "range.max"],
    )


@pytest.mark.parametrize(
    ("command", "present"),
    [
        # unter dem Mindeststrom
        (Command(EVCS, K.CURRENT, 5.0, urgent=True), {(EVCS, K.CURRENT): 16.0}),
        # nicht endlich; abgelehnt hat der Sollwert keinen Wert, wie ein aufgehobener Override
        (
            Command("victron", K.GRID_SETPOINT, float("-inf"), urgent=True),
            {("victron", K.GRID_SETPOINT): None},
        ),
    ],
)
def test_urgent_value_rejected_by_ranges_is_not_urgent(layer, command, present) -> None:
    v = layer.authorize(command, make_ctx(operating=OperatingState.SAFE, present=present))
    assert (v.outcome, [f.code for f in v.findings]) == (
        Outcome.REJECTED,
        ["urgent.ignored", "state.inactive"],
    )


# Rate-Limits des Tesla überstehen einen Neustart (rate_limits.json)

TESLA = "vehicle.tesla"
RATE_FILE_UNREADABLE = "Datei der Rate-Limits unlesbar"
RATE_FILE_UNWRITTEN = "Datei der Rate-Limits nicht geschrieben"


def test_tesla_hour_window_survives_restart(tmp_path, limits_live) -> None:
    rate_file = tmp_path / "rate_limits.json"
    layer = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=T0)
    for i in range(6):  # start_stop_per_hour = 6
        moment = T0 + timedelta(seconds=100 * i)
        command = Command("vehicle.tesla", K.CHARGING, i % 2 == 0)
        assert layer.authorize(command, make_ctx(now=moment)).outcome == Outcome.ACCEPTED
    later = T0 + timedelta(seconds=700)
    fresh = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=later)
    verdict = fresh.authorize(Command("vehicle.tesla", K.CHARGING, True), make_ctx(now=later))
    assert verdict.outcome == Outcome.DEFERRED
    assert verdict.findings[-1].code == "rate.start_stop"


def test_unreadable_rate_file_starts_empty(tmp_path, limits_live) -> None:
    rate_file = tmp_path / "rate_limits.json"
    rate_file.write_text("{", encoding="utf-8")
    layer = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=T0)
    command = Command("vehicle.tesla", K.CHARGING, True)
    assert layer.authorize(command, make_ctx()).outcome == Outcome.ACCEPTED


def rate_entry(at: float, **changes) -> dict:
    """Ein Eintrag von ``rate_limits.json``: ein Start/Stopp des Tesla ``at`` Sekunden nach T0."""
    return {"actuator": TESLA, "kind": "charging", "ts": S(at).isoformat()} | changes


def rate_doc(*entries, **changes) -> dict:
    return {"version": 1, "entries": list(entries)} | changes


def restarted(limits, tmp_path: Path, at: float) -> SafetyLayer:
    return SafetyLayer(limits, budget(tmp_path), rate_file=tmp_path / "rate_limits.json", now=S(at))


def start(layer: SafetyLayer, at: float, on: bool = True, **changes):
    return layer.authorize(Command(TESLA, K.CHARGING, on), make_ctx(now=S(at), **changes))


def test_rate_file_lists_the_hourly_entries_in_time_order(tmp_path, limits_live) -> None:
    layer = restarted(limits_live, tmp_path, 0)
    assert start(layer, 0).send
    current = {(TESLA, K.CURRENT): 6.0}
    assert layer.authorize(
        Command(TESLA, K.CURRENT, 10.0), make_ctx(now=S(100), present=current)
    ).send
    assert start(layer, 200, on=False).send
    assert read_json(tmp_path / "rate_limits.json") == rate_doc(
        rate_entry(0), rate_entry(100, kind="current"), rate_entry(200)
    )


def test_only_commands_with_hourly_limits_write_the_rate_file(tmp_path, limits_live) -> None:
    layer = restarted(limits_live, tmp_path, 0)
    assert layer.authorize(Command(EVCS, K.CURRENT, 10.0), make_ctx(now=S(0))).send
    assert layer.authorize(Command("victron", K.GRID_SETPOINT, 500.0), make_ctx(now=S(1))).send
    assert not (tmp_path / "rate_limits.json").exists()
    assert start(layer, 2).send
    assert read_json(tmp_path / "rate_limits.json") == rate_doc(rate_entry(2))


def test_commands_that_are_not_recorded_leave_the_rate_file_alone(tmp_path, limits_live) -> None:
    layer = restarted(limits_live, tmp_path, 0)
    for i in range(6):
        assert start(layer, 100 * i, on=i % 2 == 0).send
    rate_file = tmp_path / "rate_limits.json"
    before = rate_file.read_bytes()
    deferred = start(layer, 700)
    rejected = start(layer, 701, operating=OperatingState.OBSERVE)
    assert (deferred.outcome, rejected.outcome) == (Outcome.DEFERRED, Outcome.REJECTED)
    assert rate_file.read_bytes() == before


def test_dry_run_commands_are_written_to_the_rate_file_too(tmp_path, limits_live) -> None:
    layer = restarted(limits_live, tmp_path, 0)
    verdict = start(layer, 0, modes={TESLA: ActuatorMode.DRY_RUN})
    assert (verdict.outcome, verdict.send) == (Outcome.ACCEPTED, False)
    assert read_json(tmp_path / "rate_limits.json") == rate_doc(rate_entry(0))


def test_without_a_rate_file_nothing_is_written(layer, tmp_path: Path) -> None:
    for i in range(3):
        assert start(layer, 10 * i, on=i % 2 == 0).send
    assert list(tmp_path.iterdir()) == []


def test_tesla_current_interval_survives_restart(tmp_path, limits_live) -> None:
    first = restarted(limits_live, tmp_path, 0)
    present = {(TESLA, K.CURRENT): 6.0}
    assert first.authorize(
        Command(TESLA, K.CURRENT, 10.0), make_ctx(now=S(0), present=present)
    ).send
    fresh = restarted(limits_live, tmp_path, 30)
    present = {(TESLA, K.CURRENT): 10.0}
    early = fresh.authorize(Command(TESLA, K.CURRENT, 14.0), make_ctx(now=S(30), present=present))
    assert (early.outcome, early.findings[-1].text) == (
        Outcome.DEFERRED,
        "Mindestabstand 90 s noch nicht erreicht.",
    )
    on_time = fresh.authorize(Command(TESLA, K.CURRENT, 14.0), make_ctx(now=S(90), present=present))
    assert on_time.outcome == Outcome.ACCEPTED


def test_missing_rate_file_is_a_silent_empty_start(tmp_path, limits_live) -> None:
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 0)
    assert logs == []
    assert not (tmp_path / "rate_limits.json").exists()  # das Laden schreibt nichts
    assert start(layer, 0).outcome == Outcome.ACCEPTED


def test_unreadable_rate_file_is_logged_and_healed_by_the_next_entry(tmp_path, limits_live) -> None:
    rate_file = tmp_path / "rate_limits.json"
    rate_file.write_text("{", encoding="utf-8")
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 0)
    assert [(e["log_level"], e["event"]) for e in logs] == [("warning", RATE_FILE_UNREADABLE)]
    assert rate_file.read_text(encoding="utf-8") == "{"  # das Laden schreibt nichts
    assert start(layer, 0).send
    assert read_json(rate_file) == rate_doc(rate_entry(0))


def test_a_rate_path_that_is_no_file_starts_empty_and_is_logged(tmp_path, limits_live) -> None:
    (tmp_path / "rate_limits.json").mkdir()
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 0)
    assert [e["event"] for e in logs] == [RATE_FILE_UNREADABLE]
    assert start(layer, 0).outcome == Outcome.ACCEPTED


# sechs Start/Stopp in den letzten 10 Minuten: das Limit pro Stunde ist erreicht
SIX = [rate_entry(100 * i) for i in range(6)]

BAD_RATE_FILES = [
    pytest.param(b"", id="leer"),
    pytest.param(b"\xff\xfe\x00", id="keine-utf8-kodierung"),
    pytest.param(b"[]", id="liste-statt-objekt"),
    pytest.param(b"null", id="null"),
    pytest.param(json.dumps({"entries": SIX}).encode(), id="version-fehlt"),
    pytest.param(json.dumps(rate_doc(*SIX, version=2)).encode(), id="andere-version"),
    pytest.param(json.dumps({"version": 1}).encode(), id="entries-fehlt"),
    pytest.param(json.dumps(rate_doc(*SIX, extra=1)).encode(), id="fremder-schluessel"),
    pytest.param(json.dumps({"version": 1, "entries": {}}).encode(), id="entries-objekt"),
    pytest.param(json.dumps(rate_doc(*SIX, 5)).encode(), id="eintrag-zahl"),
    pytest.param(
        json.dumps(rate_doc(*SIX, {"actuator": TESLA, "kind": "charging"})).encode(),
        id="ts-fehlt",
    ),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, extra=1))).encode(), id="eintrag-extra"),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, ts="gestern"))).encode(), id="ts-text"),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, ts=5))).encode(), id="ts-zahl"),
    pytest.param(
        json.dumps(rate_doc(*SIX, rate_entry(0, ts="2026-10-08T10:00:00"))).encode(),
        id="ts-ohne-zeitzone",
    ),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, kind="bogus"))).encode(), id="art-fremd"),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, kind=5))).encode(), id="art-zahl"),
    pytest.param(json.dumps(rate_doc(*SIX, rate_entry(0, actuator=5))).encode(), id="aktor-zahl"),
]


@pytest.mark.parametrize("content", BAD_RATE_FILES)
def test_invalid_rate_file_is_unreadable_as_a_whole(tmp_path, limits_live, content: bytes) -> None:
    # Die sechs gültigen Einträge würden den nächsten Start sperren; ein schlechter Eintrag in der
    # Datei macht sie unlesbar: ganz oder gar nicht.
    (tmp_path / "rate_limits.json").write_bytes(content)
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 700)
    assert [(e["log_level"], e["event"]) for e in logs] == [("warning", RATE_FILE_UNREADABLE)]
    assert start(layer, 700).outcome == Outcome.ACCEPTED


def test_rate_file_with_all_entries_valid_blocks_the_next_start(tmp_path, limits_live) -> None:
    write_json_atomic(tmp_path / "rate_limits.json", rate_doc(*SIX))
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 700)
    assert logs == []
    assert start(layer, 700).outcome == Outcome.DEFERRED


@pytest.mark.parametrize(
    ("age_s", "outcome"),
    [(3599, Outcome.DEFERRED), (3600, Outcome.ACCEPTED)],
    ids=["younger", "hour"],
)
def test_entries_count_after_loading_while_younger_than_an_hour(
    tmp_path, limits_live, age_s, outcome
) -> None:
    write_json_atomic(tmp_path / "rate_limits.json", rate_doc(*[rate_entry(0)] * 6))
    layer = restarted(limits_live, tmp_path, age_s)
    assert start(layer, age_s).outcome == outcome


def test_entries_of_actuators_without_hourly_limits_in_the_file_are_ignored(
    tmp_path, limits_live
) -> None:
    entries = [
        rate_entry(699, actuator=EVCS, kind="current"),  # fürs EVCS gilt ein Mindestabstand von 5 s
        rate_entry(699, actuator="vehicle.gone"),
    ]
    write_json_atomic(tmp_path / "rate_limits.json", rate_doc(*entries))
    with capture_logs() as logs:
        layer = restarted(limits_live, tmp_path, 700)
    assert logs == []
    ctx = make_ctx(now=S(700), present={(EVCS, K.CURRENT): 8.0})
    assert layer.authorize(Command(EVCS, K.CURRENT, 12.0), ctx).outcome == Outcome.ACCEPTED


@pytest.mark.parametrize("content", [rate_doc(*SIX), b"{"], ids=["valid", "broken"])
def test_rate_file_is_loaded_only_together_with_now(tmp_path, limits_live, content) -> None:
    rate_file = tmp_path / "rate_limits.json"
    if isinstance(content, bytes):
        rate_file.write_bytes(content)
    else:
        write_json_atomic(rate_file, content)
    with capture_logs() as logs:
        layer = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file)
    assert logs == []  # ohne ``now`` wird nicht gelesen, also gibt es auch nichts zu melden
    assert start(layer, 700).outcome == Outcome.ACCEPTED
    assert read_json(rate_file) == rate_doc(rate_entry(700))  # geschrieben wird trotzdem


def test_failing_rate_file_write_is_logged_and_the_window_holds(
    tmp_path, limits_live, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll"))
    )
    layer = restarted(limits_live, tmp_path, 0)
    with capture_logs() as logs:
        for i in range(6):
            assert start(layer, 100 * i, on=i % 2 == 0).outcome == Outcome.ACCEPTED
        deferred = start(layer, 700)
    assert [(e["log_level"], e["event"]) for e in logs] == [("error", RATE_FILE_UNWRITTEN)] * 6
    assert (deferred.outcome, deferred.findings[-1].code) == (Outcome.DEFERRED, "rate.start_stop")
    assert list(tmp_path.iterdir()) == []


# Auffrischen eines Overrides (Plan 2c-2a, Präzisierung 7)

SETPOINT = ("victron", K.GRID_SETPOINT)


def test_refresh_sends_the_same_override(layer) -> None:
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    verdict = layer.authorize(refresh, make_ctx(present={SETPOINT: 2000.0}))
    assert (verdict.outcome, verdict.value, verdict.send) == (Outcome.ACCEPTED, 2000.0, True)
    plain = Command("victron", K.GRID_SETPOINT, 2000.0)
    unchanged = layer.authorize(plain, make_ctx(now=S(5), present={SETPOINT: 2000.0}))
    assert unchanged.outcome is Outcome.UNCHANGED


def test_refresh_passes_inside_the_deadband(layer) -> None:
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    verdict = layer.authorize(refresh, make_ctx(present={SETPOINT: 1990.0}))
    assert (verdict.outcome, verdict.value, verdict.findings) == (Outcome.ACCEPTED, 2000.0, ())
    plain = Command("victron", K.GRID_SETPOINT, 2000.0)
    inside = layer.authorize(plain, make_ctx(now=S(5), present={SETPOINT: 1990.0}))
    assert (inside.outcome, [f.code for f in inside.findings]) == (
        Outcome.UNCHANGED,
        ["rate.deadband"],
    )


def test_refresh_keeps_interval_lock_and_state(layer) -> None:
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    present = {SETPOINT: 2000.0}
    assert layer.authorize(refresh, make_ctx(present=present)).outcome is Outcome.ACCEPTED
    early = layer.authorize(refresh, make_ctx(now=S(1), present=present))
    assert (early.outcome, early.value, early.findings[-1].code) == (
        Outcome.DEFERRED,
        2000.0,
        "rate.interval",
    )
    locked = make_ctx(now=S(2), locks={"victron": ("DESS",)}, present=present)
    verdict = layer.authorize(refresh, locked)
    assert (verdict.outcome, verdict.findings[-1].code) == (Outcome.REJECTED, "lock")
    safe = make_ctx(now=S(2), operating=OperatingState.SAFE, present=present)
    verdict = layer.authorize(refresh, safe)
    assert (verdict.outcome, verdict.findings[-1].code) == (Outcome.REJECTED, "state.inactive")
    dry = layer.authorize(refresh, make_ctx(now=S(2), modes={}, present=present))
    assert (dry.outcome, dry.dry_run, dry.send) == (Outcome.ACCEPTED, True, False)


def test_refresh_is_still_clamped_by_the_budget(layer) -> None:
    # Regel 2 senkt den Override von Zyklus zu Zyklus; hält der Cerbo schon den geklemmten Wert,
    # wäre ein Befehl ohne ``refresh`` ``UNCHANGED``.
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    ctx = make_ctx(grid_budget_w=1500.0, present={SETPOINT: 1500.0})
    verdict = layer.authorize(refresh, ctx)
    assert (verdict.outcome, verdict.value, [f.code for f in verdict.findings]) == (
        Outcome.CLAMPED,
        1500.0,
        ["range.ceiling"],
    )
    assert verdict.send


def test_refresh_is_still_reduced_by_the_phases(layer) -> None:
    # Regel 4 lässt keinen Anstieg zu: Der Wert fällt auf den, den der Cerbo schon hält.
    refresh = Command("victron", K.GRID_SETPOINT, 1700.0, refresh=True)
    ctx = make_ctx(phase_import_a={"L1": 33.0, "L2": 20.0, "L3": 10.0}, present={SETPOINT: 1680.0})
    verdict = layer.authorize(refresh, ctx)
    assert (verdict.outcome, verdict.value, [f.code for f in verdict.findings]) == (
        Outcome.REDUCED,
        1680.0,
        ["phase.reduced"],
    )
    assert verdict.send


def test_refresh_is_never_urgent(layer) -> None:
    command = Command("victron", K.GRID_SETPOINT, 2000.0, urgent=True, refresh=True)
    verdict = layer.authorize(command, make_ctx(present={SETPOINT: 2000.0}))
    assert (verdict.outcome, verdict.findings[0].code) == (Outcome.ACCEPTED, "urgent.ignored")
    early = layer.authorize(command, make_ctx(now=S(1), present={SETPOINT: 2000.0}))
    assert (early.outcome, [f.code for f in early.findings]) == (
        Outcome.DEFERRED,
        ["urgent.ignored", "rate.interval"],
    )
    # auch in SAFE nicht, obwohl der Wert dem des Cerbo gleicht, also Richtung sicher geht
    safe = make_ctx(now=S(5), operating=OperatingState.SAFE, present={SETPOINT: 2000.0})
    in_safe = layer.authorize(command, safe)
    assert (in_safe.outcome, [f.code for f in in_safe.findings]) == (
        Outcome.REJECTED,
        ["urgent.ignored", "state.inactive"],
    )


def test_refresh_does_nothing_for_other_kinds(layer) -> None:
    command = Command("wallbox.evcs", K.CURRENT, 10.0, refresh=True)
    ctx = make_ctx(present={("wallbox.evcs", K.CURRENT): 10.0})
    assert layer.authorize(command, ctx).outcome is Outcome.UNCHANGED
    inside = make_ctx(now=S(5), present={("wallbox.evcs", K.CURRENT): 10.4})
    assert layer.authorize(command, inside).outcome is Outcome.UNCHANGED


@pytest.mark.parametrize("refresh", [False, True], ids=["plain", "refresh"])
def test_refresh_does_not_touch_urgent_commands_of_other_kinds(layer, refresh) -> None:
    # ``refresh`` wirkt nur beim Netz-Sollwert: Ein dringender Strom Richtung sicher geht mit und
    # ohne das Merkmal gleich durch, den Mindestabstand umgeht er, und in SAFE ist er erlaubt.
    layer.authorize(Command(EVCS, K.CURRENT, 10.0), evcs_at(0, 8.0))
    urgent = Command(EVCS, K.CURRENT, 6.0, urgent=True, refresh=refresh)
    bypass = layer.authorize(urgent, evcs_at(3, 10.0))
    assert (bypass.outcome, bypass.value, bypass.findings) == (
        Outcome.ACCEPTED,
        6.0,
        (URGENT_BYPASS,),
    )
    in_safe = layer.authorize(urgent, evcs_at(60, 16.0, operating=OperatingState.SAFE))
    assert (in_safe.outcome, in_safe.value, in_safe.findings, in_safe.send) == (
        Outcome.ACCEPTED,
        6.0,
        (),
        True,
    )


# Ladelimit des Tesla (Plan 4b, Präzisierung 9)


def test_charge_limit_only_for_the_tesla(layer) -> None:
    assert layer.authorize(Command("vehicle.tesla", K.CHARGE_LIMIT, 80.0), make_ctx()).sendable
    for actuator, label in (("wallbox.evcs", "EVCS „evcs“"), ("victron", "Victron (ESS)")):
        verdict = layer.authorize(Command(actuator, K.CHARGE_LIMIT, 80.0), make_ctx())
        assert (verdict.outcome, verdict.findings[-1].text) == (
            Outcome.REJECTED,
            f"„{label}“ kennt den Befehl „charge_limit“ nicht.",
        )


def test_charge_limit_is_never_urgent(layer) -> None:
    command = Command("vehicle.tesla", K.CHARGE_LIMIT, 50.0, urgent=True)
    present = {("vehicle.tesla", K.CHARGE_LIMIT): 90.0}
    verdict = layer.authorize(command, make_ctx(operating=OperatingState.SAFE, present=present))
    assert verdict.outcome is Outcome.REJECTED
    assert [f.code for f in verdict.findings] == ["urgent.ignored", "state.inactive"]


def test_charge_limit_passes_full_phases_unchanged(layer) -> None:
    full = make_ctx(phase_import_a=dict.fromkeys(PHASES, 40.0))
    verdict = layer.authorize(Command("vehicle.tesla", K.CHARGE_LIMIT, 100.0), full)
    assert (verdict.outcome, verdict.value, verdict.findings) == (Outcome.ACCEPTED, 100.0, ())


def test_charge_limit_does_not_use_the_write_budget(layer, tmp_path: Path) -> None:
    for i in range(13):  # mehr als das Schreibbudget von 12 je Stunde
        command = Command("vehicle.tesla", K.CHARGE_LIMIT, 50.0 + i)
        assert layer.authorize(command, make_ctx(now=S(600 * i))).sendable


# --- Sollwertversatz der Klimaanlage (Plan 6a, Entscheidung 8) -----------------------------------

KLIMA = "thermal.klima"
OFFSET_STEP = Finding("offset.step", "Versatz nur eine Stufe je 10 min weg von 0.")
OFFSET_SIGN = Finding("offset.step", "Vorzeichenwechsel nur über 0.")


def offset(value, **changes) -> Command:
    return Command(KLIMA, K.SETPOINT_OFFSET, value, **changes)


def klima_at(at: float, echo: float | None = None, **changes):
    """Kontext zum Zeitpunkt ``at`` s; ``echo`` ist der zurückgelesene Versatz, ``None``: keiner."""
    present = {} if echo is None else {(KLIMA, K.SETPOINT_OFFSET): echo}
    return make_ctx(now=S(at), present=present, **changes)


def outcome(verdict):
    return (verdict.outcome, verdict.value, verdict.findings)


def test_offset_steps_one_stage_per_interval_live(layer) -> None:
    assert outcome(layer.authorize(offset(0.5), klima_at(0, echo=0.0))) == (
        Outcome.ACCEPTED,
        0.5,
        (),
    )
    assert outcome(layer.authorize(offset(1.0), klima_at(300, echo=0.5))) == (
        Outcome.REJECTED,
        None,
        (OFFSET_STEP,),
    )
    v = layer.authorize(offset(1.0), klima_at(600, echo=0.5))
    assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 1.0, True)


def test_offset_two_stages_at_once_rejected(layer) -> None:
    v = layer.authorize(offset(1.0), klima_at(0, echo=0.0))
    assert outcome(v) == (Outcome.REJECTED, None, (OFFSET_STEP,))


def test_offset_without_echo_counts_zero_as_present(layer) -> None:
    assert layer.authorize(offset(-0.5), klima_at(0)).outcome is Outcome.ACCEPTED
    assert layer.authorize(offset(-1.5), klima_at(0)).findings == (OFFSET_STEP,)


def test_offset_sign_change_rejected(layer) -> None:
    v = layer.authorize(offset(-0.5), klima_at(10_000, echo=1.0))
    assert outcome(v) == (Outcome.REJECTED, None, (OFFSET_SIGN,))


def test_offset_back_to_zero_and_decreases_go_at_once(layer) -> None:
    assert layer.authorize(offset(0.5), klima_at(0, echo=0.0)).sendable
    assert layer.authorize(offset(0.0), klima_at(1, echo=3.0)).outcome is Outcome.ACCEPTED
    assert layer.authorize(offset(-1.0), klima_at(2, echo=-3.0)).outcome is Outcome.ACCEPTED


def test_offset_rounded_and_clamped_before_the_step_rule(layer) -> None:
    v = layer.authorize(offset(3.7), klima_at(0, echo=2.5))
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.CLAMPED,
        3.0,
        ["range.clamped"],
    )
    # der Wert nach den Regeln 1–3 entspricht dem Echo: nichts zu senden
    v = layer.authorize(offset(3.7), klima_at(1, echo=3.0))
    assert (v.outcome, v.findings[-1].code) == (Outcome.UNCHANGED, "rate.unchanged")
    v = layer.authorize(offset(-0.3), klima_at(600, echo=0.0))
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (
        Outcome.CLAMPED,
        -0.5,
        ["range.step"],
    )


def test_offset_rejected_by_the_step_rule_is_not_recorded(layer) -> None:
    assert layer.authorize(offset(0.5), klima_at(0, echo=0.0)).sendable
    assert layer.authorize(offset(1.0), klima_at(100, echo=0.5)).outcome is Outcome.REJECTED
    # die Ablehnung zählt nicht als Erhöhung: 600 s nach der ersten geht die nächste Stufe
    assert layer.authorize(offset(1.0), klima_at(600, echo=0.5)).sendable


def test_dry_run_checks_against_last_decided(layer) -> None:
    dry = {"modes": {KLIMA: ActuatorMode.DRY_RUN}}
    v = layer.authorize(offset(0.5), klima_at(0, echo=0.0, **dry))
    assert (v.outcome, v.dry_run, v.send) == (Outcome.ACCEPTED, True, False)
    # das Echo bleibt im Dry-Run 0; 1,0 K ist eine Stufe über dem zuletzt entschiedenen Wert
    assert layer.authorize(offset(1.0), klima_at(600, echo=0.0, **dry)).outcome is Outcome.ACCEPTED
    v = layer.authorize(offset(2.0), klima_at(1200, echo=0.0, **dry))
    assert outcome(v) == (Outcome.REJECTED, None, (OFFSET_STEP,))
    assert layer.authorize(offset(1.5), klima_at(1200, echo=0.0, **dry)).outcome is Outcome.ACCEPTED


def test_dry_run_without_a_mode_entry_uses_the_last_decided_value(layer) -> None:
    ctx = {"modes": {}}
    assert layer.authorize(offset(0.5), klima_at(0, echo=0.0, **ctx)).dry_run
    assert layer.authorize(offset(1.0), klima_at(600, echo=0.0, **ctx)).sendable


def test_dry_run_zero_counts_as_decided(layer) -> None:
    dry = {"modes": {KLIMA: ActuatorMode.DRY_RUN}}
    assert layer.authorize(offset(0.5), klima_at(0, echo=0.0, **dry)).sendable
    assert layer.authorize(offset(1.0), klima_at(600, echo=0.0, **dry)).sendable
    # zurück auf 0: auch gleich dem Echo kein „unverändert“ (Ruling R26); entschieden ist 0
    v = layer.authorize(offset(0.0), klima_at(700, echo=0.0, **dry))
    assert (v.outcome, v.dry_run) == (Outcome.ACCEPTED, True)
    v = layer.authorize(offset(1.0), klima_at(1300, echo=0.0, **dry))
    assert outcome(v) == (Outcome.REJECTED, None, (OFFSET_STEP,))
    assert layer.authorize(offset(0.5), klima_at(1300, echo=0.0, **dry)).sendable


def test_layer_accepts_zero_in_safe_state(layer, limits) -> None:
    (safe,) = limits.safe_commands(KLIMA)
    for echo in (2.0, -3.0, None):
        v = layer.authorize(safe, klima_at(0, echo=echo, operating=OperatingState.SAFE))
        assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 0.0, True), echo
    # dringend und dem Betrag nach kleiner mit gleichem Vorzeichen: auch Richtung sicher
    ctx = klima_at(1, echo=2.0, operating=OperatingState.SAFE)
    assert layer.authorize(offset(1.0, urgent=True), ctx).outcome is Outcome.ACCEPTED


def test_urgent_offset_away_from_zero_is_not_urgent(layer) -> None:
    ctx = klima_at(0, echo=0.0, operating=OperatingState.SAFE)
    v = layer.authorize(offset(0.5, urgent=True), ctx)
    assert (v.outcome, v.findings[0], v.findings[-1].code) == (
        Outcome.REJECTED,
        URGENT_IGNORED,
        "state.inactive",
    )


def test_urgent_offset_that_rounds_away_from_safe_is_not_urgent(layer) -> None:
    # 1,8 K liegt unter dem Echo 2,0 K, gerundet aber 2,0 K: kein Schritt Richtung sicher mehr
    ctx = klima_at(0, echo=1.9, operating=OperatingState.SAFE)
    v = layer.authorize(offset(1.8, urgent=True), ctx)
    assert (v.outcome, v.findings[0]) == (Outcome.REJECTED, URGENT_IGNORED)


def test_offset_has_no_interval_or_budget(layer) -> None:
    for at in range(5):
        assert layer.authorize(offset(0.0), klima_at(at, echo=0.5)).outcome is Outcome.ACCEPTED


def test_zero_offset_is_never_unchanged(layer) -> None:
    # Abschlussreview I2, Ruling R26: Das Echo zeigt 0, der Adapter kann aber noch einen alten
    # Versatz halten (Pakete zur UVR gehen verloren). 0 geht deshalb immer hinaus, auch gleich
    # dem Echo; ein Wert außer 0 gleich dem Echo bleibt „unverändert“.
    v = layer.authorize(offset(0.0), klima_at(0, echo=0.0))
    assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 0.0, True)
    v = layer.authorize(offset(0.0), klima_at(1, echo=0.04))
    assert (v.outcome, v.send) == (Outcome.ACCEPTED, True)
    assert layer.authorize(offset(0.5), klima_at(2, echo=0.0)).sendable
    assert layer.authorize(offset(0.5), klima_at(3, echo=0.5)).outcome is Outcome.UNCHANGED
