from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.control.foreign import (
    FOREIGN_HITS,
    WATCHED_KINDS,
    ForeignChange,
    ForeignChangeDetector,
)
from bindaems.core.safety.locks import LockManager, lock_causes
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Severity

EVCS = "wallbox.evcs"
CUR = (EVCS, CommandKind.CURRENT)
LABELS = {EVCS: "EVCS „evcs“", "victron": "Victron (ESS)"}


def checks(
    detector: ForeignChangeDetector, values: Sequence[CommandValue], pending=frozenset()
) -> list[ForeignChange]:
    found: list[ForeignChange] = []
    for i, value in enumerate(values):
        found += detector.check({CUR: value}, pending, T0 + timedelta(seconds=10 * i))
    return found


def test_pending_write_is_not_foreign() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 10, 10, 10], pending={CUR}) == []


def test_three_hits_in_a_row_are_needed() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 6, 10, 10]) == []
    assert len(checks(detector, [10])) == 1


def test_new_value_becomes_expected() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert len(checks(detector, [10, 10, 10, 10, 10, 10])) == 1


def test_three_hits_lock_then_release(limits) -> None:
    detector, locks = ForeignChangeDetector(60.0), LockManager(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])  # Erkennung bei T0 + 20 s

    def locked_at(seconds: int) -> bool:
        now = T0 + timedelta(seconds=seconds)
        locks.update(now, lock_causes(detector.alarms(now, LABELS), [], limits))
        return EVCS in locks.active(now)

    assert locked_at(20) and locked_at(79)  # Ursache besteht bis unter 80 s
    assert locked_at(80) and locked_at(139)  # Freigabewartezeit ab 80 s
    assert not locked_at(140)


def test_alarm_text_for_the_setpoint_override() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, None)
    for i in range(3):
        detector.check(
            {("victron", CommandKind.GRID_SETPOINT): 500.0},
            frozenset(),
            T0 + timedelta(seconds=10 * i),
        )
    assert [a.message for a in detector.alarms(T0 + timedelta(seconds=20), LABELS)] == [
        "Victron (ESS): Netz-Sollwert von außen geändert (500 W statt kein Override)."
    ]


def test_unexpected_pairs_are_not_watched() -> None:
    assert checks(ForeignChangeDetector(60.0), [10, 10, 10, 10]) == []


# Weitere Fälle: Zähler, unbekannte Werte, Alarme und Vergessen (Präzisierung 8), die die Tests
# oben nicht berühren.

VSET = ("victron", CommandKind.GRID_SETPOINT)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_constants() -> None:
    assert FOREIGN_HITS == 3
    assert WATCHED_KINDS == {CommandKind.CURRENT, CommandKind.GRID_SETPOINT}


def test_detection_reports_expected_actual_and_time() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    # Drei abweichende Werte in Folge zählen, auch verschiedene; der dritte ist der Ist-Wert.
    assert checks(detector, [10, 12, 14]) == [
        ForeignChange(EVCS, CommandKind.CURRENT, 6.0, 14, at(20))
    ]
    assert checks(detector, [14, 14, 14]) == []  # 14 ist jetzt die Erwartung


def test_hits_needed_are_configurable() -> None:
    detector = ForeignChangeDetector(60.0, hits=2)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10]) == []
    assert len(checks(detector, [10, 10])) == 1


def test_values_within_the_tolerance_are_no_hits() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [6.4, 5.6, 6.4, 5.6, 6.0] * 2) == []  # ±0,4 A wie beim Rücklesen
    assert len(checks(detector, [6.5, 5.5, 6.5])) == 1  # darüber sind es Treffer


def test_unknown_actual_value_is_neither_hit_nor_match() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    for i in range(5):  # unbekannt zählt nie als Treffer
        assert detector.check({}, frozenset(), at(10 * i)) == []
    assert checks(detector, [10, 10]) == []
    assert detector.check({}, frozenset(), at(50)) == []  # und setzt den Zähler nicht zurück
    assert len(detector.check({CUR: 10}, frozenset(), at(60))) == 1


def test_pending_write_neither_counts_nor_resets() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 10]) == []
    assert checks(detector, [10, 6, 10], pending={CUR}) == []  # weder Treffer noch Übereinstimmung
    assert len(checks(detector, [10])) == 1  # der dritte Treffer


def test_pending_write_of_another_pair_does_not_matter() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    pending = {(EVCS, CommandKind.CHARGING), ("wallbox.other", CommandKind.CURRENT)}
    assert len(checks(detector, [10, 10, 10], pending=pending)) == 1


def test_expect_resets_the_counter_and_sets_the_expectation() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 10]) == []
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)  # das EMS hat erneut geschrieben
    assert checks(detector, [10, 10]) == []
    detector.expect(EVCS, CommandKind.CURRENT, 10.0)  # und diesmal einen anderen Wert
    assert checks(detector, [10, 10, 10, 10]) == []


@pytest.mark.parametrize(
    "kind", [CommandKind.CHARGING, CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC]
)
def test_other_kinds_are_not_watched(kind) -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, kind, 6.0)
    for i in range(4):
        assert detector.check({(EVCS, kind): 10.0}, frozenset(), at(10 * i)) == []
    assert detector.alarms(at(40), LABELS) == []


