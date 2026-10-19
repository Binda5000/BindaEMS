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
    SupportIntent,
    SurplusOrder,
    SurplusPriority,
    VehicleIntent,
)
from bindaems.shared.plan import (
    PLAN_VALID,
    BatteryIntent,
    BatteryIntentKind,
    IntentPlan,
    IntentPlanSlot,
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


VEHICLE_MAX_W = {"tesla": 11040.0, "egolf": 7360.0}  # 16 A · 230 V · 3 bzw. 2 Phasen


def charging_store(tmp_path, limits) -> IntentStore:
    path = tmp_path / "intent.json"
    return IntentStore(
        path, limits, vehicles=VEHICLES, wallboxes=("evcs", "twc"), vehicle_max_w=VEHICLE_MAX_W
    )


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
                "tesla": {
                    "mode": "now",
                    "boost_until": "2026-10-08T13:00:00Z",
                    "wallbox": "evcs",
                    "target_soc_pct": 80,
                    "departure": "07:00",
                },
                "egolf": {
                    "mode": "min_pv",
                    "boost_until": None,
                    "wallbox": None,
                    "target_soc_pct": 80,
                    "departure": "07:00",
                },
            },
            "priority": ["egolf", "tesla"],
            "surplus_priority": {"kind": "cars_first", "soc_pct": 50.0},
            "guest": {"mode": "min_pv"},
            "plan": None,
            "support": {"allowed": True, "soc_pct": None},
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


# --- Plan und Unterstützung in der Absicht (Plan 3c, Präzisierung 4) ---

SLOTS_BROKEN = ("Plan: Slots nicht im Viertelstundenraster oder nicht lückenlos.",)
SLOTS_TOO_MANY = ("Plan: zu viele Slots (höchstens 8).",)
VALIDITY_BROKEN = ("Plan: Gültigkeit passt nicht zu den Slots.",)
GRID_CHARGE_OFF = ("Plan: Netzladen, obwohl ausgeschaltet.",)
CHARGE = BatteryIntentKind.NETZLADEN


def at(hhmm: str) -> datetime:
    """Der Zeitpunkt ``hhmm`` (UTC) am Tag von ``T0``."""
    hour, minute = hhmm.split(":")
    return T0.replace(hour=int(hour), minute=int(minute))


def plan_at(
    created: datetime,
    starts: list[str],
    *,
    valid_until: datetime | None = None,
    kinds: list[BatteryIntent] | None = None,
    grid_charge_allowed: bool = True,
) -> IntentPlan:
    """Ein Plan mit je einem Slot ab ``starts``; ohne Angabe gilt er 45 min, höchstens bis zum Ende
    des letzten Slots, und jeder Slot ist ``EIGENVERBRAUCH``."""
    begins = [at(start) for start in starts]
    if valid_until is None:
        valid_until = min(created + PLAN_VALID, begins[-1] + timedelta(minutes=15))
    intents = kinds or [BatteryIntent() for _ in begins]
    return IntentPlan(
        id=7,
        created_at=created,
        valid_until=valid_until,
        grid_charge_allowed=grid_charge_allowed,
        slots=[
            IntentPlanSlot(start=begin, intent=intent)
            for begin, intent in zip(begins, intents, strict=True)
        ],
    )


def grid_charge(watts: float) -> BatteryIntent:
    return BatteryIntent(kind=CHARGE, grid_charge_w=watts)


FOUR = ["10:00", "10:15", "10:30", "10:45"]


def test_plan_is_accepted_and_saved(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    plan = plan_at(at("10:01"), FOUR)
    assert store.submit(Intent(version=1, plan=plan), at("10:01")).result == "accepted"
    assert store.current.plan == plan
    assert read_json(tmp_path / "intent.json")["intent"]["plan"]["id"] == plan.id


def test_plan_with_eight_slots_and_the_longest_validity_is_accepted(tmp_path, limits) -> None:
    starts = ["10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45"]
    plan = plan_at(at("10:01"), starts, valid_until=at("10:46"))
    assert make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01")) == (
        IntentReply("accepted", 1)
    )


