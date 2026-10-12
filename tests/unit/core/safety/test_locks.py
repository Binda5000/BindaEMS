from datetime import timedelta

import pytest
from tests.helpers import T0

from bindaems.core.checks.selfcheck import CheckResult
from bindaems.core.safety.locks import LockInfo, LockManager, lock_causes
from bindaems.shared.domain import Alarm, Severity

S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
DESS = "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern."
EVCS = "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."
PLAN = "Ladeplan im Fahrzeug „Tesla Model 3“ ist aktiv."


def alarm(id_: str, message: str) -> Alarm:
    return Alarm(id_, Severity.ERROR, message, T0)


def test_competitors_and_selfcheck_map_to_actuators(limits) -> None:
    competitors = [
        alarm("competitor.dess", DESS),
        alarm("competitor.evcs_mode.evcs", EVCS),
        alarm("competitor.vehicle_schedule.tesla", PLAN),
    ]
    selfcheck = [
        CheckResult("ess_mode", "fail", "ESS-Modus 3, erwartet 1."),
        CheckResult("dess", "fail", "Dynamic ESS ist aktiv (Modus 1)."),
        CheckResult("schedules", "unknown", "Ladefenster nicht verfügbar."),
    ]
    assert lock_causes(competitors, selfcheck, limits) == {
        "victron": [DESS, "ESS-Modus 3, erwartet 1.", "Ladefenster nicht verfügbar."],
        "wallbox.evcs": [EVCS],
        "vehicle.tesla": [PLAN],
    }


def test_warn_ok_and_core_checks_do_not_lock(limits) -> None:
    selfcheck = [
        CheckResult("min_soc", "warn", "Victron-Min-SOC 30 % liegt über der USV-Reserve 20 %."),
        CheckResult("phases", "fail", "Erwartet 3 Phasen, gemeldet 2."),
        CheckResult("fresh_data", "fail", "Nicht aktuell: grid.l1.power_w."),
        CheckResult("batterylife", "ok", "BatteryLife-Zustand 10 wie erwartet."),
    ]
    assert lock_causes([], selfcheck, limits) == {}


def test_non_actuators_are_ignored(limits) -> None:
    egolf = alarm("competitor.vehicle_schedule.egolf", "Ladeplan im Fahrzeug „e-Golf“ ist aktiv.")
    assert lock_causes([egolf], [], limits) == {}


def test_lock_since_first_cause() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {"victron": ["B"]})
    assert locks.active(S(10)) == {"victron": LockInfo(("B",), T0, None)}


def test_cause_must_be_absent_60s_before_release() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    assert locks.active(S(69)) == {"victron": LockInfo(("A",), T0, S(10))}
    assert locks.active(S(70)) == {}


def test_returning_cause_restarts_the_release_wait() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    locks.update(S(40), {"victron": ["A"]})
    locks.update(S(50), {})
    assert "victron" in locks.active(S(105))
    assert locks.active(S(110)) == {}


def test_release_time_is_configurable() -> None:
    locks = LockManager(5.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(1), {})
    assert locks.active(S(6)) == {}


# Weitere Fälle: die übrigen Zeilen der Regeltabelle (Spec 7.9) und Zusagen, die die Tests oben
# nicht berühren.


def test_schedule_alarms_lock_victron_in_alarm_order(limits) -> None:
    window_0 = alarm("competitor.schedule.0", "Victron-Ladefenster 0 ist aktiv.")
    window_3 = alarm("competitor.schedule.3", "Victron-Ladefenster 3 ist aktiv.")
    assert lock_causes([window_0, alarm("competitor.dess", DESS), window_3], [], limits) == {
        "victron": [
            "Victron-Ladefenster 0 ist aktiv.",
            DESS,
            "Victron-Ladefenster 3 ist aktiv.",
        ]
    }


def test_other_alarms_do_not_lock(limits) -> None:
    others = [
        alarm("plaus.range.grid", "Netzleistung außerhalb des Bereichs."),
        alarm("safety.write_budget", "Schreibbudget erschöpft."),
        alarm("competitor.unbekannt", "Etwas Fremdes."),
    ]
    assert lock_causes(others, [], limits) == {}


