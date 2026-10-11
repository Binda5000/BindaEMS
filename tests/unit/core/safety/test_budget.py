import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from tests.helpers import T0

from bindaems.core.persistence.files import read_json
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.types import Finding
from bindaems.shared.domain import Alarm, Severity

M = lambda m: T0 + timedelta(minutes=m)  # noqa: E731
TEXT = "Schreibbudget für persistente Einstellungen erschöpft (12/h, 100/Tag)."


def test_hour_limit(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        assert budget.check(M(i)) is None
        budget.record(M(i))
    assert budget.check(M(12)) == Finding("budget.exhausted", TEXT)
    assert budget.check(M(60) + timedelta(seconds=1)) is None


def test_day_limit(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 20, tmp_path / "b.json", T0)
    for i in [*range(12), *range(120, 128)]:
        budget.record(M(i))
    assert (
        budget.check(M(240)).text
        == "Schreibbudget für persistente Einstellungen erschöpft (12/h, 20/Tag)."
    )
    assert budget.check(M(24 * 60 + 12)) is None


def test_persists_across_restart(tmp_path: Path) -> None:
    first = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        first.record(M(i))
    assert PersistentWriteBudget(12, 100, tmp_path / "b.json", M(13)).check(M(13)) is not None


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert PersistentWriteBudget(12, 100, tmp_path / "b.json", T0).check(T0) is None


def test_unreadable_file_blocks_for_an_hour(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    assert budget.check(M(59)) is not None
    assert budget.alarms(M(59)) == [
        Alarm(
            "safety.write_budget_file",
            Severity.WARNING,
            "Datei des Schreibbudgets unlesbar; persistente Einstellungen bleiben 1 h gesperrt.",
            T0,
        )
    ]
    assert budget.check(M(61)) is None
    assert budget.alarms(M(61)) == []


def test_exhausted_alarm_until_capacity_returns(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        budget.record(M(i))
    budget.note_rejection(M(12))
    assert budget.alarms(M(30)) == [
        Alarm(
            "safety.write_budget",
            Severity.WARNING,
            "Schreibbudget für persistente Einstellungen erschöpft.",
            M(12),
        )
    ]
    assert budget.alarms(M(61)) == []


def test_old_entries_dropped_from_file(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    budget.record(T0)
    later = T0 + timedelta(hours=25)
    budget.record(later)
    assert read_json(tmp_path / "b.json") == {"writes": [later.isoformat()]}


# Weitere Randfälle

S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
UNREADABLE_LOG = "Datei des Schreibbudgets unlesbar"
UNWRITTEN_LOG = "Datei des Schreibbudgets nicht geschrieben"
EXHAUSTED_ALARM = "Schreibbudget für persistente Einstellungen erschöpft."
FILE_ALARM = "Datei des Schreibbudgets unlesbar; persistente Einstellungen bleiben 1 h gesperrt."


def new_budget(
    tmp_path: Path, per_hour: int = 12, per_day: int = 100, now: datetime = T0
) -> PersistentWriteBudget:
    return PersistentWriteBudget(per_hour, per_day, tmp_path / "b.json", now)


def fill(budget: PersistentWriteBudget, count: int = 12) -> None:
    """Je eine Minute Abstand ab T0; mit den Standardlimits (12/h) ist das Budget dann erschöpft."""
    for i in range(count):
        budget.record(M(i))


# Fenster und Grenzen


def test_hour_window_is_half_open(tmp_path: Path) -> None:
    budget = new_budget(tmp_path, per_hour=2)
    budget.record(T0)
    budget.record(T0)
    assert budget.check(S(3599)) is not None
    assert budget.check(S(3600)) is None


def test_day_window_is_half_open(tmp_path: Path) -> None:
    budget = new_budget(tmp_path, per_day=2)
    budget.record(T0)
    budget.record(T0)
    assert budget.check(S(86399)) == Finding(
        "budget.exhausted", "Schreibbudget für persistente Einstellungen erschöpft (12/h, 2/Tag)."
    )
    assert budget.check(S(86400)) is None


@pytest.mark.parametrize(
    ("per_hour", "per_day", "limits"),
    [(0, 100, "0/h, 100/Tag"), (12, 0, "12/h, 0/Tag")],
)
def test_a_limit_of_zero_blocks_everything(
    tmp_path: Path, per_hour: int, per_day: int, limits: str
) -> None:
    finding = new_budget(tmp_path, per_hour, per_day).check(T0)
    assert finding == Finding(
        "budget.exhausted", f"Schreibbudget für persistente Einstellungen erschöpft ({limits})."
    )


@pytest.mark.parametrize("ahead_min", [10, 25 * 60], ids=["10-min", "25-h"])
def test_entries_after_now_still_count(tmp_path: Path, ahead_min: int) -> None:
    budget = new_budget(tmp_path, per_hour=1)
    budget.record(M(ahead_min))
    assert budget.check(M(0)) is not None


# Datei: Format, Laden und Schreiben


def test_missing_file_is_silent_and_is_not_created(tmp_path: Path) -> None:
    with capture_logs() as logs:
        budget = new_budget(tmp_path)
        assert budget.check(T0) is None
    assert (logs, budget.alarms(T0), list(tmp_path.iterdir())) == ([], [], [])


def test_file_holds_the_writes_as_iso_times_in_utc(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    budget.record(T0)
    budget.record((T0 + timedelta(minutes=5)).astimezone(timezone(timedelta(hours=2))))
    assert read_json(tmp_path / "b.json") == {
        "writes": ["2026-10-08T10:00:00+00:00", "2026-10-08T10:05:00+00:00"]
    }


def test_naive_time_is_refused_and_not_counted(tmp_path: Path) -> None:
    budget = new_budget(tmp_path, per_hour=1)
    with pytest.raises(ValueError, match="naiv"):
        budget.record(datetime(2026, 10, 8, 10, 0))
    assert not (tmp_path / "b.json").exists()
    assert budget.check(T0) is None


def test_record_drops_exactly_the_entries_that_no_longer_count(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    budget.record(T0)
    budget.record(S(86399))
    assert read_json(tmp_path / "b.json") == {"writes": [T0.isoformat(), S(86399).isoformat()]}
    budget.record(S(86400))
    assert read_json(tmp_path / "b.json") == {
        "writes": [S(86399).isoformat(), S(86400).isoformat()]
    }


def test_empty_writes_list_is_a_valid_empty_budget(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text('{"writes": []}')
    with capture_logs() as logs:
        budget = new_budget(tmp_path, per_hour=1)
    assert (logs, budget.check(T0), budget.alarms(T0)) == ([], None, [])


def test_any_iso_time_with_an_offset_is_accepted(tmp_path: Path) -> None:
    written = {"writes": ["2026-10-08T10:00:00Z", "2026-10-08T12:00:00+02:00"]}  # beide 10:00 UTC
    (tmp_path / "b.json").write_text(json.dumps(written))
    budget = new_budget(tmp_path, per_hour=2)
    assert budget.check(M(1)) is not None
    assert budget.check(M(60)) is None


def test_write_failure_is_logged_and_the_counter_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll"))
    )
    budget = new_budget(tmp_path, per_hour=2)
    with capture_logs() as logs:
        budget.record(M(0))
        budget.record(M(1))
    assert [(e["log_level"], e["event"]) for e in logs] == [("error", UNWRITTEN_LOG)] * 2
    assert budget.check(M(2)) is not None
    assert list(tmp_path.iterdir()) == []


def test_next_successful_write_brings_the_file_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = new_budget(tmp_path)
    with monkeypatch.context() as failing:
        failing.setattr(
            "bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll"))
        )
        budget.record(M(0))
    budget.record(M(1))
    assert read_json(tmp_path / "b.json") == {"writes": [M(0).isoformat(), M(1).isoformat()]}


# Unlesbare Datei


UNREADABLE = [
    pytest.param(b"kaputt", id="kein-json"),
    pytest.param(b"", id="leer"),
    pytest.param(b"\xff\xfe\x00", id="keine-utf8-kodierung"),
    pytest.param(b"[]", id="liste-statt-objekt"),
    pytest.param(b"null", id="null"),
    pytest.param(b"{}", id="writes-fehlt"),
    pytest.param(b'{"writes": {}}', id="writes-objekt-statt-liste"),
    pytest.param(b'{"writes": ""}', id="writes-leerer-text"),
    pytest.param(b'{"writes": [1]}', id="eintrag-keine-zeichenkette"),
    pytest.param(b'{"writes": ["gestern"]}', id="eintrag-keine-zeit"),
    pytest.param(b'{"writes": ["2026-10-08T10:00:00"]}', id="eintrag-ohne-zeitzone"),
    pytest.param(b'{"writes": [], "mehr": 1}', id="fremder-schluessel"),
]


@pytest.mark.parametrize("content", UNREADABLE)
def test_unusable_file_blocks_logs_an_error_and_stays_untouched(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "b.json").write_bytes(content)
    with capture_logs() as logs:
        budget = new_budget(tmp_path, now=M(5))
    assert [(e["log_level"], e["event"]) for e in logs] == [("error", UNREADABLE_LOG)]
    assert budget.check(M(64)) == Finding("budget.exhausted", TEXT)
    assert [a.id for a in budget.alarms(M(64))] == ["safety.write_budget_file"]
    assert (tmp_path / "b.json").read_bytes() == content


def test_a_path_that_cannot_be_read_as_a_file_blocks_too(tmp_path: Path) -> None:
    (tmp_path / "b.json").mkdir()
    with capture_logs() as logs:
        budget = new_budget(tmp_path)
    assert [e["event"] for e in logs] == [UNREADABLE_LOG]
    assert budget.check(T0) is not None


def test_block_lasts_one_hour_from_loading(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = new_budget(tmp_path, now=M(5))
    assert budget.check(S(65 * 60 - 1)) is not None
    assert budget.alarms(S(65 * 60 - 1)) == [
        Alarm("safety.write_budget_file", Severity.WARNING, FILE_ALARM, M(5))
    ]
    assert budget.check(M(65)) is None
    assert budget.alarms(M(65)) == []


def test_first_record_after_the_block_rewrites_the_file(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = new_budget(tmp_path)
    assert budget.check(M(61)) is None
    budget.record(M(61))
    assert read_json(tmp_path / "b.json") == {"writes": [M(61).isoformat()]}
    with capture_logs() as logs:
        reloaded = new_budget(tmp_path, per_hour=1, now=M(62))
    assert (logs, reloaded.alarms(M(62))) == ([], [])
    assert reloaded.check(M(62)) is not None  # der eine Eintrag wurde geladen


def test_record_during_the_block_does_not_end_it(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = new_budget(tmp_path)
    budget.record(M(10))
    assert read_json(tmp_path / "b.json") == {"writes": [M(10).isoformat()]}
    assert budget.check(M(11)) is not None
    assert [a.id for a in budget.alarms(M(11))] == ["safety.write_budget_file"]


# Alarm „Budget erschöpft“


def test_no_alarm_without_a_rejection(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    fill(budget)
    assert budget.check(M(12)) is not None
    assert budget.alarms(M(12)) == []


def test_alarm_starts_at_the_first_rejection(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    fill(budget)
    budget.note_rejection(M(12))
    budget.note_rejection(M(13))
    assert budget.alarms(M(14)) == [
        Alarm("safety.write_budget", Severity.WARNING, EXHAUSTED_ALARM, M(12))
    ]


def test_a_rejection_while_capacity_is_left_raises_no_alarm(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    budget.note_rejection(T0)
    assert budget.alarms(T0) == []


@pytest.mark.parametrize(
    "observe",
    [
        pytest.param(lambda budget: budget.alarms(M(61)), id="alarms"),
        pytest.param(lambda budget: budget.check(M(61)), id="check"),
    ],
)
def test_a_new_exhaustion_starts_a_new_alarm(tmp_path: Path, observe) -> None:
    budget = new_budget(tmp_path, per_hour=2)
    budget.record(M(0))
    budget.record(M(1))
    budget.note_rejection(M(2))
    observe(budget)  # M(0) und M(1) sind jetzt 61 und 60 Minuten alt: die Kapazität ist zurück
    budget.record(M(61))
    budget.record(M(62))
    budget.note_rejection(M(63))
    assert [a.since for a in budget.alarms(M(64))] == [M(63)]


def test_recording_while_exhausted_keeps_the_alarm(tmp_path: Path) -> None:
    budget = new_budget(tmp_path)
    fill(budget)
    budget.note_rejection(M(12))
    budget.record(M(13))  # ein Befehl, der das Budget umgeht, wird trotzdem mitgezählt
    assert [a.since for a in budget.alarms(M(14))] == [M(12)]


def test_both_alarms_come_in_the_order_of_the_table(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = new_budget(tmp_path, now=M(1))
    budget.note_rejection(M(2))
    assert [(a.id, a.since) for a in budget.alarms(M(3))] == [
        ("safety.write_budget", M(2)),
        ("safety.write_budget_file", M(1)),
    ]