def test_gap_in_plan_slots_is_invalid(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), ["10:00", "10:30"])
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection) == ("rejected", "invalid")
    assert reply.details == SLOTS_BROKEN


@pytest.mark.parametrize(
    "starts",
    [
        pytest.param(["10:05", "10:20"], id="off-grid"),
        pytest.param(["10:15", "10:00"], id="descending"),
        pytest.param(["10:00", "10:00"], id="duplicate"),
    ],
)
def test_slots_off_the_grid_or_out_of_order_are_invalid(tmp_path, limits, starts) -> None:
    plan = plan_at(at("10:20"), starts, valid_until=at("10:30"))
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:20"))
    assert (reply.result, reply.rejection, reply.details) == ("rejected", "invalid", SLOTS_BROKEN)


def test_more_than_eight_slots_is_invalid(tmp_path, limits) -> None:
    starts = [f"{10 + n // 4}:{n % 4 * 15:02d}" for n in range(9)]
    plan = plan_at(at("10:01"), starts, valid_until=at("10:46"))
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection, reply.details) == ("rejected", "invalid", SLOTS_TOO_MANY)


@pytest.mark.parametrize(
    ("created", "valid_until"),
    [
        pytest.param("10:01", "11:01", id="beyond-the-last-slot"),
        pytest.param("10:20", "10:20", id="empty"),
        pytest.param("10:20", "10:10", id="ends-before-it-starts"),
        pytest.param("09:50", "10:20", id="created-before-the-first-slot"),
    ],
)
def test_validity_beyond_the_last_slot_or_out_of_order_is_invalid(
    tmp_path, limits, created, valid_until
) -> None:
    plan = plan_at(at(created), FOUR, valid_until=at(valid_until))
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:20"))
    assert (reply.result, reply.rejection, reply.details) == (
        "rejected",
        "invalid",
        VALIDITY_BROKEN,
    )


def test_validity_longer_than_45_min_is_invalid(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR, valid_until=at("10:46") + timedelta(minutes=1))
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection, reply.details) == (
        "rejected",
        "invalid",
        VALIDITY_BROKEN,
    )


def test_grid_charge_slot_with_switch_off_is_invalid(tmp_path, limits) -> None:
    kinds = [BatteryIntent(), grid_charge(3000.0), BatteryIntent(), BatteryIntent()]
    plan = plan_at(at("10:01"), FOUR, kinds=kinds, grid_charge_allowed=False)
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection, reply.details) == (
        "rejected",
        "invalid",
        GRID_CHARGE_OFF,
    )


def test_plan_with_soc_end_outside_0_to_100_is_rejected(tmp_path, limits) -> None:
    # das Modell verbietet es; model_copy prüft nicht, wie eine Absicht aus einer beschädigten
    # Datei oder einem anderen Sender
    plan = plan_at(at("10:01"), FOUR)
    values = [100.5, 50.0, -1.0, 100.0]
    slots = [
        slot.model_copy(update={"soc_end_pct": value})
        for slot, value in zip(plan.slots, values, strict=True)
    ]
    plan = plan.model_copy(update={"slots": slots})
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection) == ("rejected", "invalid")
    assert reply.details == ("Plan: Ziel-SOC eines Slots außerhalb von 0 bis 100 %.",)


def test_plan_with_soc_end_inside_0_to_100_is_accepted(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR)
    slots = [
        slot.model_copy(update={"soc_end_pct": value})
        for slot, value in zip(plan.slots, [0.0, 100.0, 55.5, None], strict=True)
    ]
    plan = plan.model_copy(update={"slots": slots})
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply == IntentReply("accepted", 1)


def test_plan_without_grid_charge_is_fine_with_switch_off(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR, grid_charge_allowed=False)
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply == IntentReply("accepted", 1)


