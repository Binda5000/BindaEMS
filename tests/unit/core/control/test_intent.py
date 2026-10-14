import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs
from tests.helpers import INTENT_CHARGING_DEFAULTS, T0

from bindaems.core.control.intent import (
    BOOST_MAX,
    INTENT_FILE_PROBLEM,
    IntentReply,
    IntentStore,
)
from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.control import (
    ActuatorMode,
    ChargeMode,
    GuestIntent,
    Intent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
)

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
        "intent": {
            "version": 5,
            "control": True,
            "actuators": {"wallbox.evcs": "live"},
        }
        | INTENT_CHARGING_DEFAULTS,
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
        "intent": {"version": 6, "control": False, "actuators": {}} | INTENT_CHARGING_DEFAULTS,
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
        store.submit(Intent(version=9, vehicles={"golf": VehicleIntent()}), T0),
    ]
    assert replies == [
        IntentReply("rejected", 5, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"),
        IntentReply("rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"),
        IntentReply(
            "rejected", 5, ("Version 5 ist bereits mit anderem Inhalt angenommen.",), "stale"
        ),
        IntentReply("rejected", 5, ("Unbekanntes Fahrzeug „golf“.",), "invalid"),
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


# Absicht für die Laderegelung (Plan 2c-1): Fahrzeuge, Wallboxen, Priorität, Boost und Annahmezeit

VEHICLES = {"tesla": "Tesla Model 3", "egolf": "e-Golf"}
BOOST_CLAMPED = "Tesla Model 3: Boost auf 12 h begrenzt, bis 00:00."  # für T0 + 12 h in Wien
PRIORITY_INVALID = "Die Priorität muss jedes Fahrzeug genau einmal nennen."


def charging_store(tmp_path, limits) -> IntentStore:
    path = tmp_path / "intent.json"
    return IntentStore(path, limits, vehicles=VEHICLES, wallboxes=("evcs", "twc"))


def test_invalid_vehicles_wallboxes_and_priority_are_rejected(tmp_path, limits) -> None:
    intent = Intent(
        version=1,
        vehicles={
            "golf": VehicleIntent(),
            "tesla": VehicleIntent(wallbox="garage"),
        },
        priority=["tesla"],
    )
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    assert (reply.result, reply.rejection, reply.version) == ("rejected", "invalid", None)
    assert reply.details == (
        "Unbekanntes Fahrzeug „golf“.",
        "Unbekannte Wallbox „garage“ für „tesla“.",
        "Die Priorität muss jedes Fahrzeug genau einmal nennen.",
    )


def test_one_wallbox_for_two_vehicles_is_rejected(tmp_path, limits) -> None:
    intent = Intent(
        version=1,
        vehicles={
            "tesla": VehicleIntent(wallbox="evcs"),
            "egolf": VehicleIntent(wallbox="evcs"),
        },
    )
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    assert reply.details == ("Wallbox „evcs“ ist mehreren Fahrzeugen zugeordnet.",)


def test_unknown_vehicles_and_wallboxes_are_listed_sorted(tmp_path, limits) -> None:
    intent = Intent(
        version=1,
        vehicles={
            "zeta": VehicleIntent(),
            "tesla": VehicleIntent(wallbox="x1"),
            "alpha": VehicleIntent(),
            "egolf": VehicleIntent(wallbox="x2"),
        },
    )
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    # erst die Fahrzeuge nach Schlüssel, dann die Wallboxen nach Fahrzeug (nicht nach Wallbox)
    assert reply.details == (
        "Unbekanntes Fahrzeug „alpha“.",
        "Unbekanntes Fahrzeug „zeta“.",
        "Unbekannte Wallbox „x2“ für „egolf“.",
        "Unbekannte Wallbox „x1“ für „tesla“.",
    )


def test_shared_wallboxes_are_listed_sorted(tmp_path, limits) -> None:
    store = IntentStore(
        tmp_path / "intent.json",
        limits,
        vehicles={"a": "A", "b": "B", "c": "C", "d": "D"},
        wallboxes=("evcs", "twc"),
    )
    intent = Intent(
        version=1,
        vehicles={
            "a": VehicleIntent(wallbox="twc"),
            "b": VehicleIntent(wallbox="twc"),
            "c": VehicleIntent(wallbox="evcs"),
            "d": VehicleIntent(wallbox="evcs"),
        },
    )
    assert store.submit(intent, T0).details == (
        "Wallbox „evcs“ ist mehreren Fahrzeugen zugeordnet.",
        "Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet.",
    )


@pytest.mark.parametrize(
    "priority",
    [[], ["tesla"], ["tesla", "tesla"], ["tesla", "egolf", "egolf"], ["tesla", "egolf", "golf"]],
    ids=["leer", "unvollstaendig", "doppelt", "doppelt-und-vollstaendig", "unbekannt"],
)
def test_priority_not_naming_every_vehicle_once_is_rejected(tmp_path, limits, priority) -> None:
    reply = charging_store(tmp_path, limits).submit(Intent(version=1, priority=priority), T0)
    assert reply == IntentReply("rejected", None, (PRIORITY_INVALID,), "invalid")


def test_priority_in_any_order_is_accepted(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    assert store.submit(Intent(version=1, priority=["egolf", "tesla"]), T0) == IntentReply(
        "accepted", 1
    )
    assert store.current.priority == ["egolf", "tesla"]


def test_invalid_comes_after_unknown_actuators_and_before_the_version(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    store.submit(Intent(version=5), T0)
    both = Intent(version=3, actuators={"wallbox.twc": LIVE}, vehicles={"golf": VehicleIntent()})
    assert store.submit(both, T0) == IntentReply(
        "rejected", 5, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"
    )
    # auch die Version 3 ist zu alt, aber der Inhalt wird zuerst geprüft
    old = Intent(version=3, vehicles={"golf": VehicleIntent()})
    assert store.submit(old, T0) == IntentReply(
        "rejected", 5, ("Unbekanntes Fahrzeug „golf“.",), "invalid"
    )
    assert store.current == Intent(version=5)


def test_charging_intent_is_accepted_saved_and_reloaded(tmp_path, limits) -> None:
    intent = Intent(
        version=2,
        control=True,
        vehicles={
            "tesla": VehicleIntent(
                mode=ChargeMode.NOW, boost_until=T0 + timedelta(hours=3), wallbox="evcs"
            ),
            "egolf": VehicleIntent(mode=ChargeMode.MIN_PV),
        },
        priority=["egolf", "tesla"],
        surplus_priority=SurplusPriority(kind=SurplusOrder.CARS_FIRST, soc_pct=50.0),
        guest=GuestIntent(mode=ChargeMode.MIN_PV),
    )
    store = charging_store(tmp_path, limits)
    assert store.submit(intent, T0) == IntentReply("accepted", 2)
    assert store.current == intent
    assert read_json(tmp_path / "intent.json") == {
        "intent": {
            "version": 2,
            "control": True,
            "actuators": {},
            "vehicles": {
                "tesla": {"mode": "now", "boost_until": "2026-10-08T13:00:00Z", "wallbox": "evcs"},
                "egolf": {"mode": "min_pv", "boost_until": None, "wallbox": None},
            },
            "priority": ["egolf", "tesla"],
            "surplus_priority": {"kind": "cars_first", "soc_pct": 50.0},
            "guest": {"mode": "min_pv"},
        },
        "accepted_at": T0.isoformat(),
    }
    again = charging_store(tmp_path, limits)
    again.load()
    assert (again.current, again.accepted_at, again.load_problem) == (intent, T0, None)


def test_boost_limit_is_12_hours() -> None:
    assert BOOST_MAX == timedelta(hours=12)


def test_boost_beyond_12_hours_is_clamped(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))}
    )
    reply = store.submit(intent, T0)  # T0 = 12:00 Wiener Zeit
    assert (reply.result, reply.details) == ("clamped", (BOOST_CLAMPED,))
    assert store.current.vehicles["tesla"].boost_until == T0 + timedelta(hours=12)
    assert store.accepted_at == T0


@pytest.mark.parametrize(
    "offset",
    [timedelta(hours=12), timedelta(hours=1), timedelta(hours=-1)],
    ids=["genau-12-h", "kurz", "vergangenheit"],
)
def test_boost_up_to_12_hours_or_in_the_past_is_kept(tmp_path, limits, offset) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + offset)})
    assert store.submit(intent, T0) == IntentReply("accepted", 1)  # kein Fehler, kein Hinweis
    assert store.current.vehicles["tesla"].boost_until == T0 + offset


