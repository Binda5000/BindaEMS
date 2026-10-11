import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from tests.helpers import T0

from bindaems.core.control.intent import INTENT_FILE_PROBLEM, IntentReply, IntentStore
from bindaems.core.persistence.files import read_json
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import ActuatorMode, Intent

LIVE, DRY = ActuatorMode.LIVE, ActuatorMode.DRY_RUN

EVCS_CLAMPED = (
    "EVCS „evcs“: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."
)
VICTRON_CLAMPED = (
    "Victron (ESS): in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."
)
TESLA_CLAMPED = (
    "Tesla Model 3: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."
)


def make_store(tmp_path: Path, limits: HardLimits) -> IntentStore:
    store = IntentStore(tmp_path / "intent.json", limits)
    store.load()
    return store


def test_first_intent_accepted_and_saved(tmp_path, limits_live) -> None:
    store = make_store(tmp_path, limits_live)
    reply = store.submit(Intent(version=5, control=True, actuators={"wallbox.evcs": LIVE}), T0)
    assert reply == IntentReply("accepted", 5)
    assert read_json(tmp_path / "intent.json") == {
        "intent": {"version": 5, "control": True, "actuators": {"wallbox.evcs": "live"}},
        "accepted_at": T0.isoformat(),
    }


def test_live_without_permission_is_clamped(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, actuators={"wallbox.evcs": LIVE}), T0)
    assert reply == IntentReply("clamped", 1, (EVCS_CLAMPED,))
    assert store.current.actuators == {"wallbox.evcs": DRY}


def test_unknown_actuator_rejected(tmp_path, limits) -> None:
    reply = make_store(tmp_path, limits).submit(
        Intent(version=1, actuators={"wallbox.twc": LIVE}), T0
    )
    assert reply == IntentReply(
        "rejected", None, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"
    )


def test_older_version_rejected(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5), T0)
    assert store.submit(Intent(version=4, control=True), T0) == IntentReply(
        "rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"
    )


def test_same_version_same_content_accepted(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    assert store.submit(Intent(version=5, control=True), T0) == IntentReply("accepted", 5)


def test_resubmitting_clamped_intent_is_idempotent(tmp_path, limits, monkeypatch) -> None:
    store = make_store(tmp_path, limits)
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE})
    first = store.submit(intent, T0)
    assert first == IntentReply("clamped", 1, (EVCS_CLAMPED,))
    save = Mock()
    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", save)
    assert store.submit(intent, T0) == first
    save.assert_not_called()
    assert store.current.actuators == {"wallbox.evcs": DRY}