def test_plan_problems_come_in_a_fixed_order_after_the_vehicle_problems(tmp_path, limits) -> None:
    kinds = [grid_charge(1000.0), BatteryIntent()]
    plan = plan_at(at("10:01"), ["10:00", "10:30"], kinds=kinds, grid_charge_allowed=False)
    intent = Intent(version=1, plan=plan, support=SupportIntent(soc_pct=10.0))
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=1), at("10:00"))
    reply = store.submit(intent.model_copy(update={"version": 2}), at("10:01"))
    assert reply.details == (*SLOTS_BROKEN, *GRID_CHARGE_OFF)  # die Schwelle klemmt der core
    assert store.current.version == 1  # die gültige Version bleibt


def test_invalid_plan_changes_nothing(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=1), at("10:00"))
    before = (tmp_path / "intent.json").read_bytes()
    bad = Intent(version=2, plan=plan_at(at("10:01"), ["10:00", "10:30"]))
    assert store.submit(bad, at("10:01")).rejection == "invalid"
    assert (store.current.version, (tmp_path / "intent.json").read_bytes()) == (1, before)


def test_valid_until_is_clamped_to_45_min_after_acceptance(tmp_path, limits) -> None:
    # die Uhr der app geht vor: Plan von 10:20 bis 11:05, angenommen um 10:01
    skewed = plan_at(at("10:20"), ["10:15", "10:30", "10:45", "11:00"], valid_until=at("11:05"))
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, plan=skewed), at("10:01"))
    assert reply.result == "clamped"
    assert store.current.plan.valid_until == at("10:46")
    assert reply.details == ("Plan bis 12:46 begrenzt.",)  # Wiener Zeit (UTC+2)
    saved = read_json(tmp_path / "intent.json")["intent"]["plan"]["valid_until"]
    assert datetime.fromisoformat(saved) == at("10:46")


def test_valid_until_within_45_min_of_acceptance_is_kept(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR)
    store = make_store(tmp_path, limits)
    assert store.submit(Intent(version=1, plan=plan), at("10:01") + PLAN_VALID).result == "accepted"
    assert store.current.plan.valid_until == plan.valid_until


def test_valid_until_hint_shows_winter_time_after_the_end_of_summer_time(tmp_path, limits) -> None:
    day = T0.replace(month=11, day=2)  # nach dem 25.10.: Wiener Zeit ist UTC+1
    created = day.replace(hour=10, minute=20)
    plan = IntentPlan(
        id=1,
        created_at=created,
        valid_until=created + PLAN_VALID,
        grid_charge_allowed=True,
        slots=[
            IntentPlanSlot(start=day.replace(hour=10, minute=minute), intent=BatteryIntent())
            for minute in (15, 30, 45)
        ]
        + [IntentPlanSlot(start=day.replace(hour=11, minute=0), intent=BatteryIntent())],
    )
    received = day.replace(hour=10, minute=1)
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), received)
    assert reply.details == ("Plan bis 11:46 begrenzt.",)


def test_grid_charge_above_the_setpoint_cap_is_clamped(tmp_path, limits) -> None:
    kinds = [grid_charge(9000.0), grid_charge(8000.0), grid_charge(500.0), BatteryIntent()]
    plan = plan_at(at("10:01"), FOUR, kinds=kinds)
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply == IntentReply(
        "clamped", 1, ("Netzladen auf 8000 W begrenzt (maximaler Netzladesetpoint).",)
    )
    assert [slot.intent.grid_charge_w for slot in store.current.plan.slots] == [8000, 8000, 500, 0]
    assert [slot.intent.kind for slot in store.current.plan.slots][:3] == [CHARGE] * 3


def test_grid_charge_cap_is_the_configured_setpoint_not_the_fuse(tmp_path, limits) -> None:
    named = dict((name, watts) for watts, name in limits.setpoint_limits)
    assert named["maximaler Netzladesetpoint"] == 8000.0
    assert named["Absicherung"] > 8000.0