def test_only_the_boost_beyond_12_hours_is_clamped(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    soon = T0 + timedelta(hours=1)
    intent = Intent(
        version=1,
        vehicles={
            "egolf": VehicleIntent(mode=ChargeMode.NOW, boost_until=soon),
            "tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20), wallbox="twc"),
        },
    )
    assert store.submit(intent, T0) == IntentReply("clamped", 1, (BOOST_CLAMPED,))
    assert store.current.vehicles == {
        "egolf": VehicleIntent(mode=ChargeMode.NOW, boost_until=soon),
        "tesla": VehicleIntent(boost_until=T0 + timedelta(hours=12), wallbox="twc"),
    }


def test_clamp_details_name_actuators_first_and_vehicles_in_config_order(tmp_path, limits) -> None:
    far = T0 + timedelta(hours=20)
    intent = Intent(
        version=1,
        actuators={"wallbox.evcs": LIVE},
        vehicles={"egolf": VehicleIntent(boost_until=far), "tesla": VehicleIntent(boost_until=far)},
    )
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    assert (reply.result, reply.details) == (
        "clamped",
        (EVCS_CLAMPED, BOOST_CLAMPED, "e-Golf: Boost auf 12 h begrenzt, bis 00:00."),
    )


def test_boost_hint_shows_vienna_time_across_the_end_of_summer_time(tmp_path, limits) -> None:
    # 22:00 Wiener Sommerzeit am 24.10.2026; 12 h später, nach der Umstellung, ist es 09:00
    received = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)
    intent = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=received + timedelta(hours=20))}
    )
    reply = charging_store(tmp_path, limits).submit(intent, received)
    assert reply.details == ("Tesla Model 3: Boost auf 12 h begrenzt, bis 09:00.",)


