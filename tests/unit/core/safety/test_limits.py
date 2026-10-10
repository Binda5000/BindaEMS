import pytest
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.safety.limits import CurrentRange, HardLimits, toward_safe
from bindaems.shared.control import Command, CommandKind


def test_catalog_from_example(limits) -> None:
    assert list(limits.actuators) == ["victron", "wallbox.evcs", "vehicle.tesla"]
    evcs = limits.actuators["wallbox.evcs"]
    assert (evcs.family, evcs.label, evcs.needs_watchdog, evcs.live_allowed) == (
        "evcs",
        "EVCS „evcs“",
        True,
        False,
    )
    tesla = limits.actuators["vehicle.tesla"]
    assert (tesla.label, tesla.needs_watchdog) == ("Tesla Model 3", False)
    assert limits.actuators["victron"].kinds == {
        CommandKind.GRID_SETPOINT,
        CommandKind.MAX_DISCHARGE,
        CommandKind.MIN_SOC,
    }
    assert limits.currents["wallbox.evcs"] == CurrentRange(6, 16, 6)
    assert limits.currents["vehicle.tesla"] == CurrentRange(5, 16, 5)
    assert limits.wallbox_phases["twc"] == ("L1", "L2", "L3")


def test_phase_limit_and_setpoint_ceiling(limits) -> None:
    assert limits.phase_limit_a == 33.0
    assert limits.grid_setpoint_max(None) == (8000.0, "maximaler Netzladesetpoint")
    assert limits.grid_setpoint_max(5000.0) == (5000.0, "Leistungsbudget")


def test_fuse_and_max_import_bind(cfg) -> None:
    victron = cfg.victron.model_copy(update={"max_grid_charge_setpoint_w": 30000.0})
    high = cfg.model_copy(update={"victron": victron})
    assert HardLimits.from_config(high).grid_setpoint_max(None) == (22770.0, "Absicherung")
    capped = high.model_copy(update={"grid": cfg.grid.model_copy(update={"max_import_kw": 6.0})})
    assert HardLimits.from_config(capped).grid_setpoint_max(None) == (
        6000.0,
        "maximale Bezugsleistung",
    )


def test_safe_commands(limits) -> None:
    def safe(kind, value, actuator="victron"):
        return Command(actuator, kind, value, urgent=True, reasons=("safe_state",))

    assert limits.safe_commands("victron") == (
        safe(CommandKind.GRID_SETPOINT, None),
        safe(CommandKind.MAX_DISCHARGE, -1.0),
        safe(CommandKind.MIN_SOC, 20.0),
    )
    assert limits.safe_commands("wallbox.evcs") == (safe(CommandKind.CURRENT, 6.0, "wallbox.evcs"),)
    assert limits.safe_commands("vehicle.tesla") == (
        safe(CommandKind.CURRENT, 5.0, "vehicle.tesla"),
    )


K = CommandKind
TOWARD_SAFE = [
    (Command("wallbox.evcs", K.CURRENT, 6.0), {("wallbox.evcs", K.CURRENT): 16.0}, True),
    (Command("wallbox.evcs", K.CURRENT, 16.0), {("wallbox.evcs", K.CURRENT): 6.0}, False),
    (Command("wallbox.evcs", K.CURRENT, 6.0), {}, True),
    (Command("wallbox.evcs", K.CURRENT, 10.0), {}, False),
    (Command("vehicle.tesla", K.CHARGING, False), {}, True),
    (Command("vehicle.tesla", K.CHARGING, True), {}, False),
    (Command("victron", K.GRID_SETPOINT, None), {}, True),
    (Command("victron", K.GRID_SETPOINT, 500.0), {("victron", K.GRID_SETPOINT): 1000.0}, True),
    (Command("victron", K.GRID_SETPOINT, 500.0), {("victron", K.GRID_SETPOINT): None}, False),
    (Command("victron", K.GRID_SETPOINT, 500.0), {}, False),
    (Command("victron", K.MAX_DISCHARGE, -1.0), {}, True),
    (Command("victron", K.MAX_DISCHARGE, 5000.0), {("victron", K.MAX_DISCHARGE): 3000.0}, True),
    (Command("victron", K.MAX_DISCHARGE, 5000.0), {("victron", K.MAX_DISCHARGE): -1.0}, False),
    (Command("victron", K.MIN_SOC, 20.0), {}, True),
    (Command("victron", K.MIN_SOC, 30.0), {("victron", K.MIN_SOC): 40.0}, True),
    (Command("victron", K.MIN_SOC, 30.0), {("victron", K.MIN_SOC): 25.0}, False),
    (Command("wallbox.evcs", K.CURRENT, True), {}, False),
]


@pytest.mark.parametrize(("command", "present", "expected"), TOWARD_SAFE)
def test_toward_safe(limits, command, present, expected) -> None:
    assert toward_safe(command, make_ctx(present=present), limits) is expected


@pytest.mark.parametrize(
    ("value", "actual"),
    [
        pytest.param(10**400, 16.0, id="value-too-large"),
        pytest.param(-(10**400), 16.0, id="value-too-small"),
        pytest.param(6.0, 10**400, id="actual-too-large"),
    ],
)
def test_toward_safe_ignores_an_int_too_large_for_a_float(limits, value, actual) -> None:
    command = Command("wallbox.evcs", K.CURRENT, value, urgent=True)
    ctx = make_ctx(present={("wallbox.evcs", K.CURRENT): actual})
    assert toward_safe(command, ctx, limits) is False
