from datetime import timedelta

from tests.helpers import T0

from bindaems.core.control.safe_state import (
    SAFE_ATTEMPTS,
    SAFE_RETRY_S,
    SafeStateKeeper,
    SafeTarget,
    safe_values,
    safe_values_reached,
)
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.config import Config
from bindaems.shared.control import Command, CommandKind
from bindaems.shared.domain import Alarm, Severity

EVCS = "wallbox.evcs"
CUR = (EVCS, CommandKind.CURRENT)


def test_safe_current_for_a_live_target(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    cmds = keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 16.0}, frozenset(), {EVCS})
    assert cmds == [Command(EVCS, CommandKind.CURRENT, 6.0, urgent=True, reasons=("safe_state",))]


def test_matching_value_needs_no_command(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert (
        keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 6}, frozenset(), {EVCS}) == []
    )


def test_dry_run_target_once_per_incident(limits) -> None:
    keeper, target = SafeStateKeeper(limits), [SafeTarget(EVCS, "safe_state")]
    assert len(keeper.commands(T0, target, {CUR: 16.0}, frozenset(), set())) == 1
    assert (
        keeper.commands(T0 + timedelta(seconds=30), target, {CUR: 16.0}, frozenset(), set()) == []
    )
    keeper.commands(T0 + timedelta(seconds=31), [], {}, frozenset(), set())  # Vorfall endet
    assert (
        len(keeper.commands(T0 + timedelta(seconds=32), target, {CUR: 16.0}, frozenset(), set()))
        == 1
    )


def test_safe_values_retry_three_times_then_fail(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    sent_at = [
        s
        for s in (0, 5, 10, 20, 30, 40, 60)
        if keeper.commands(T0 + timedelta(seconds=s), target, {CUR: 16.0}, frozenset(), {EVCS})
    ]
    assert sent_at == [0, 10, 20]
    assert keeper.failed == {CUR}
    alarms = keeper.alarms(T0, {EVCS: "EVCS „evcs“"}, {EVCS})
    assert [a.message for a in alarms] == [
        "EVCS „evcs“: Sicherwert für „current“ nicht bestätigt. Der Watchdog am Cerbo übernimmt."
    ]


def test_pending_write_waits(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 16.0}, {CUR}, {EVCS}) == []


