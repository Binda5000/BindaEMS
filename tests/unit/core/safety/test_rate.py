from dataclasses import replace
from datetime import timedelta

import pytest
from tests.helpers import T0
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.rate import (
    EVCS_DEADBAND_A,
    EVCS_INTERVAL_S,
    VICTRON_DEADBAND_W,
    VICTRON_OVERRIDE_INTERVAL_S,
    RateLimiter,
    deadband,
)
from bindaems.core.safety.types import Finding
from bindaems.shared.control import Command, CommandKind

K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def test_evcs_current_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("wallbox.evcs", K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(4)) == Finding(
        "rate.interval", "Mindestabstand 5 s noch nicht erreicht."
    )
    assert rl.check(cmd, S(5)) is None


def test_kinds_are_independent(limits) -> None:
    rl = RateLimiter(limits)
    rl.record(Command("wallbox.evcs", K.CURRENT, 10.0), T0)
    assert rl.check(Command("wallbox.evcs", K.CHARGING, True), S(1)) is None


def test_victron_setpoint_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("victron", K.GRID_SETPOINT, 500.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(1)).code == "rate.interval"
    assert rl.check(cmd, S(2)) is None


def test_persistent_kinds_have_no_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("victron", K.MIN_SOC, 30.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_tesla_current_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("vehicle.tesla", K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(89)).text == "Mindestabstand 90 s noch nicht erreicht."
    assert rl.check(cmd, S(90)) is None


def test_tesla_hourly_limit(limits) -> None:
    rl = RateLimiter(limits)
    for i in range(20):
        rl.record(Command("vehicle.tesla", K.CURRENT, 10.0), S(91 * i))
    later = Command("vehicle.tesla", K.CURRENT, 12.0)
    assert rl.check(later, S(91 * 20)) == Finding(
        "rate.per_hour", "Höchstens 20 Befehle pro Stunde erreicht."
    )
    assert rl.check(later, S(3601)) is None


def test_tesla_start_stop_limit(limits) -> None:
    rl = RateLimiter(limits)
    for i in range(6):
        rl.record(Command("vehicle.tesla", K.CHARGING, i % 2 == 0), S(60 * i))
    finding = rl.check(Command("vehicle.tesla", K.CHARGING, True), S(420))
    assert finding == Finding("rate.start_stop", "Höchstens 6 Start/Stopp pro Stunde erreicht.")


DEADBAND = [
    (Command("wallbox.evcs", K.CURRENT, 10.0), 10.0, "Wert unverändert."),
    (Command("vehicle.tesla", K.CURRENT, 11.0), 10.0, "Änderung unter dem Totband (2 A)."),
    (Command("vehicle.tesla", K.CURRENT, 12.0), 10.0, None),
    (Command("victron", K.GRID_SETPOINT, 1030.0), 1000.0, "Änderung unter dem Totband (50 W)."),
    (Command("victron", K.GRID_SETPOINT, None), None, "Wert unverändert."),
    (Command("victron", K.GRID_SETPOINT, None), 1000.0, None),
    (Command("victron", K.MIN_SOC, 30.0), 30.0, "Wert unverändert."),
    (Command("victron", K.MIN_SOC, 31.0), 30.0, None),
    (Command("wallbox.evcs", K.CHARGING, True), True, "Wert unverändert."),
]


@pytest.mark.parametrize(("command", "present", "text"), DEADBAND)
def test_deadband(limits, command, present, text) -> None:
    ctx = make_ctx(present={(command.actuator, command.kind): present})
    finding = deadband(command, command.value, ctx, limits)
    assert (finding.text if finding else None) == text


def test_no_deadband_without_present_value(limits) -> None:
    cmd = Command("victron", K.GRID_SETPOINT, 1000.0)
    assert deadband(cmd, 1000.0, make_ctx(), limits) is None


# Weitere Randfälle der Regeltabelle, die die Tests oben offenlassen: Grenzen der Abstände und
# Fenster, Reihenfolge der Prüfungen, Unabhängigkeit der Schlüssel, Werte aus der Konfiguration,
# Gleichheit gegen Totband, unbrauchbare Werte.
EVCS, TESLA, VICTRON = "wallbox.evcs", "vehicle.tesla", "victron"
NAN, INF = float("nan"), float("inf")
INTERVAL_5 = Finding("rate.interval", "Mindestabstand 5 s noch nicht erreicht.")
UNCHANGED = Finding("rate.unchanged", "Wert unverändert.")


def inside(width: str, unit: str) -> Finding:
    return Finding("rate.deadband", f"Änderung unter dem Totband ({width} {unit}).")


def fill(rl: RateLimiter, command: Command, seconds) -> None:
    """Merkt den Befehl zu allen Zeitpunkten (in s seit ``T0``)."""
    for s in seconds:
        rl.record(command, S(s))


def tesla_limits(limits: HardLimits, **changes) -> HardLimits:
    """Grenzen mit geänderten Befehlslimits des Teslas."""
    commands = limits.vehicle_commands[TESLA].model_copy(update=changes)
    return replace(limits, vehicle_commands={**limits.vehicle_commands, TESLA: commands})


def test_constants() -> None:
    assert (EVCS_INTERVAL_S, VICTRON_OVERRIDE_INTERVAL_S) == (5.0, 2.0)
    assert (EVCS_DEADBAND_A, VICTRON_DEADBAND_W) == (1.0, 50.0)


def test_evcs_charging_has_its_own_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CHARGING, True)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(4.9)) == INTERVAL_5
    assert rl.check(cmd, S(5)) is None


