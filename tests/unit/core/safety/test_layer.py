from datetime import timedelta
from pathlib import Path

import pytest
from tests.helpers import T0
from tests.unit.core.safety.conftest import PHASES, make_ctx

from bindaems.core.persistence.files import read_json
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.types import Finding, InputStatus, Outcome
from bindaems.shared.control import ActuatorMode, Command, CommandKind, OperatingState
from bindaems.shared.domain import Alarm, Severity

K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


@pytest.fixture
def layer(limits, tmp_path: Path) -> SafetyLayer:
    return SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0))


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