def test_grid_outage_adds_stop(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    present = {CUR: 16.0, (EVCS, CommandKind.CHARGING): True}
    cmds = keeper.commands(
        T0, [SafeTarget(EVCS, "safe_state", stop_charging=True)], present, frozenset(), {EVCS}
    )
    assert [(c.kind, c.value, c.reasons) for c in cmds] == [
        (CommandKind.CURRENT, 6.0, ("safe_state",)),
        (CommandKind.CHARGING, False, ("grid_outage",)),
    ]


def test_victron_safe_values(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    cmds = keeper.commands(T0, [SafeTarget("victron", "handback")], {}, frozenset(), {"victron"})
    assert [(c.kind, c.value) for c in cmds] == [
        (CommandKind.GRID_SETPOINT, None),
        (CommandKind.MAX_DISCHARGE, -1.0),
        (CommandKind.MIN_SOC, 20.0),
    ]


# --- Weitere Fälle: Sicherwerte, Vorfälle, Dry-Run, gescheiterte Arten und Alarme ---------------

TESLA = "vehicle.tesla"
CHG = (EVCS, CommandKind.CHARGING)
GRID = ("victron", CommandKind.GRID_SETPOINT)
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
LABELS = {EVCS: "EVCS „evcs“", "victron": "Victron (ESS)", TESLA: "Tesla Model 3"}
VICTRON_SAFE = {
    GRID: None,
    ("victron", CommandKind.MAX_DISCHARGE): -1.0,
    ("victron", CommandKind.MIN_SOC): 20.0,
}


def sent(keeper: SafeStateKeeper, seconds: int, targets, present, *, live=(EVCS,), pending=()):
    """Die Befehle eines Aufrufs als (Aktor, Art, Wert)."""
    cmds = keeper.commands(S(seconds), targets, present, frozenset(pending), frozenset(live))
    return [(c.actuator, c.kind, c.value) for c in cmds]


def test_constants() -> None:
    assert (SAFE_RETRY_S, SAFE_ATTEMPTS) == (10.0, 3)


def test_safe_values_per_family(limits_live) -> None:
    assert safe_values(limits_live, SafeTarget(EVCS, "safe_state")) == {CommandKind.CURRENT: 6.0}
    assert safe_values(limits_live, SafeTarget(EVCS, "safe_state", stop_charging=True)) == {
        CommandKind.CURRENT: 6.0,
        CommandKind.CHARGING: False,
    }
    assert safe_values(limits_live, SafeTarget(TESLA, "handback", stop_charging=True)) == {
        CommandKind.CURRENT: 5.0,
        CommandKind.CHARGING: False,
    }
    # Victron lädt kein Fahrzeug: Der Stopp bei Netzausfall betrifft ihn nicht.
    assert safe_values(limits_live, SafeTarget("victron", "safe_state", stop_charging=True)) == {
        CommandKind.GRID_SETPOINT: None,
        CommandKind.MAX_DISCHARGE: -1.0,
        CommandKind.MIN_SOC: 20.0,
    }
    assert safe_values(limits_live, SafeTarget("kein.aktor", "safe_state", True)) == {}


def test_safe_values_do_not_depend_on_live_allowed(limits) -> None:
    assert safe_values(limits, SafeTarget(EVCS, "handback")) == {CommandKind.CURRENT: 6.0}


def test_reached_needs_every_known_value(limits_live) -> None:
    victron = SafeTarget("victron", "handback")
    assert safe_values_reached(limits_live, victron, VICTRON_SAFE)
    # Kein Override (None) ist ein bekannter Wert; ein fehlender Eintrag ist unbekannt.
    assert not safe_values_reached(
        limits_live, victron, {k: v for k, v in VICTRON_SAFE.items() if k != GRID}
    )
    assert not safe_values_reached(limits_live, victron, VICTRON_SAFE | {GRID: 50.0})
    stop = SafeTarget(EVCS, "safe_state", stop_charging=True)
    assert not safe_values_reached(limits_live, stop, {CUR: 6.0})
    assert not safe_values_reached(limits_live, stop, {CUR: 6.0, CHG: True})
    assert safe_values_reached(limits_live, stop, {CUR: 6.4, CHG: False})


def test_reached_uses_the_tolerance_of_the_kind(limits_live) -> None:
    target = SafeTarget(EVCS, "handback")
    assert safe_values_reached(limits_live, target, {CUR: 5.6})
    assert not safe_values_reached(limits_live, target, {CUR: 6.5})
    assert not safe_values_reached(limits_live, target, {CUR: None})


def test_handback_commands_carry_the_handback_reason(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    cmds = keeper.commands(T0, [SafeTarget(EVCS, "handback")], {CUR: 16.0}, frozenset(), {EVCS})
    assert cmds == [Command(EVCS, CommandKind.CURRENT, 6.0, urgent=True, reasons=("handback",))]


def test_unknown_value_gets_a_command(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert sent(keeper, 0, [SafeTarget(EVCS, "safe_state")], {}) == [
        (EVCS, CommandKind.CURRENT, 6.0)
    ]


def test_known_safe_values_need_no_command(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    target = [SafeTarget("victron", "safe_state")]
    assert sent(keeper, 0, target, VICTRON_SAFE, live=("victron",)) == []
    assert sent(keeper, 1, target, VICTRON_SAFE | {GRID: 50.0}, live=("victron",)) == [
        ("victron", CommandKind.GRID_SETPOINT, None)
    ]


def test_targets_keep_their_order(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    targets = [SafeTarget(TESLA, "safe_state"), SafeTarget(EVCS, "handback")]
    assert sent(keeper, 0, targets, {}, live=(EVCS, TESLA)) == [
        (TESLA, CommandKind.CURRENT, 5.0),
        (EVCS, CommandKind.CURRENT, 6.0),
    ]


def test_each_kind_keeps_its_own_attempts(limits_live) -> None:
    keeper, target = (
        SafeStateKeeper(limits_live),
        [SafeTarget(EVCS, "safe_state", stop_charging=True)],
    )
    assert len(sent(keeper, 0, target, {CUR: 16.0, CHG: False})) == 1  # nur der Strom
    # Das Laden geht wieder an: Der Stopp ist sofort dran, der Strom erst nach 10 s.
    assert sent(keeper, 5, target, {CUR: 16.0, CHG: True}) == [(EVCS, CommandKind.CHARGING, False)]
    assert sent(keeper, 10, target, {CUR: 16.0, CHG: True}) == [(EVCS, CommandKind.CURRENT, 6.0)]


def test_matching_value_does_not_reset_the_attempts(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20):
        assert len(sent(keeper, seconds, target, {CUR: 16.0})) == 1
        assert sent(keeper, seconds + 1, target, {CUR: 6.0}) == []  # bestätigt
    # Höchstens 3 Versuche je Vorfall, auch wenn der Wert zwischendurch stimmte.
    assert sent(keeper, 40, target, {CUR: 16.0}) == []
    assert keeper.failed == {CUR}


def test_pending_write_neither_counts_nor_fails(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert sent(keeper, 30, target, {CUR: 16.0}, pending=[CUR]) == []
    assert keeper.failed == frozenset()  # der offene Wert kann noch ankommen
    assert sent(keeper, 31, target, {CUR: 16.0}) == []
    assert keeper.failed == {CUR}


def test_pending_write_delays_the_first_attempt(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    assert sent(keeper, 0, target, {CUR: 16.0}, pending=[CUR]) == []
    assert sent(keeper, 1, target, {CUR: 16.0}) == [(EVCS, CommandKind.CURRENT, 6.0)]


def test_failed_only_after_the_retry_time(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert keeper.failed == frozenset()
    sent(keeper, 29, target, {CUR: 16.0})
    assert keeper.failed == frozenset()
    sent(keeper, 30, target, {CUR: 16.0})
    assert keeper.failed == {CUR}


def test_failed_kind_clears_when_the_value_is_right(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert keeper.failed == {CUR}
    sent(keeper, 31, target, {CUR: 6.0})  # etwa der Watchdog hat den Sicherwert gesetzt
    assert keeper.failed == frozenset() and keeper.alarms(S(31), LABELS, {EVCS}) == []


def test_dry_run_target_never_fails(limits) -> None:
    keeper, target = SafeStateKeeper(limits), [SafeTarget(EVCS, "safe_state")]
    for seconds in range(0, 100, 10):
        sent(keeper, seconds, target, {CUR: 16.0}, live=())
    assert keeper.failed == frozenset() and keeper.alarms(S(100), LABELS, {EVCS}) == []


def test_dry_run_waits_until_the_value_differs(limits) -> None:
    keeper, target = SafeStateKeeper(limits), [SafeTarget(EVCS, "safe_state")]
    assert sent(keeper, 0, target, {CUR: 6.0}, live=()) == []
    assert sent(keeper, 1, target, {CUR: 16.0}, live=()) == [(EVCS, CommandKind.CURRENT, 6.0)]
    assert sent(keeper, 50, target, {CUR: 16.0}, live=()) == []


def test_dry_run_waits_for_a_pending_write(limits) -> None:
    keeper, target = SafeStateKeeper(limits), [SafeTarget(EVCS, "safe_state")]
    assert sent(keeper, 0, target, {CUR: 16.0}, live=(), pending=[CUR]) == []
    assert sent(keeper, 1, target, {CUR: 16.0}, live=()) == [(EVCS, CommandKind.CURRENT, 6.0)]


def test_actuator_turning_live_starts_with_three_attempts(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    assert len(sent(keeper, 0, target, {CUR: 16.0}, live=())) == 1  # Dry-Run zählt nicht
    sent_at = [s for s in (1, 5, 11, 21, 31, 41) if sent(keeper, s, target, {CUR: 16.0})]
    assert sent_at == [1, 11, 21]


def test_live_attempts_stay_with_the_incident(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    sent(keeper, 0, target, {CUR: 16.0})  # 1. Versuch
    assert len(sent(keeper, 5, target, {CUR: 16.0}, live=())) == 1  # jetzt im Dry-Run
    sent_at = [s for s in (10, 20, 30, 40) if sent(keeper, s, target, {CUR: 16.0})]
    assert sent_at == [10, 20]  # wieder live: noch 2 Versuche


def test_dry_run_actuator_is_not_in_failed(limits_live) -> None:
    # Ein Ziel, das im Vorfall nie live war, scheitert nie, auch nach einem früheren Vorfall.
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert keeper.failed == {CUR}
    sent(keeper, 31, [], {})  # Vorfall endet
    assert sent(keeper, 32, target, {CUR: 16.0}, live=()) == [(EVCS, CommandKind.CURRENT, 6.0)]
    for seconds in range(42, 100, 10):
        assert sent(keeper, seconds, target, {CUR: 16.0}, live=()) == []
    assert keeper.failed == frozenset() and keeper.alarms(S(92), LABELS, {EVCS}) == []


def fail_live(keeper: SafeStateKeeper, target) -> None:
    """Drei Live-Versuche bei 0, 10 und 20 s ohne Treffer: Bei 30 s ist der Strom gescheitert."""
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert keeper.failed == {CUR}


def test_failed_kind_stays_failed_when_no_longer_live(limits_live) -> None:
    # Der Watchdog löst aus: Die EVCS ist nicht mehr wirksam live, der Strom bleibt gescheitert.
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "handback")]
    fail_live(keeper, target)
    for seconds in (31, 41, 100):
        assert sent(keeper, seconds, target, {CUR: 16.0}, live=()) == []  # auch kein Dry-Run
        assert keeper.failed == {CUR}
    assert [(a.id, a.since) for a in keeper.alarms(S(100), LABELS, {EVCS})] == [
        ("actuator.failed.wallbox.evcs", S(30))
    ]


def test_failed_kind_ends_when_the_value_matches_while_not_live(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "handback")]
    fail_live(keeper, target)
    sent(keeper, 31, target, {CUR: 16.0}, live=())
    assert keeper.failed == {CUR}
    assert sent(keeper, 40, target, {CUR: 6.0}, live=()) == []  # der Watchdog setzt den Sicherwert
    assert keeper.failed == frozenset() and keeper.alarms(S(40), LABELS, {EVCS}) == []


def test_matched_kind_fails_again_only_while_live(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    fail_live(keeper, target)
    sent(keeper, 31, target, {CUR: 16.0}, live=())  # Watchdog ausgelöst
    sent(keeper, 40, target, {CUR: 6.0}, live=())  # der Watchdog setzt den Sicherwert
    assert keeper.failed == frozenset()
    # Der Wert weicht wieder ab, die EVCS ist weiter nicht live: nicht gescheitert, einmal Dry-Run.
    assert sent(keeper, 50, target, {CUR: 16.0}, live=()) == [(EVCS, CommandKind.CURRENT, 6.0)]
    assert keeper.failed == frozenset()
    # Wieder live: Die Versuche des Vorfalls sind verbraucht, die Art scheitert ohne Versuch.
    assert sent(keeper, 60, target, {CUR: 16.0}) == []
    assert keeper.failed == {CUR}


def test_failed_kind_ends_with_the_incident_while_not_live(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "handback")]
    fail_live(keeper, target)
    sent(keeper, 31, target, {CUR: 16.0}, live=())
    assert keeper.failed == {CUR}
    sent(keeper, 32, [], {}, live=())  # kein Ziel mehr
    assert keeper.failed == frozenset() and keeper.alarms(S(32), LABELS, {EVCS}) == []


def test_failed_kind_gets_no_new_attempts_when_live_again(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "handback")]
    fail_live(keeper, target)
    sent(keeper, 31, target, {CUR: 16.0}, live=())  # Watchdog ausgelöst
    sent_at = [s for s in (61, 71, 81, 91, 120) if sent(keeper, s, target, {CUR: 16.0})]
    assert sent_at == [] and keeper.failed == {CUR}  # wieder scharf und live: kein Versuch mehr


def test_failure_is_only_established_while_live(limits_live) -> None:
    # Die 3 Versuche sind verbraucht, aber vor Ablauf der 10 s ist die EVCS nicht mehr live.
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert sent(keeper, 30, target, {CUR: 16.0}, live=()) == [(EVCS, CommandKind.CURRENT, 6.0)]
    assert keeper.failed == frozenset()  # im Dry-Run nicht gescheitert, der Befehl kommt einmal
    assert sent(keeper, 40, target, {CUR: 16.0}, live=()) == []
    assert sent(keeper, 50, target, {CUR: 16.0}) == []  # wieder live: gescheitert, ohne Versuch
    assert keeper.failed == {CUR}


def test_new_incident_starts_with_new_attempts(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert keeper.failed == {CUR}
    sent(keeper, 31, [], {})  # Vorfall endet
    assert keeper.failed == frozenset() and keeper.alarms(S(31), LABELS, {EVCS}) == []
    assert sent(keeper, 32, target, {CUR: 16.0}) == [(EVCS, CommandKind.CURRENT, 6.0)]


def test_change_of_reason_is_the_same_incident(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    sent(keeper, 0, [SafeTarget(EVCS, "handback")], {CUR: 16.0})
    assert sent(keeper, 1, [SafeTarget(EVCS, "safe_state")], {CUR: 16.0}) == []


def test_other_targets_keep_their_incident(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    both = [SafeTarget(EVCS, "safe_state"), SafeTarget(TESLA, "safe_state")]
    sent(keeper, 0, both, {}, live=(EVCS, TESLA))
    assert sent(keeper, 1, both[1:], {}, live=(EVCS, TESLA)) == []  # der Tesla wartet weiter
    assert sent(keeper, 2, both, {}, live=(EVCS, TESLA)) == [(EVCS, CommandKind.CURRENT, 6.0)]


def test_same_actuator_twice_sends_once(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    twice = [SafeTarget(EVCS, "safe_state"), SafeTarget(EVCS, "handback")]
    cmds = keeper.commands(T0, twice, {CUR: 16.0}, frozenset(), {EVCS})
    assert [(c.kind, c.reasons) for c in cmds] == [(CommandKind.CURRENT, ("safe_state",))]
    for seconds in (10, 20, 30):
        sent(keeper, seconds, twice, {CUR: 16.0})
    assert [a.message for a in keeper.alarms(S(30), LABELS, {EVCS})] == [
        "EVCS „evcs“: Sicherwert für „current“ nicht bestätigt. Der Watchdog am Cerbo übernimmt."
    ]


def test_dry_run_twice_sends_once(limits) -> None:
    keeper = SafeStateKeeper(limits)
    twice = [SafeTarget(EVCS, "safe_state"), SafeTarget(EVCS, "handback")]
    assert len(sent(keeper, 0, twice, {CUR: 16.0}, live=())) == 1


def test_alarm_names_every_failed_kind(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget("victron", "safe_state")]
    present = VICTRON_SAFE | {GRID: 50.0, ("victron", CommandKind.MIN_SOC): 10.0}
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, present, live=("victron",))
    assert keeper.alarms(S(30), LABELS, {"victron"}) == [
        Alarm(
            "actuator.failed.victron",
            Severity.ERROR,
            "Victron (ESS): Sicherwert für „grid_setpoint“, „min_soc“ nicht bestätigt. "
            "Der Watchdog am Cerbo übernimmt.",
            S(30),
        )
    ]


def test_alarm_without_watchdog_has_no_suffix(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(TESLA, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {(TESLA, CommandKind.CURRENT): 16.0}, live=(TESLA,))
    assert [(a.id, a.message) for a in keeper.alarms(S(30), LABELS, {EVCS, "victron"})] == [
        (
            "actuator.failed.vehicle.tesla",
            "Tesla Model 3: Sicherwert für „current“ nicht bestätigt.",
        )
    ]


def test_alarm_without_label_names_the_actuator(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert [a.message for a in keeper.alarms(S(30), {}, frozenset())] == [
        "wallbox.evcs: Sicherwert für „current“ nicht bestätigt."
    ]


def test_alarm_since_is_the_first_failure_of_the_incident(limits_live) -> None:
    keeper, target = (
        SafeStateKeeper(limits_live),
        [SafeTarget(EVCS, "safe_state", stop_charging=True)],
    )
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0, CHG: False})
    assert [a.since for a in keeper.alarms(S(30), LABELS, {EVCS})] == [S(30)]
    for seconds in (31, 41, 51):  # das Laden geht an, der Stopp scheitert später
        sent(keeper, seconds, target, {CUR: 16.0, CHG: True})
    sent(keeper, 61, target, {CUR: 16.0, CHG: True})
    alarms = keeper.alarms(S(61), LABELS, {EVCS})
    assert [(a.message, a.since) for a in alarms] == [
        (
            "EVCS „evcs“: Sicherwert für „current“, „charging“ nicht bestätigt. "
            "Der Watchdog am Cerbo übernimmt.",
            S(30),
        )
    ]


def test_new_incident_has_its_own_alarm_since(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert [a.since for a in keeper.alarms(S(30), LABELS, {EVCS})] == [S(30)]
    sent(keeper, 31, [], {})  # Vorfall endet
    for seconds in (32, 42, 52, 62):
        sent(keeper, seconds, target, {CUR: 16.0})
    assert [a.since for a in keeper.alarms(S(62), LABELS, {EVCS})] == [S(62)]


def test_alarms_follow_the_order_of_the_targets(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    targets = [SafeTarget(TESLA, "safe_state"), SafeTarget(EVCS, "safe_state")]
    present = {CUR: 16.0, (TESLA, CommandKind.CURRENT): 16.0}
    for seconds in (0, 10, 20, 30):
        sent(keeper, seconds, targets, present, live=(EVCS, TESLA))
    assert keeper.failed == {CUR, (TESLA, CommandKind.CURRENT)}
    assert [a.id for a in keeper.alarms(S(30), LABELS, {EVCS})] == [
        "actuator.failed.vehicle.tesla",
        "actuator.failed.wallbox.evcs",
    ]


def test_keeper_without_commands_has_nothing_failed(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert keeper.failed == frozenset() and keeper.alarms(T0, LABELS, {EVCS}) == []


# Ein Strom unter dem Sicherwert gilt als erreicht: Der sichere Zustand erhöht nie einen Strom
# (Spec 8.5). Die Konfiguration erlaubt min_a ≤ safe_a, etwa einen Sicherwert von 10 A.


def safe_at_ten(cfg: Config) -> HardLimits:
    """Grenzen mit einer live freigegebenen EVCS, deren Sicherwert 10 A ist (min_a 6 A)."""
    evcs = cfg.wallboxes["evcs"].model_copy(update={"safe_a": 10, "live_allowed": True})
    return HardLimits.from_config(
        cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "evcs": evcs}})
    )


def test_a_current_below_the_safe_value_needs_no_command(cfg: Config) -> None:
    keeper, target = SafeStateKeeper(safe_at_ten(cfg)), [SafeTarget(EVCS, "safe_state")]
    for seconds in (0, 10, 20, 30, 40):
        assert sent(keeper, seconds, target, {CUR: 8.0}) == []
    assert keeper.failed == frozenset() and keeper.alarms(S(40), LABELS, {EVCS}) == []


def test_a_current_above_the_safe_value_still_gets_it(cfg: Config) -> None:
    keeper, target = SafeStateKeeper(safe_at_ten(cfg)), [SafeTarget(EVCS, "safe_state")]
    assert sent(keeper, 0, target, {CUR: 12.0}) == [(EVCS, CommandKind.CURRENT, 10.0)]


def test_above_the_safe_value_the_tolerance_of_matches_applies(cfg: Config) -> None:
    limits, target = safe_at_ten(cfg), SafeTarget(EVCS, "safe_state")
    assert safe_values_reached(limits, target, {CUR: 10.4})
    assert not safe_values_reached(limits, target, {CUR: 10.5})
    assert not safe_values_reached(limits, target, {CUR: None})  # unbekannt bleibt unbekannt
    assert not safe_values_reached(limits, target, {CUR: float("nan")})
    assert not safe_values_reached(limits, target, {})


def test_a_handback_with_a_lower_current_is_done_at_once(cfg: Config) -> None:
    limits = safe_at_ten(cfg)
    assert safe_values_reached(limits, SafeTarget(EVCS, "handback"), {CUR: 8.0})
    keeper = SafeStateKeeper(limits)
    assert sent(keeper, 0, [SafeTarget(EVCS, "handback")], {CUR: 8.0}) == []


def test_only_the_current_may_stay_below_its_safe_value(limits_live) -> None:
    # Eine niedrigere Entladegrenze sperrt das Entladen, ein niedrigerer Min-SOC unterschreitet die
    # USV-Reserve, und „Laden aus“ bleibt ein Wahrheitswert: Für sie gilt weiter ``matches``.
    victron = SafeTarget("victron", "safe_state")
    lower_limit = VICTRON_SAFE | {("victron", CommandKind.MAX_DISCHARGE): 0.0}
    lower_soc = VICTRON_SAFE | {("victron", CommandKind.MIN_SOC): 10.0}
    assert not safe_values_reached(limits_live, victron, lower_limit)
    assert not safe_values_reached(limits_live, victron, lower_soc)
    stop = SafeTarget(EVCS, "safe_state", stop_charging=True)
    assert safe_values_reached(limits_live, stop, {CUR: 0.0, CHG: False})
    assert not safe_values_reached(limits_live, stop, {CUR: 0.0, CHG: True})