def test_victron_setpoint_interval_text(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(VICTRON, K.GRID_SETPOINT, 500.0)
    rl.record(cmd, T0)
    finding = rl.check(cmd, S(1.9))
    assert finding == Finding("rate.interval", "Mindestabstand 2 s noch nicht erreicht.")


def test_max_discharge_has_no_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(VICTRON, K.MAX_DISCHARGE, 1000.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_tesla_charging_has_no_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(TESLA, K.CHARGING, True)
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_interval_counts_from_the_last_record(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CURRENT, 10.0)
    fill(rl, cmd, [0, 10])
    assert rl.check(cmd, S(14)) == INTERVAL_5
    assert rl.check(cmd, S(15)) is None


def test_check_does_not_record(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CURRENT, 10.0)
    assert rl.check(cmd, T0) is None
    assert rl.check(cmd, T0) is None


def test_check_does_not_forget_records(limits) -> None:
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(7200)) is None  # eine spätere Frage …
    assert rl.check(cmd, S(1)) == INTERVAL_5  # … vergisst nichts


def test_actuators_are_independent(limits) -> None:
    rl = RateLimiter(limits)
    rl.record(Command(EVCS, K.CURRENT, 10.0), T0)
    assert rl.check(Command(TESLA, K.CURRENT, 10.0), S(1)) is None


def test_record_in_the_future_defers(limits) -> None:
    # Läuft die Uhr zurück, ist der Abstand negativ und damit nicht erreicht.
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CURRENT, 10.0)
    rl.record(cmd, S(10))
    assert rl.check(cmd, T0) == INTERVAL_5


def test_records_after_now_still_count(limits) -> None:
    # Läuft die Uhr zurück, liegen Einträge nach ``now``; ihr Abstand ist negativ, also zählen sie.
    rl = RateLimiter(limits)
    ahead = range(5000, 5360, 60)  # 6 Start/Stopp, mindestens 5000 s nach T0
    fill(rl, Command(TESLA, K.CHARGING, True), ahead)
    assert rl.check(Command(TESLA, K.CHARGING, True), T0).code == "rate.start_stop"


def test_unknown_actuator_has_no_rules(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("wallbox.twc", K.CURRENT, 10.0)  # kein Aktor
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_tesla_interval_comes_from_the_configuration(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, current_interval_s=30.0))
    cmd = Command(TESLA, K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(29)).text == "Mindestabstand 30 s noch nicht erreicht."
    assert rl.check(cmd, S(30)) is None