def test_a_new_version_clamps_the_boost_against_its_own_receipt(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    store.submit(Intent(version=1), T0)
    later = T0 + timedelta(hours=1)
    intent = Intent(
        version=2, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))}
    )
    assert store.submit(intent, later) == IntentReply(
        "clamped", 2, ("Tesla Model 3: Boost auf 12 h begrenzt, bis 01:00.",)
    )
    assert store.current.vehicles["tesla"].boost_until == later + timedelta(hours=12)
    assert store.accepted_at == later


def test_resubmitting_a_clamped_boost_later_is_a_repetition(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))}
    )
    store.submit(intent, T0)
    assert store.submit(intent, T0 + timedelta(minutes=5)).result == "clamped"
    again = charging_store(tmp_path, limits)
    again.load()
    assert again.accepted_at == T0
    assert again.submit(intent, T0 + timedelta(hours=1)).result == "clamped"


def test_repetition_of_a_clamped_boost_gives_the_same_reply_and_saves_nothing(
    tmp_path, limits, monkeypatch
) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))}
    )
    first = store.submit(intent, T0)
    save = Mock()
    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", save)
    # die Wiederholung klemmt gegen die Annahmezeit: auch „bis 00:00“ bleibt dasselbe
    assert store.submit(intent, T0 + timedelta(hours=5)) == first
    save.assert_not_called()
    assert store.accepted_at == T0