def test_actuators_and_kinds_are_counted_independently() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    detector.expect("vehicle.tesla", CommandKind.CURRENT, 5.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, None)
    present = {CUR: 10.0, ("vehicle.tesla", CommandKind.CURRENT): 5.0, VSET: 500.0}
    found = [
        change for i in range(3) for change in detector.check(present, frozenset(), at(10 * i))
    ]
    assert [(c.actuator, c.kind) for c in found] == [(EVCS, CommandKind.CURRENT), VSET]
    assert sorted(a.id for a in detector.alarms(at(20), LABELS)) == [
        "competitor.foreign.victron",
        "competitor.foreign.wallbox.evcs",
    ]


def test_no_override_is_a_known_value() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, None)
    for i in range(6):  # kein Override ist die Erwartung und kein fehlender Wert
        assert detector.check({VSET: None}, frozenset(), at(10 * i)) == []
    assert detector.alarms(at(60), LABELS) == []


def test_lifted_override_is_a_hit() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, 200.0)
    found = [detector.check({VSET: None}, frozenset(), at(10 * i)) for i in range(3)]
    assert found == [
        [],
        [],
        [ForeignChange("victron", CommandKind.GRID_SETPOINT, 200.0, None, at(20))],
    ]
    assert [a.message for a in detector.alarms(at(20), LABELS)] == [
        "Victron (ESS): Netz-Sollwert von außen geändert (kein Override statt 200 W)."
    ]


def test_alarm_for_the_current() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])
    assert detector.alarms(at(25), LABELS) == [
        Alarm(
            "competitor.foreign.wallbox.evcs",
            Severity.ERROR,
            "EVCS „evcs“: Strom von außen geändert (10 A statt 6 A).",
            at(20),
        )
    ]


def test_alarm_numbers_are_written_the_german_way() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, 200.0)
    for i in range(3):
        detector.check({CUR: 7.5, VSET: -300.0}, frozenset(), at(10 * i))
    assert sorted(a.message for a in detector.alarms(at(20), LABELS)) == [
        "EVCS „evcs“: Strom von außen geändert (7,5 A statt 6 A).",
        "Victron (ESS): Netz-Sollwert von außen geändert (\N{MINUS SIGN}300 W statt 200 W).",
    ]


def test_alarm_text_survives_a_value_that_is_no_number() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, None)  # kein sinnvoller Strom, aber kein Absturz
    checks(detector, [10, 10, 10])
    assert [a.message for a in detector.alarms(at(20), LABELS)] == [
        "EVCS „evcs“: Strom von außen geändert (10 A statt unbekannt)."
    ]


def test_alarm_without_label_names_the_actuator() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])
    assert [a.message for a in detector.alarms(at(20), {})] == [
        "wallbox.evcs: Strom von außen geändert (10 A statt 6 A)."
    ]


def test_alarm_lasts_hold_s_from_the_detection() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])  # Erkennung bei 20 s
    assert len(detector.alarms(at(20), LABELS)) == 1
    assert len(detector.alarms(at(79), LABELS)) == 1
    assert detector.alarms(at(80), LABELS) == []


def test_hold_time_is_configurable() -> None:
    detector = ForeignChangeDetector(5.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])
    assert len(detector.alarms(at(24), LABELS)) == 1
    assert detector.alarms(at(25), LABELS) == []


def test_there_is_one_alarm_per_actuator_from_its_latest_detection() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    for seconds in (0, 10, 20):
        detector.check({CUR: 10}, frozenset(), at(seconds))
    for seconds in (30, 40, 50):
        detector.check({CUR: 12}, frozenset(), at(seconds))
    assert [(a.message, a.since) for a in detector.alarms(at(50), LABELS)] == [
        ("EVCS „evcs“: Strom von außen geändert (12 A statt 10 A).", at(50))
    ]
    assert detector.alarms(at(109), LABELS) != [] and detector.alarms(at(110), LABELS) == []


def test_forget_keeps_the_alarm_and_the_lock_cause_until_hold_s(limits) -> None:
    # Die Laufzeit vergisst den Aktor, sobald die Sperre ihm „owned“ nimmt. Der Alarm, der die
    # Sperre trägt, muss das überstehen: Er besteht bis ``hold_s`` nach der Erkennung.
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])  # Erkennung bei 20 s
    detector.forget(EVCS)
    text = "EVCS „evcs“: Strom von außen geändert (10 A statt 6 A)."
    for seconds in (20, 50, 79):
        alarms = detector.alarms(at(seconds), LABELS)
        assert [(a.id, a.message, a.since) for a in alarms] == [
            ("competitor.foreign.wallbox.evcs", text, at(20))
        ]
        assert lock_causes(alarms, [], limits) == {EVCS: [text]}
    assert detector.alarms(at(80), LABELS) == []
    assert lock_causes(detector.alarms(at(80), LABELS), [], limits) == {}


def test_forget_drops_the_expectation_and_the_counter() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 10]) == []
    detector.forget(EVCS)
    assert checks(detector, [10, 10, 10, 10]) == []  # nicht mehr beobachtet
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)  # und mit neuer Erwartung von vorn
    assert checks(detector, [10, 10]) == []
    assert len(checks(detector, [10])) == 1


def test_forget_keeps_the_other_actuators() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, None)
    detector.forget(EVCS)
    detector.forget("unbekannt")  # ohne Wirkung
    found = [detector.check({VSET: 500.0}, frozenset(), at(10 * i)) for i in range(3)]
    assert [len(f) for f in found] == [0, 0, 1]


@pytest.mark.parametrize("kind", sorted(WATCHED_KINDS))
def test_every_watched_kind_has_an_alarm_text(kind) -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("a", kind, 10.0)
    for i in range(3):
        detector.check({("a", kind): 20.0}, frozenset(), at(10 * i))
    assert len(detector.alarms(at(20), {})) == 1