def test_interval_text_uses_the_decimal_comma(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, current_interval_s=0.5))
    cmd = Command(TESLA, K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(0.4)).text == "Mindestabstand 0,5 s noch nicht erreicht."
    assert rl.check(cmd, S(0.5)) is None


def test_zero_interval_never_defers(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, current_interval_s=0.0))
    cmd = Command(TESLA, K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_interval_longer_than_the_window_still_counts(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, current_interval_s=7200.0))
    cmd = Command(TESLA, K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(3700)).text == "Mindestabstand 7200 s noch nicht erreicht."
    assert rl.check(cmd, S(7200)) is None


def test_hourly_limit_window_is_half_open(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CURRENT, 10.0), range(0, 2000, 100))  # 20 Befehle, der erste bei 0 s
    later = Command(TESLA, K.CURRENT, 12.0)
    assert rl.check(later, S(3599.999)).code == "rate.per_hour"
    assert rl.check(later, S(3600)) is None  # der erste Befehl ist genau 3600 s alt


def test_hourly_limit_counts_all_kinds(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CURRENT, 10.0), range(0, 1400, 100))  # 14 Strombefehle
    fill(rl, Command(TESLA, K.CHARGING, True), range(10, 60, 10))  # 5 Start/Stopp
    probe = Command(TESLA, K.CURRENT, 12.0)
    assert rl.check(probe, S(1400)) is None  # 19 Befehle
    rl.record(Command(TESLA, K.CHARGING, False), S(60))
    assert rl.check(probe, S(1400)).code == "rate.per_hour"  # 20 Befehle


def test_start_stop_counts_only_charging_commands(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CURRENT, 10.0), range(0, 1000, 100))  # 10 Strombefehle
    assert rl.check(Command(TESLA, K.CHARGING, True), S(1000)) is None


def test_start_stop_limit_does_not_block_current_commands(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CHARGING, True), range(0, 360, 60))  # 6 Start/Stopp
    assert rl.check(Command(TESLA, K.CURRENT, 10.0), S(420)) is None


def test_start_stop_window_is_half_open(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CHARGING, True), range(0, 360, 60))
    start = Command(TESLA, K.CHARGING, True)
    assert rl.check(start, S(3599.999)).code == "rate.start_stop"
    assert rl.check(start, S(3600)) is None


def test_interval_comes_before_the_hourly_limits(limits) -> None:
    rl = RateLimiter(limits)
    fill(rl, Command(TESLA, K.CURRENT, 10.0), [91 * i for i in range(20)])
    assert rl.check(Command(TESLA, K.CURRENT, 12.0), S(91 * 19 + 89)).code == "rate.interval"


def test_per_hour_comes_before_start_stop(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, per_hour=6, start_stop_per_hour=6))
    fill(rl, Command(TESLA, K.CHARGING, True), range(0, 360, 60))
    finding = rl.check(Command(TESLA, K.CHARGING, True), S(420))
    assert finding == Finding("rate.per_hour", "Höchstens 6 Befehle pro Stunde erreicht.")


def test_hourly_limits_come_from_the_configuration(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, per_hour=7, start_stop_per_hour=3))
    fill(rl, Command(TESLA, K.CHARGING, True), [0, 60, 120])
    assert rl.check(Command(TESLA, K.CHARGING, True), S(200)) == Finding(
        "rate.start_stop", "Höchstens 3 Start/Stopp pro Stunde erreicht."
    )
    fill(rl, Command(TESLA, K.CURRENT, 10.0), [200, 300, 400, 500])  # zusammen 7 Befehle
    assert rl.check(Command(TESLA, K.CURRENT, 12.0), S(600)) == Finding(
        "rate.per_hour", "Höchstens 7 Befehle pro Stunde erreicht."
    )


def test_zero_limits_allow_nothing(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, per_hour=0, start_stop_per_hour=0))
    assert rl.check(Command(TESLA, K.CURRENT, 10.0), T0) == Finding(
        "rate.per_hour", "Höchstens 0 Befehle pro Stunde erreicht."
    )