def test_both_plan_clamps_come_after_the_boost_hints_in_a_fixed_order(tmp_path, limits) -> None:
    kinds = [grid_charge(9000.0)] + [BatteryIntent()] * 3
    plan = plan_at(at("10:20"), ["10:15", "10:30", "10:45", "11:00"], valid_until=at("11:05"))
    plan = plan.model_copy(
        update={"slots": [plan.slots[0].model_copy(update={"intent": kinds[0]}), *plan.slots[1:]]}
    )
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply.details == (
        "Plan bis 12:46 begrenzt.",
        "Netzladen auf 8000 W begrenzt (maximaler Netzladesetpoint).",
    )


def test_expired_plan_is_accepted_and_inert(tmp_path, limits) -> None:
    old = plan_at(at("10:01"), FOUR)  # gilt bis 10:46
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, plan=old), at("11:30"))
    assert reply == IntentReply("accepted", 1)
    assert store.current.plan == old
    assert not store.current.plan.valid_at(at("11:30"))


def test_expired_plan_with_a_long_validity_is_not_an_error(tmp_path, limits) -> None:
    # selbst ein Plan, dessen Slots längst vorbei sind und der zu hoch lädt, wird nur geklemmt
    old = plan_at(at("10:01"), FOUR, kinds=[grid_charge(9000.0)] + [BatteryIntent()] * 3)
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=old), at("13:00"))
    assert reply.result == "clamped"
    assert reply.details == ("Netzladen auf 8000 W begrenzt (maximaler Netzladesetpoint).",)


def test_repeated_version_with_clamped_plan_is_a_repetition(tmp_path, limits) -> None:
    skewed = plan_at(at("10:20"), ["10:15", "10:30", "10:45", "11:00"], valid_until=at("11:05"))
    intent = Intent(version=1, plan=skewed)
    store = make_store(tmp_path, limits)
    first = store.submit(intent, at("10:01"))
    saved = (tmp_path / "intent.json").read_bytes()
    # zehn Minuten später: der Empfang bleibt die Annahme, die Klemmung ändert sich nicht
    again = store.submit(intent, at("10:11"))
    assert again == first == IntentReply("clamped", 1, ("Plan bis 12:46 begrenzt.",))
    assert (tmp_path / "intent.json").read_bytes() == saved
    assert store.current.plan.valid_until == at("10:46")


def test_repeated_version_after_a_restart_is_a_repetition(tmp_path, limits) -> None:
    skewed = plan_at(at("10:20"), ["10:15", "10:30", "10:45", "11:00"], valid_until=at("11:05"))
    intent = Intent(version=1, plan=skewed)
    make_store(tmp_path, limits).submit(intent, at("10:01"))
    restarted = make_store(tmp_path, limits)
    assert restarted.submit(intent, at("10:30")).result == "clamped"


def test_same_version_with_another_plan_is_a_conflict(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=1, plan=plan_at(at("10:01"), FOUR)), at("10:01"))
    other = Intent(version=1, plan=plan_at(at("10:02"), FOUR))
    assert store.submit(other, at("10:02")).rejection == "stale"


RAISED = "Unterstützungsschwelle 15 % auf die USV-Reserve (20 %) angehoben."


def test_support_threshold_below_reserve_is_raised_to_it(tmp_path, limits) -> None:
    # Gesamtreview, Minor 3: geklemmt mit Hinweis statt die ganze Absicht abzulehnen
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, support=SupportIntent(soc_pct=15.0)), T0)
    assert reply == IntentReply("clamped", 1, (RAISED,))
    assert store.current.support == SupportIntent(soc_pct=20.0)
    assert read_json(tmp_path / "intent.json")["intent"]["support"]["soc_pct"] == 20.0
    # dieselbe Version noch einmal ist eine Wiederholung mit derselben Antwort
    again = store.submit(Intent(version=1, support=SupportIntent(soc_pct=15.0)), T0)
    assert again == reply