def test_same_version_other_content_rejected(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    assert store.submit(Intent(version=5), T0) == IntentReply(
        "rejected", 5, ("Version 5 ist bereits mit anderem Inhalt angenommen.",), "stale"
    )


def test_saved_intent_reloaded(tmp_path, limits) -> None:
    make_store(tmp_path, limits).submit(Intent(version=7, control=True), T0)
    again = make_store(tmp_path, limits)
    assert (again.current, again.load_problem) == (Intent(version=7, control=True), None)


def test_corrupt_file_starts_without_intent(tmp_path, limits) -> None:
    (tmp_path / "intent.json").write_text("{kaputt")
    store = make_store(tmp_path, limits)
    assert (store.current, store.load_problem) == (None, INTENT_FILE_PROBLEM)
    store.submit(Intent(version=1), T0)
    assert store.load_problem is None


def test_save_failure_keeps_intent_in_memory(tmp_path, limits, monkeypatch) -> None:
    monkeypatch.setattr(
        "bindaems.core.control.intent.write_json_atomic", Mock(side_effect=OSError("Platte voll"))
    )
    store = make_store(tmp_path, limits)
    assert store.submit(Intent(version=2, control=True), T0) == IntentReply(
        "accepted", 2, ("Absicht nicht gespeichert: Platte voll",)
    )
    assert store.current == Intent(version=2, control=True)


# Weitere Randfälle: Reihenfolge der Regeln, Klemmung, Speicherung und Datei


def test_newer_version_replaces_the_valid_intent(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    later = T0 + timedelta(minutes=1)
    assert store.submit(Intent(version=6), later) == IntentReply("accepted", 6)
    assert store.current == Intent(version=6)
    assert read_json(tmp_path / "intent.json") == {
        "intent": {"version": 6, "control": False, "actuators": {}},
        "accepted_at": later.isoformat(),
    }


def test_rejections_change_nothing(tmp_path, limits, monkeypatch) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    saved = read_json(tmp_path / "intent.json")
    save = Mock()
    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", save)
    replies = [
        store.submit(Intent(version=9, actuators={"wallbox.twc": LIVE}), T0),
        store.submit(Intent(version=4), T0),
        store.submit(Intent(version=5), T0),
    ]
    assert replies == [
        IntentReply("rejected", 5, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"),
        IntentReply("rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"),
        IntentReply(
            "rejected", 5, ("Version 5 ist bereits mit anderem Inhalt angenommen.",), "stale"
        ),
    ]
    save.assert_not_called()
    assert store.current == Intent(version=5, control=True)
    assert read_json(tmp_path / "intent.json") == saved


def test_unknown_actuators_are_listed_sorted_and_checked_first(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5), T0)
    # auch die Version 3 ist zu alt, aber unbekannte Aktoren werden zuerst geprüft
    intent = Intent(version=3, actuators={"zeta": LIVE, "wallbox.evcs": LIVE, "alpha": DRY})
    assert store.submit(intent, T0) == IntentReply(
        "rejected",
        5,
        ("Unbekannter Aktor „alpha“.", "Unbekannter Aktor „zeta“."),
        "unknown_actuator",
    )


def test_live_is_clamped_before_the_comparison(tmp_path, limits, monkeypatch) -> None:
    store = make_store(tmp_path, limits)
    assert store.submit(Intent(version=1, actuators={"wallbox.evcs": DRY}), T0) == IntentReply(
        "accepted", 1
    )
    save = Mock()
    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", save)
    # nach dem Klemmen derselbe Inhalt: eine Wiederholung, kein Konflikt
    assert store.submit(Intent(version=1, actuators={"wallbox.evcs": LIVE}), T0) == IntentReply(
        "clamped", 1, (EVCS_CLAMPED,)
    )
    save.assert_not_called()


def test_clamp_touches_only_actuators_without_permission_and_follows_the_catalog(
    tmp_path, limits
) -> None:
    catalog = dict(limits.actuators)
    catalog["wallbox.evcs"] = replace(catalog["wallbox.evcs"], live_allowed=True)
    store = make_store(tmp_path, replace(limits, actuators=catalog))
    # gesendet wird erst das Fahrzeug, der Katalog nennt Victron zuerst
    intent = Intent(
        version=1, actuators={"vehicle.tesla": LIVE, "wallbox.evcs": LIVE, "victron": LIVE}
    )
    assert store.submit(intent, T0) == IntentReply("clamped", 1, (VICTRON_CLAMPED, TESLA_CLAMPED))
    assert store.current.actuators == {"vehicle.tesla": DRY, "wallbox.evcs": LIVE, "victron": DRY}


def test_clamped_intent_is_saved_and_reloaded_as_clamped(tmp_path, limits) -> None:
    clamped = Intent(version=1, control=True, actuators={"wallbox.evcs": DRY})
    make_store(tmp_path, limits).submit(
        Intent(version=1, control=True, actuators={"wallbox.evcs": LIVE}), T0
    )
    assert read_json(tmp_path / "intent.json")["intent"]["actuators"] == {"wallbox.evcs": "dry_run"}
    assert make_store(tmp_path, limits).current == clamped


def test_file_problem_text_is_fixed() -> None:
    assert INTENT_FILE_PROBLEM == (
        "Gespeicherte Absicht unlesbar; der core startet ohne Absicht (Regelbetrieb aus)."
    )


def test_missing_file_is_no_problem_and_is_not_created(tmp_path, limits) -> None:
    with capture_logs() as logs:
        store = make_store(tmp_path, limits)
    assert (store.current, store.load_problem, logs) == (None, None, [])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        "",
        "null",
        "[]",
        "{}",
        '{"intent": null}',
        '{"intent": {"version": 0}}',
        '{"intent": {"version": 1, "unbekannt": 1}}',
        '{"intent": {"version": 1, "actuators": {"victron": "an"}}}',
    ],
    ids=["leer", "null", "Liste", "ohne-intent", "intent-null", "version-0", "fremdfeld", "modus"],
)
def test_unusable_file_content_starts_without_intent(tmp_path, limits, content: str) -> None:
    (tmp_path / "intent.json").write_text(content)
    with capture_logs() as logs:
        store = make_store(tmp_path, limits)
    assert (store.current, store.load_problem) == (None, INTENT_FILE_PROBLEM)
    assert [(e["log_level"], e["event"]) for e in logs] == [
        ("warning", "Gespeicherte Absicht unlesbar")
    ]


def test_path_that_cannot_be_read_starts_without_intent(tmp_path, limits) -> None:
    (tmp_path / "intent.json").mkdir()  # lesen scheitert mit einem OSError, nicht mit ValueError
    with capture_logs() as logs:
        store = make_store(tmp_path, limits)
    assert (store.current, store.load_problem) == (None, INTENT_FILE_PROBLEM)
    assert [(e["log_level"], e["event"]) for e in logs] == [
        ("warning", "Gespeicherte Absicht unlesbar")
    ]


def test_load_replaces_the_state_with_what_the_file_says(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=3, control=True), T0)
    (tmp_path / "intent.json").write_text("{kaputt")
    store.load()
    assert (store.current, store.load_problem) == (None, INTENT_FILE_PROBLEM)
    (tmp_path / "intent.json").unlink()
    store.load()
    assert (store.current, store.load_problem) == (None, None)


def test_save_failure_is_logged_and_listed_after_the_clamp_details(
    tmp_path, limits, monkeypatch
) -> None:
    monkeypatch.setattr(
        "bindaems.core.control.intent.write_json_atomic", Mock(side_effect=OSError("Platte voll"))
    )
    store = make_store(tmp_path, limits)
    with capture_logs() as logs:
        reply = store.submit(Intent(version=2, actuators={"wallbox.evcs": LIVE}), T0)
    assert reply == IntentReply(
        "clamped", 2, (EVCS_CLAMPED, "Absicht nicht gespeichert: Platte voll")
    )
    assert [(e["log_level"], e["event"], e["error"]) for e in logs] == [
        ("error", "Absicht nicht gespeichert", "Platte voll")
    ]


def test_saves_never_overlap(tmp_path, limits, monkeypatch) -> None:
    # die API ruft submit aus mehreren Threads; write_json_atomic verträgt nur einen Schreiber
    guard = threading.Lock()
    running = peak = 0  # gerade laufende und höchste Zahl gleichzeitiger Speicherungen

    def slow_save(path: Path, data: Any) -> None:
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with guard:
            running -= 1

    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", slow_save)
    store = make_store(tmp_path, limits)
    threads = [
        threading.Thread(target=store.submit, args=(Intent(version=version), T0))
        for version in range(1, 6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1