def test_zero_start_stop_limit_blocks_only_start_stop(limits) -> None:
    rl = RateLimiter(tesla_limits(limits, start_stop_per_hour=0))
    assert rl.check(Command(TESLA, K.CHARGING, True), T0) == Finding(
        "rate.start_stop", "Höchstens 0 Start/Stopp pro Stunde erreicht."
    )
    assert rl.check(Command(TESLA, K.CURRENT, 10.0), T0) is None


def test_only_the_tesla_has_hourly_limits(limits) -> None:
    rl = RateLimiter(limits)
    current = Command(EVCS, K.CURRENT, 10.0)
    fill(rl, current, range(0, 200, 5))  # 40 Befehle in 195 s
    assert rl.check(current, S(200)) is None
    charging = Command(EVCS, K.CHARGING, True)
    fill(rl, charging, range(0, 50, 5))  # 10 Start/Stopp
    assert rl.check(charging, S(50)) is None
    setpoint = Command(VICTRON, K.GRID_SETPOINT, 500.0)
    fill(rl, setpoint, range(0, 80, 2))  # 40 Befehle
    assert rl.check(setpoint, S(80)) is None


def test_hourly_counts_are_per_vehicle(limits) -> None:
    second = replace(limits.actuators[TESLA], id="vehicle.model_y")
    two_vehicles = replace(
        limits,
        actuators={**limits.actuators, second.id: second},
        vehicle_commands={**limits.vehicle_commands, second.id: limits.vehicle_commands[TESLA]},
    )
    rl = RateLimiter(two_vehicles)
    fill(rl, Command(TESLA, K.CURRENT, 10.0), range(0, 2000, 100))
    assert rl.check(Command(TESLA, K.CURRENT, 12.0), S(2000)).code == "rate.per_hour"
    assert rl.check(Command(second.id, K.CURRENT, 12.0), S(2000)) is None


def test_old_records_are_dropped(limits) -> None:
    # Der Speicher bleibt beschränkt: Es bleiben nur die Einträge im Fenster des letzten.
    rl, cmd = RateLimiter(limits), Command(EVCS, K.CURRENT, 10.0)
    fill(rl, cmd, range(0, 36000, 5))  # zehn Stunden im 5-s-Takt, der letzte bei 35 995 s
    # Es bleiben t = 32 400 … 35 995 s; der Eintrag bei 32 395 s ist genau 3600 s alt.
    assert len(rl._times[(EVCS, K.CURRENT)]) == 720