def test_support_threshold_with_decimals_is_shown_as_is(tmp_path, limits) -> None:
    reply = make_store(tmp_path, limits).submit(
        Intent(version=1, support=SupportIntent(soc_pct=19.5)), T0
    )
    assert reply.details == ("Unterstützungsschwelle 19.5 % auf die USV-Reserve (20 %) angehoben.",)


def test_a_raised_reserve_does_not_block_other_changes(tmp_path, limits) -> None:
    # Die Reserve stieg in config.yaml über die gespeicherte Schwelle: Moduswechsel und
    # „Regelbetrieb aus“ gehen weiter durch, die Schwelle steigt auf die Reserve.
    saved = Intent(
        version=6,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)},
        support=SupportIntent(soc_pct=15.0),
    )
    document = {"intent": saved.model_dump(mode="json"), "accepted_at": T0.isoformat()}
    write_json_atomic(tmp_path / "intent.json", document)
    store = charging_store(tmp_path, limits)
    store.load()
    mode = saved.model_copy(update={"version": 7, "vehicles": {"tesla": VehicleIntent()}})
    assert store.submit(mode, T0) == IntentReply("clamped", 7, (RAISED,))
    off = mode.model_copy(update={"version": 8, "control": False})
    assert store.submit(off, T0) == IntentReply("clamped", 8, (RAISED,))
    assert store.current.control is False
    assert store.current.support == SupportIntent(soc_pct=20.0)


@pytest.mark.parametrize(
    "support",
    [SupportIntent(), SupportIntent(soc_pct=20.0), SupportIntent(allowed=False, soc_pct=100.0)],
)
def test_support_at_or_above_reserve_or_unset_is_accepted(tmp_path, limits, support) -> None:
    store = make_store(tmp_path, limits)
    assert store.submit(Intent(version=1, support=support), T0) == IntentReply("accepted", 1)
    assert store.current.support == support
    assert read_json(tmp_path / "intent.json")["intent"]["support"] == support.model_dump()


def test_blocked_support_below_reserve_is_raised_too(tmp_path, limits) -> None:
    reply = make_store(tmp_path, limits).submit(
        Intent(version=1, support=SupportIntent(allowed=False, soc_pct=5.0)), T0
    )
    assert reply.result == "clamped"
    assert reply.details == ("Unterstützungsschwelle 5 % auf die USV-Reserve (20 %) angehoben.",)


def test_saved_intent_without_plan_loads_with_defaults(tmp_path, limits) -> None:
    # so schrieb der core in Plan 2d: Lademodi und Gast, aber weder „plan“ noch „support“
    sent = {
        "version": 6,
        "control": True,
        "actuators": {"wallbox.evcs": "dry_run"},
        "vehicles": {"tesla": {"mode": "now", "boost_until": None, "wallbox": "evcs"}},
        "priority": None,
        "surplus_priority": {"kind": "cars_first", "soc_pct": 50.0},
        "guest": {"mode": "pv"},
    }
    write_json_atomic(tmp_path / "intent.json", {"intent": sent, "accepted_at": T0.isoformat()})
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.load_problem is None
    assert store.current.plan is None
    assert store.current.support == SupportIntent()
    # die app sendet dieselbe Absicht weiter: eine Wiederholung, kein Konflikt
    assert store.submit(Intent.model_validate(sent), T0) == IntentReply("accepted", 6)


def test_saved_intent_with_plan_is_reloaded(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR, kinds=[grid_charge(2000.0)] + [BatteryIntent()] * 3)
    make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    store = make_store(tmp_path, limits)
    assert store.load_problem is None
    assert store.current.plan == plan
    assert store.accepted_at == at("10:01")


# --- Geplantes Laden in der Absicht (Plan 4b, Präzisierung 2) ---


