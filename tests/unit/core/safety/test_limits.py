import math
from dataclasses import replace
from typing import cast

import pytest
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.safety.limits import CurrentRange, HardLimits, OffsetRange, toward_safe
from bindaems.shared.control import VICTRON, Command, CommandKind


def test_catalog_from_example(limits) -> None:
    assert list(limits.actuators) == [
        "victron",
        "wallbox.evcs",
        "vehicle.tesla",
        "thermal.klima",
    ]
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
    assert evcs.kinds == {CommandKind.CURRENT, CommandKind.CHARGING}
    assert tesla.kinds == {CommandKind.CURRENT, CommandKind.CHARGING, CommandKind.CHARGE_LIMIT}
    assert limits.currents["wallbox.evcs"] == CurrentRange(6, 16, 6)
    assert limits.currents["vehicle.tesla"] == CurrentRange(5, 16, 5)
    assert limits.wallbox_phases["twc"] == ("L1", "L2", "L3")


def test_catalog_has_thermal(limits) -> None:
    spec = limits.actuators["thermal.klima"]
    assert (spec.family, spec.label, spec.needs_watchdog, spec.live_allowed) == (
        "thermal",
        "Klimaanlage",
        False,
        False,
    )
    assert spec.kinds == {CommandKind.SETPOINT_OFFSET}
    assert limits.offsets == {"thermal.klima": OffsetRange(max_k=3.0, step_k=0.5, interval_s=600.0)}


def test_thermal_limits_follow_the_configuration(cfg) -> None:
    klima = cfg.thermal.devices["klima"].model_copy(
        update={"max_offset_k": 1.5, "step_interval_s": 900.0, "live_allowed": True, "label": "AC"}
    )
    changed = cfg.model_copy(
        update={"thermal": cfg.thermal.model_copy(update={"devices": {"klima": klima}})}
    )
    limits = HardLimits.from_config(changed)
    assert limits.offsets["thermal.klima"] == OffsetRange(1.5, 0.5, 900.0)
    assert (
        limits.actuators["thermal.klima"].label,
        limits.actuators["thermal.klima"].live_allowed,
    ) == (
        "AC",
        True,
    )


def test_without_thermal_devices_there_is_no_thermal_actuator(cfg) -> None:
    empty = cfg.model_copy(update={"thermal": cfg.thermal.model_copy(update={"devices": {}})})
    limits = HardLimits.from_config(empty)
    assert "thermal.klima" not in limits.actuators
    assert limits.offsets == {}
    assert limits.safe_commands("thermal.klima") == ()


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


def test_safe_command_is_zero(limits) -> None:
    assert limits.safe_commands("thermal.klima") == (
        Command(
            "thermal.klima",
            CommandKind.SETPOINT_OFFSET,
            0.0,
            urgent=True,
            reasons=("safe_state",),
        ),
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
    # Sollwertversatz: 0, oder dem Betrag nach höchstens der Ist-Wert mit gleichem Vorzeichen
    (Command("thermal.klima", K.SETPOINT_OFFSET, 0.0), {}, True),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 0),
        {("thermal.klima", K.SETPOINT_OFFSET): 2.0},
        True,
    ),
    (Command("thermal.klima", K.SETPOINT_OFFSET, 0.5), {}, False),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 1.0),
        {("thermal.klima", K.SETPOINT_OFFSET): 1.5},
        True,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 1.5),
        {("thermal.klima", K.SETPOINT_OFFSET): 1.5},
        True,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 2.0),
        {("thermal.klima", K.SETPOINT_OFFSET): 1.5},
        False,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, -1.0),
        {("thermal.klima", K.SETPOINT_OFFSET): -2.0},
        True,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, -1.0),
        {("thermal.klima", K.SETPOINT_OFFSET): 2.0},
        False,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 0.5),
        {("thermal.klima", K.SETPOINT_OFFSET): -1.0},
        False,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 0.5),
        {("thermal.klima", K.SETPOINT_OFFSET): None},
        False,
    ),
    (Command("thermal.klima", K.SETPOINT_OFFSET, False), {}, False),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 1.0),
        {("thermal.klima", K.SETPOINT_OFFSET): math.inf},
        False,
    ),
    (
        Command("thermal.klima", K.SETPOINT_OFFSET, 1.0),
        {("thermal.klima", K.SETPOINT_OFFSET): True},
        False,
    ),
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


def test_toward_safe_with_unknown_base_only_clears(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=None, present={(VICTRON, K.GRID_SETPOINT): None})
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, None, urgent=True), ctx, limits)
    assert not toward_safe(Command(VICTRON, K.GRID_SETPOINT, -1000.0, urgent=True), ctx, limits)


@pytest.mark.parametrize("base", [None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("value", [-1000.0, 0.0, 500.0])
def test_toward_safe_with_unusable_base_accepts_no_number(limits, base, value) -> None:
    # Ohne Override gilt der Basis-Sollwert; ist er unbekannt, lässt sich kein Wert vergleichen.
    ctx = make_ctx(base_grid_setpoint_w=base, present={(VICTRON, K.GRID_SETPOINT): None})
    assert not toward_safe(Command(VICTRON, K.GRID_SETPOINT, value, urgent=True), ctx, limits)
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, None, urgent=True), ctx, limits)


def test_toward_safe_with_unknown_base_compares_with_the_known_override(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=None, present={(VICTRON, K.GRID_SETPOINT): 1000.0})
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, 500.0), ctx, limits)
    assert not toward_safe(Command(VICTRON, K.GRID_SETPOINT, 1500.0), ctx, limits)


def test_toward_safe_without_override_compares_with_the_known_base(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=500.0, present={(VICTRON, K.GRID_SETPOINT): None})
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, 400.0), ctx, limits)
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, 500.0), ctx, limits)
    assert not toward_safe(Command(VICTRON, K.GRID_SETPOINT, 600.0), ctx, limits)


def test_kind_given_as_plain_text_counts_as_that_kind(limits) -> None:
    plain = Command("wallbox.evcs", cast(CommandKind, "current"), 6.0, urgent=True)
    ctx = make_ctx(present={("wallbox.evcs", K.CURRENT): 10.0})
    assert toward_safe(plain, ctx, limits)


@pytest.mark.parametrize(("command", "present", "expected"), TOWARD_SAFE)
def test_toward_safe_gives_the_same_for_kinds_as_plain_text(
    limits, command, present, expected
) -> None:
    # Jede Art, einmal als Mitglied und einmal als gleicher Text: dasselbe Urteil.
    plain = replace(command, kind=cast(CommandKind, str(command.kind)))
    assert type(plain.kind) is str
    assert toward_safe(plain, make_ctx(present=present), limits) is expected