MORE_DEADBAND = [
    # (Aktor, Art, Wert, Ist-Wert, Befund)
    # EVCS-Strom: 1 A; genau 1 A Änderung liegt nicht mehr darunter
    (EVCS, K.CURRENT, 10.5, 10.0, inside("1", "A")),
    (EVCS, K.CURRENT, 9.5, 10.0, inside("1", "A")),
    (EVCS, K.CURRENT, 11.0, 10.0, None),
    (EVCS, K.CURRENT, 9.0, 10.0, None),
    # Tesla-Strom: current_step_a, in der Beispielkonfiguration 2 A
    (TESLA, K.CURRENT, 9.0, 10.0, inside("2", "A")),
    (TESLA, K.CURRENT, 8.0, 10.0, None),
    # Netz-Sollwert: 50 W; ohne Override (None) ist jeder Wert eine Änderung
    (VICTRON, K.GRID_SETPOINT, 1049.9, 1000.0, inside("50", "W")),
    (VICTRON, K.GRID_SETPOINT, 960.0, 1000.0, inside("50", "W")),
    (VICTRON, K.GRID_SETPOINT, 1050.0, 1000.0, None),
    (VICTRON, K.GRID_SETPOINT, 950.0, 1000.0, None),
    (VICTRON, K.GRID_SETPOINT, 1000.0, None, None),
    (VICTRON, K.GRID_SETPOINT, 0.0, None, None),
    # Gleicher Wert ist „unverändert“ und kein Totband, auch innerhalb des Totbands
    (EVCS, K.CURRENT, 10.0, 10.0, UNCHANGED),
    (TESLA, K.CURRENT, 10.0, 10.0, UNCHANGED),
    (VICTRON, K.GRID_SETPOINT, 1000.0, 1000.0, UNCHANGED),
    # Zahlen mit Abstand unter 1e-9 sind gleich, ab 1e-9 nicht mehr
    (VICTRON, K.GRID_SETPOINT, 1000.0, 1000.0 + 1e-10, UNCHANGED),
    (VICTRON, K.MIN_SOC, 30.0, 30.0 + 5e-10, UNCHANGED),
    (VICTRON, K.MAX_DISCHARGE, 0.0, 9e-10, UNCHANGED),
    (VICTRON, K.MAX_DISCHARGE, 0.0, 1e-9, None),
    # Persistente Arten: nur der Vergleich auf Gleichheit, kein Totband
    (VICTRON, K.MIN_SOC, 30.5, 30.0, None),
    (VICTRON, K.MAX_DISCHARGE, 1010.0, 1000.0, None),
    (VICTRON, K.MAX_DISCHARGE, -1.0, -1.0, UNCHANGED),
    (VICTRON, K.MAX_DISCHARGE, -1.0, 1000.0, None),
    # Laden an/aus: Gleichheit nur unter bool, nie zwischen bool und Zahl
    (EVCS, K.CHARGING, False, False, UNCHANGED),
    (TESLA, K.CHARGING, True, True, UNCHANGED),
    (EVCS, K.CHARGING, True, False, None),
    (EVCS, K.CHARGING, True, 1.0, None),
    (EVCS, K.CHARGING, False, 0.0, None),
    (EVCS, K.CHARGING, True, None, None),
    (EVCS, K.CURRENT, 1.0, True, None),
    (EVCS, K.CURRENT, True, 1.0, None),
    # Unbrauchbare Werte (nicht endlich, falscher Typ) sind weder gleich noch im Totband
    (EVCS, K.CURRENT, 10.0, NAN, None),
    (EVCS, K.CURRENT, NAN, 10.0, None),
    (EVCS, K.CURRENT, NAN, NAN, None),
    (EVCS, K.CURRENT, INF, INF, None),
    (EVCS, K.CURRENT, 10.0, "10", None),
    pytest.param(EVCS, K.CURRENT, 10.0, 10**400, None, id="huge-int"),  # größer als jedes float
    (EVCS, K.CURRENT, 10.0, None, None),
    # Was kein Aktor ist, hat kein Totband, aber den Vergleich auf Gleichheit
    ("wallbox.twc", K.CURRENT, 10.5, 10.0, None),
    ("wallbox.twc", K.CURRENT, 10.0, 10.0, UNCHANGED),
]


@pytest.mark.parametrize(("actuator", "kind", "value", "present", "expected"), MORE_DEADBAND)
def test_deadband_cases(limits, actuator, kind, value, present, expected) -> None:
    ctx = make_ctx(present={(actuator, kind): present})
    assert deadband(Command(actuator, kind, value), value, ctx, limits) == expected


def test_deadband_judges_the_given_value_not_the_command_value(limits) -> None:
    command = Command(EVCS, K.CURRENT, 16.0)  # angefordert; die Regeln davor haben geklemmt
    ctx = make_ctx(present={(EVCS, K.CURRENT): 10.0})
    assert deadband(command, 10.0, ctx, limits) == UNCHANGED
    assert deadband(command, 10.5, ctx, limits) == inside("1", "A")
    assert deadband(command, 16.0, ctx, limits) is None


def test_present_of_another_actuator_or_kind_does_not_count(limits) -> None:
    ctx = make_ctx(present={(EVCS, K.CURRENT): 10.0, (TESLA, K.CHARGING): True})
    assert deadband(Command(TESLA, K.CURRENT, 10.0), 10.0, ctx, limits) is None


def test_tesla_band_comes_from_the_configuration(limits) -> None:
    custom = tesla_limits(limits, current_step_a=3)
    ctx = make_ctx(present={(TESLA, K.CURRENT): 10.0})
    assert deadband(Command(TESLA, K.CURRENT, 12.0), 12.0, ctx, custom) == inside("3", "A")
    assert deadband(Command(TESLA, K.CURRENT, 13.0), 13.0, ctx, custom) is None