@pytest.mark.parametrize(
    ("check_id", "status", "locks"),
    [
        ("ess_mode", "fail", "victron"),
        ("ess_mode", "unknown", "victron"),
        ("batterylife", "fail", "victron"),
        ("batterylife", "unknown", "victron"),
        ("min_soc", "fail", "victron"),
        ("min_soc", "unknown", "victron"),
        ("dess", "unknown", "victron"),
        ("schedules", "unknown", "victron"),
        ("evcs_mode.evcs", "unknown", "wallbox.evcs"),
        # ein fail steckt schon im Alarm des mitregelnden Systems
        ("dess", "fail", None),
        ("schedules", "fail", None),
        ("evcs_mode.evcs", "fail", None),
        # ok und warn sperren nie, die Kernprüfungen auch dann nicht, wenn sie unbekannt sind
        ("ess_mode", "ok", None),
        ("ess_mode", "warn", None),
        ("evcs_mode.evcs", "ok", None),
        ("phases", "unknown", None),
        ("fresh_data", "unknown", None),
    ],
)
def test_selfcheck_rows_of_the_rule_table(limits, check_id, status, locks) -> None:
    result = lock_causes([], [CheckResult(check_id, status, "Meldung.")], limits)
    assert result == ({} if locks is None else {locks: ["Meldung."]})


def test_foreign_change_alarms_lock_the_named_actuator(limits) -> None:
    evcs = "EVCS „evcs“: Strom von außen geändert (10 A statt 6 A)."
    victron = "Victron (ESS): Netz-Sollwert von außen geändert (500 W statt kein Override)."
    tesla = "Tesla Model 3: Strom von außen geändert (10 A statt 6 A)."
    competitors = [
        alarm("competitor.foreign.wallbox.evcs", evcs),
        alarm("competitor.foreign.victron", victron),
        alarm("competitor.foreign.vehicle.tesla", tesla),
    ]
    assert lock_causes(competitors, [], limits) == {
        "wallbox.evcs": [evcs],
        "victron": [victron],
        "vehicle.tesla": [tesla],
    }


def test_foreign_change_of_something_that_is_no_actuator_is_ignored(limits) -> None:
    text = "Strom von außen geändert (10 A statt 6 A)."
    others = [
        alarm("competitor.foreign.wallbox.twc", text),  # der Wall Connector ist kein Aktor
        alarm("competitor.foreign.vehicle.egolf", text),  # ein Fahrzeug ohne Tessie auch nicht
        alarm("competitor.foreign.unbekannt", text),
        alarm("competitor.foreign.", text),
    ]
    assert lock_causes(others, [], limits) == {}


def test_foreign_change_joins_the_other_causes_of_the_actuator(limits) -> None:
    foreign = "Victron (ESS): Netz-Sollwert von außen geändert (500 W statt kein Override)."
    competitors = [alarm("competitor.dess", DESS), alarm("competitor.foreign.victron", foreign)]
    selfcheck = [CheckResult("min_soc", "fail", "Victron-Min-SOC 5 % liegt unter der Reserve.")]
    assert lock_causes(competitors, selfcheck, limits) == {
        "victron": [DESS, foreign, "Victron-Min-SOC 5 % liegt unter der Reserve."]
    }


def test_equal_texts_appear_once_per_actuator(limits) -> None:
    text = "Ladefenster nicht verfügbar."
    competitors = [alarm("competitor.schedule.1", text)]
    selfcheck = [
        CheckResult("schedules", "unknown", text),
        CheckResult("min_soc", "unknown", text),
        CheckResult("evcs_mode.evcs", "unknown", text),
    ]
    assert lock_causes(competitors, selfcheck, limits) == {
        "victron": [text],
        "wallbox.evcs": [text],
    }


def test_lock_with_cause_never_expires() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    assert locks.active(S(86_400)) == {"victron": LockInfo(("A",), T0, None)}


def test_cleared_at_stays_at_the_first_absence() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    locks.update(S(20), {})
    assert locks.active(S(30)) == {"victron": LockInfo(("A",), T0, S(10))}


def test_returning_cause_keeps_since_and_updates_reasons() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    locks.update(S(20), {"victron": ["B", "C"]})
    assert locks.active(S(20)) == {"victron": LockInfo(("B", "C"), T0, None)}


def test_released_lock_starts_anew_with_a_new_since() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    assert locks.active(S(70)) == {}
    locks.update(S(80), {"victron": ["A"]})
    assert locks.active(S(80)) == {"victron": LockInfo(("A",), S(80), None)}


def test_cause_returning_before_active_ran_keeps_the_lock() -> None:
    # Die Wartezeit ist abgelaufen, aber ``active`` hat die Sperre noch nicht entfernt: im Zweifel
    # bleibt der Aktor gesperrt.
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    locks.update(S(80), {"victron": ["A"]})
    assert locks.active(S(80)) == {"victron": LockInfo(("A",), T0, None)}


def test_actuators_are_locked_and_released_independently() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"], "wallbox.evcs": ["B"]})
    locks.update(S(10), {"wallbox.evcs": ["B"]})
    assert locks.active(S(70)) == {"wallbox.evcs": LockInfo(("B",), T0, None)}


def test_empty_cause_list_counts_as_no_cause() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {"victron": [], "wallbox.evcs": []})
    assert locks.active(S(10)) == {"victron": LockInfo(("A",), T0, S(10))}