def test_accepted_at_follows_the_valid_intent(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    assert store.accepted_at is None
    store.submit(Intent(version=1), T0)
    assert store.accepted_at == T0
    later = T0 + timedelta(minutes=1)
    assert store.submit(Intent(version=1), later).result == "accepted"  # Wiederholung
    assert store.submit(Intent(version=1, control=True), later).result == "rejected"
    assert store.accepted_at == T0
    store.submit(Intent(version=2), later)
    assert store.accepted_at == later


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"accepted_at": "gestern"},
        {"accepted_at": 12},
        {"accepted_at": None},
        {"accepted_at": "2026-10-08T10:00:00"},
    ],
    ids=["fehlt", "kein-iso", "zahl", "null", "ohne-zeitzone"],
)
def test_missing_or_invalid_accepted_at_in_the_file_is_none(tmp_path, limits, extra) -> None:
    write_json_atomic(tmp_path / "intent.json", {"intent": {"version": 3}} | extra)
    store = make_store(tmp_path, limits)
    assert (store.current, store.accepted_at, store.load_problem) == (Intent(version=3), None, None)


def test_load_forgets_the_receipt_of_an_unreadable_file(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=3), T0)
    (tmp_path / "intent.json").write_text("{kaputt")
    store.load()
    assert (store.current, store.accepted_at) == (None, None)


def test_repetition_without_a_known_receipt_clamps_against_now(tmp_path, limits) -> None:
    stored = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=12))}
    )
    write_json_atomic(tmp_path / "intent.json", {"intent": stored.model_dump(mode="json")})
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.accepted_at is None
    sent = Intent(
        version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))}
    )
    # ohne Annahmezeit gilt „jetzt“ als Empfang: Der Inhalt weicht vom gespeicherten ab
    assert store.submit(sent, T0 + timedelta(hours=1)) == IntentReply(
        "rejected", 1, ("Version 1 ist bereits mit anderem Inhalt angenommen.",), "stale"
    )


def test_intent_file_from_2b_loads_with_defaults(tmp_path, limits) -> None:  # Review Focus 4
    write_json_atomic(
        tmp_path / "intent.json",
        {
            "intent": {"version": 3, "control": True, "actuators": {"wallbox.evcs": "dry_run"}},
            "accepted_at": T0.isoformat(),
        },
    )
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.load_problem is None
    assert (store.current.vehicles, store.current.priority) == ({}, None)
    assert store.current.surplus_priority == SurplusPriority()
    assert store.accepted_at == T0


def test_intent_file_from_2c1_loads_with_the_guest_default(tmp_path, limits) -> None:
    # so schrieb der core in Plan 2c-1: Lademodi, aber noch kein „guest“
    sent = {
        "version": 4,
        "control": True,
        "actuators": {"wallbox.evcs": "dry_run"},
        "vehicles": {
            "tesla": {"mode": "now", "boost_until": None, "wallbox": "evcs"},
            "egolf": {"mode": "min_pv", "boost_until": None, "wallbox": None},
        },
        "priority": ["egolf", "tesla"],
        "surplus_priority": {"kind": "cars_first", "soc_pct": 50.0},
    }
    write_json_atomic(tmp_path / "intent.json", {"intent": sent, "accepted_at": T0.isoformat()})
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.load_problem is None
    assert store.current.guest.mode is ChargeMode.PV
    assert (store.current.version, store.current.control) == (4, True)
    assert store.current.vehicles["tesla"].mode is ChargeMode.NOW
    assert store.accepted_at == T0
    # die app sendet dieselbe Absicht weiter ohne „guest“: eine Wiederholung, kein Konflikt
    resent = Intent.model_validate(sent)
    assert store.submit(resent, T0 + timedelta(minutes=1)) == IntentReply("accepted", 4)


def test_guest_mode_belongs_to_the_content_of_an_intent(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    assert store.submit(Intent(version=1, guest=GuestIntent(mode=ChargeMode.NOW)), T0) == (
        IntentReply("accepted", 1)
    )
    # dieselbe Version mit anderem Modus ist ein Konflikt, die nächste Version ersetzt ihn
    assert store.submit(Intent(version=1), T0) == IntentReply(
        "rejected", 1, ("Version 1 ist bereits mit anderem Inhalt angenommen.",), "stale"
    )
    assert store.submit(Intent(version=2), T0) == IntentReply("accepted", 2)
    assert store.current.guest.mode is ChargeMode.PV