def with_vehicles(plan: IntentPlan, powers: list[dict[str, int]]) -> IntentPlan:
    """Der Plan mit der Ladeleistung je Fahrzeug in den ersten Slots (wie von der app gesendet)."""
    slots = [
        slot.model_copy(update={"vehicles": power})
        for slot, power in zip(plan.slots, powers, strict=False)
    ]
    return plan.model_copy(update={"slots": [*slots, *plan.slots[len(slots) :]]})


def test_guest_cannot_be_planned(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, guest=GuestIntent(mode=ChargeMode.PLANNED)), T0)
    assert reply.result == "rejected" and reply.rejection == "invalid"
    assert reply.details == ("Ein Gast kann nicht „Geplant“ laden.",)
    assert store.current is None


def test_a_guest_in_every_other_mode_is_fine(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    for version, mode in enumerate(
        (ChargeMode.PV, ChargeMode.MIN_PV, ChargeMode.NOW, ChargeMode.OFF), start=1
    ):
        assert store.submit(Intent(version=version, guest=GuestIntent(mode=mode)), T0) == (
            IntentReply("accepted", version)
        )


def test_a_vehicle_can_be_planned_with_target_and_departure(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    planned = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=90, departure="06:30")
    assert store.submit(Intent(version=1, vehicles={"tesla": planned}), T0) == (
        IntentReply("accepted", 1)
    )
    assert store.current.vehicles["tesla"] == planned


def test_plan_vehicle_power_within_the_limit_is_kept(tmp_path, limits) -> None:
    plan = with_vehicles(plan_at(at("10:01"), FOUR), [{"tesla": 11040, "egolf": 0}, {"egolf": 1}])
    store = charging_store(tmp_path, limits)
    assert store.submit(Intent(version=1, plan=plan), at("10:01")) == IntentReply("accepted", 1)
    assert [slot.vehicles for slot in store.current.plan.slots] == [
        {"tesla": 11040, "egolf": 0},
        {"egolf": 1},
        {},
        {},
    ]


def test_plan_naming_an_unknown_vehicle_is_rejected(tmp_path, limits) -> None:
    plan = with_vehicles(
        plan_at(at("10:01"), FOUR), [{"golf": 3000}, {"tesla": 3000, "golf": 0}, {"audi": 100}]
    )
    store = charging_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection, reply.version) == ("rejected", "invalid", None)
    # je Schlüssel ein Hinweis, sortiert, egal in wie vielen Slots er steht
    assert reply.details == (
        "Plan nennt unbekanntes Fahrzeug „audi“.",
        "Plan nennt unbekanntes Fahrzeug „golf“.",
    )
    assert store.current is None


def test_plan_without_any_vehicles_in_the_store_names_them_all_unknown(tmp_path, limits) -> None:
    plan = with_vehicles(plan_at(at("10:01"), FOUR), [{"tesla": 3000}])
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply.details == ("Plan nennt unbekanntes Fahrzeug „tesla“.",)


def test_plan_with_negative_vehicle_power_is_rejected(tmp_path, limits) -> None:
    # das Modell verbietet negative Werte; model_copy prüft nicht, wie eine Absicht aus einer
    # beschädigten Datei oder einem anderen Sender
    plan = with_vehicles(plan_at(at("10:01"), FOUR), [{"tesla": -1}, {"egolf": 0}, {"tesla": -50}])
    reply = charging_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert (reply.result, reply.rejection) == ("rejected", "invalid")
    assert reply.details == ("Plan hat negative Ladeleistung für „tesla“.",)


def test_plan_problems_come_after_the_plan_findings(tmp_path, limits) -> None:
    plan = with_vehicles(
        plan_at(at("10:01"), ["10:00", "10:30"]), [{"golf": 1}, {"tesla": -1}]
    )  # Lücke im Raster
    reply = charging_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply.details == (
        *SLOTS_BROKEN,
        "Plan nennt unbekanntes Fahrzeug „golf“.",
        "Plan hat negative Ladeleistung für „tesla“.",
    )


