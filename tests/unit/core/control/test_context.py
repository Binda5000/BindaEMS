"""Aufbau des ``SafetyContext`` und der Eingangsgrößen einer Entscheidung aus dem Snapshot."""

import math

import pytest
from tests.helpers import T0, snap

from bindaems.core.control.context import decision_inputs, safety_context
from bindaems.core.safety.types import InputStatus, SafetyContext
from bindaems.shared.control import ActuatorMode, CommandKind, OperatingState
from bindaems.shared.domain import Quality, Snapshot, Value

PHASE_IMPORT = {"L1": 1.0, "L2": 2.0, "L3": 3.0}


def build(s: Snapshot) -> SafetyContext:
    """``safety_context`` mit leeren Sperren, im Regelbetrieb und 1–3 A Netzbezug je Phase."""
    return safety_context(
        now=T0,
        operating=OperatingState.CONTROL,
        modes={},
        locks={},
        inputs=InputStatus(True),
        snap=s,
        phase_import_a=PHASE_IMPORT,
        wallbox_phase_a={},
        present={},
    )


# Vorgabe des Plans


def test_context_reads_battery_and_setpoint() -> None:
    s = snap({"battery.power_w": -1500.0, "battery.soc_pct": 46.0, "ess.grid_setpoint_w": 50.0})
    ctx = build(s)  # Helfer: safety_context mit leeren Sperren, CONTROL, phase_import_a 1–3 A
    assert (ctx.battery_discharge_w, ctx.soc_pct, ctx.base_grid_setpoint_w) == (1500.0, 46.0, 50.0)
    assert ctx.vehicle_wallbox == {} and ctx.grid_budget_w is None


def test_missing_setpoint_is_unknown() -> None:
    assert build(snap({})).base_grid_setpoint_w is None


def test_vehicle_wallbox_reaches_the_context() -> None:
    ctx = safety_context(
        now=T0,
        operating=OperatingState.CONTROL,
        modes={},
        locks={},
        inputs=InputStatus(True),
        snap=snap({}),
        phase_import_a=PHASE_IMPORT,
        wallbox_phase_a={},
        present={},
        vehicle_wallbox={"vehicle.tesla": "twc"},
    )
    assert ctx.vehicle_wallbox == {"vehicle.tesla": "twc"}


def test_decision_inputs() -> None:
    s = snap({"battery.power_w": -1500.0, "battery.soc_pct": 46.0})
    assert decision_inputs(s, {"L1": 1.5, "L2": None, "L3": -0.5}) == {
        "soc_pct": 46.0,
        "battery_w": -1500.0,
        "grid_l1_a": 1.5,
        "grid_l2_a": None,
        "grid_l3_a": -0.5,
    }


# Weitere Fälle


def test_a_charging_battery_discharges_nothing() -> None:
    assert build(snap({"battery.power_w": 600.0})).battery_discharge_w == 0.0


def test_unknown_battery_values_stay_unknown() -> None:
    ctx = build(snap({}))
    assert (ctx.battery_discharge_w, ctx.soc_pct) == (None, None)


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, True, "50", None],
    ids=["nan", "inf", "-inf", "bool", "text", "none"],
)
def test_a_setpoint_that_is_no_finite_number_is_unknown(value: Value) -> None:
    ctx = build(snap({"ess.grid_setpoint_w": value, "battery.power_w": value}))
    assert (ctx.base_grid_setpoint_w, ctx.battery_discharge_w) == (None, None)


def test_stale_values_are_unknown() -> None:
    values = {"battery.power_w": -1500.0, "battery.soc_pct": 46.0, "ess.grid_setpoint_w": 50.0}
    stale = snap(values, quality=Quality.STALE)
    ctx = build(stale)
    assert (ctx.battery_discharge_w, ctx.soc_pct, ctx.base_grid_setpoint_w) == (None, None, None)
    assert decision_inputs(stale, {})["soc_pct"] is None


def test_the_rest_is_passed_through_unchanged() -> None:
    modes = {"wallbox.evcs": ActuatorMode.LIVE}
    locks = {"victron": ("Dynamic ESS ist aktiv (Modus 1).",)}
    inputs = InputStatus(False, ("Messwert fehlt oder ist veraltet: battery.power_w.",))
    wallbox_phase_a = {"evcs": {"L1": 6.0, "L2": 6.0, "L3": 6.0}}
    present = {("wallbox.evcs", CommandKind.CURRENT): 16.0}
    ctx = safety_context(
        now=T0,
        operating=OperatingState.SAFE,
        modes=modes,
        locks=locks,
        inputs=inputs,
        snap=snap({}),
        phase_import_a=PHASE_IMPORT,
        wallbox_phase_a=wallbox_phase_a,
        present=present,
    )
    assert (ctx.now, ctx.operating, ctx.modes, ctx.locks, ctx.inputs) == (
        T0,
        OperatingState.SAFE,
        modes,
        locks,
        inputs,
    )
    assert (ctx.phase_import_a, ctx.wallbox_phase_a, ctx.present) == (
        PHASE_IMPORT,
        wallbox_phase_a,
        present,
    )


def test_decision_inputs_without_values_are_unknown() -> None:
    assert decision_inputs(snap({}), {}) == dict.fromkeys(
        ("soc_pct", "battery_w", "grid_l1_a", "grid_l2_a", "grid_l3_a")
    )


def test_decision_inputs_leave_out_numbers_json_cannot_hold() -> None:
    inputs = decision_inputs(snap({}), {"L1": math.nan, "L2": math.inf, "L3": 2.0})
    assert (inputs["grid_l1_a"], inputs["grid_l2_a"], inputs["grid_l3_a"]) == (None, None, 2.0)