def test_plan_vehicle_power_is_checked_and_clamped(tmp_path, limits) -> None:
    plan = with_vehicles(
        plan_at(at("10:01"), FOUR),
        [{"tesla": 20000, "egolf": 5000}, {"tesla": 11040}, {"tesla": 12000, "egolf": 9000}],
    )
    store = charging_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, plan=plan), at("10:01"))
    # ein Hinweis je Fahrzeug in der Reihenfolge der Konfiguration, egal in wie vielen Slots
    assert reply == IntentReply(
        "clamped",
        1,
        ("Plan für Tesla Model 3 auf 11040 W begrenzt.", "Plan für e-Golf auf 7360 W begrenzt."),
    )
    assert [slot.vehicles for slot in store.current.plan.slots] == [
        {"tesla": 11040, "egolf": 5000},
        {"tesla": 11040},
        {"tesla": 11040, "egolf": 7360},
        {},
    ]


def test_vehicle_clamp_comes_after_the_grid_charge_clamp(tmp_path, limits) -> None:
    plan = plan_at(at("10:01"), FOUR, kinds=[grid_charge(9000.0)] + [BatteryIntent()] * 3)
    plan = with_vehicles(plan, [{"tesla": 20000}])
    reply = charging_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    assert reply.details == (
        "Netzladen auf 8000 W begrenzt (maximaler Netzladesetpoint).",
        "Plan für Tesla Model 3 auf 11040 W begrenzt.",
    )


def test_repeated_version_with_clamped_vehicle_power_is_a_repetition(tmp_path, limits) -> None:
    plan = with_vehicles(plan_at(at("10:01"), FOUR), [{"tesla": 20000}])
    store = charging_store(tmp_path, limits)
    first = store.submit(Intent(version=1, plan=plan), at("10:01"))
    assert store.submit(Intent(version=1, plan=plan), at("10:02")) == first


def test_clamped_vehicle_power_is_saved_and_reloaded(tmp_path, limits) -> None:
    plan = with_vehicles(plan_at(at("10:01"), FOUR), [{"tesla": 20000}])
    charging_store(tmp_path, limits).submit(Intent(version=1, plan=plan), at("10:01"))
    reloaded = charging_store(tmp_path, limits)
    reloaded.load()
    assert reloaded.current.plan.slots[0].vehicles == {"tesla": 11040}


def test_intent_file_from_4a_loads_with_target_and_departure_defaults(tmp_path, limits) -> None:
    # so schrieb der core in Plan 4a: Fahrzeuge ohne Ziel und Abfahrt, Plan-Slots ohne Fahrzeuge
    plan = plan_at(at("10:01"), FOUR).model_dump(mode="json")
    for slot in plan["slots"]:
        del slot["vehicles"]
    sent = {
        "version": 6,
        "control": True,
        "actuators": {"wallbox.evcs": "dry_run"},
        "vehicles": {
            "tesla": {"mode": "now", "boost_until": None, "wallbox": "evcs"},
            "egolf": {"mode": "pv", "boost_until": None, "wallbox": None},
        },
        "priority": ["egolf", "tesla"],
        "surplus_priority": {"kind": "cars_first", "soc_pct": 50.0},
        "guest": {"mode": "pv"},
        "plan": plan,
        "support": {"allowed": True, "soc_pct": None},
    }
    write_json_atomic(
        tmp_path / "intent.json", {"intent": sent, "accepted_at": at("10:01").isoformat()}
    )
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.load_problem is None
    for entry in store.current.vehicles.values():
        assert (entry.target_soc_pct, entry.departure) == (80, "07:00")
    assert store.current.vehicles["tesla"].mode is ChargeMode.NOW
    assert [slot.vehicles for slot in store.current.plan.slots] == [{}, {}, {}, {}]
    # die app sendet dieselbe Absicht weiter: eine Wiederholung, kein Konflikt
    resent = Intent.model_validate(sent)
    assert store.submit(resent, at("10:02")) == IntentReply("accepted", 6)
