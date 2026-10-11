import json
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.helpers import T0, snap

from bindaems.core.adapters.victron_topics import InstanceResolver, parse_message
from bindaems.core.watchdog_client.status import (
    WATCHDOG_VERSION,
    WatchdogState,
    WatchdogStatus,
    read_watchdog,
    watchdog_alarms,
)
from bindaems.shared.config import Config
from bindaems.shared.domain import Alarm, Quality, Severity, SignalKind

STATE = SignalKind.STATE
W = WatchdogState
P = "c0619ab1234"

TRIPPED_TEXT = "Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt."
NO_ANSWER_TEXT = "Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run."
DISABLED_TEXT = "Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run."


@pytest.mark.parametrize(
    ("value", "state"),
    [(0, W.WAITING), (1, W.ARMED), (2, W.TRIPPED), (3, W.DISABLED), (7, W.UNKNOWN)],
)
def test_read_states(value: int, state: WatchdogState) -> None:
    assert read_watchdog(snap({"watchdog.state": value}, kind=STATE)).state is state


def test_missing_or_stale_state_is_unknown() -> None:
    assert read_watchdog(snap({}, kind=STATE)).state is W.UNKNOWN
    assert read_watchdog(snap({"watchdog.state": 1}, kind=STATE, quality=Quality.STALE)).state is (
        W.UNKNOWN
    )


def test_read_details() -> None:
    values = {
        "watchdog.state": 2,
        "watchdog.trip_count": 2,
        "watchdog.last_trip_time": 1791600000,
        "watchdog.last_trip_failures": 1,
        "watchdog.version": "1.0.0",
    }
    assert read_watchdog(snap(values, kind=STATE)) == WatchdogStatus(
        W.TRIPPED, 2, datetime.fromtimestamp(1791600000, UTC), 1, "1.0.0"
    )
    assert read_watchdog(snap({"watchdog.last_trip_time": 0}, kind=STATE)).last_trip is None


def test_alarm_when_tripped() -> None:
    last = datetime.fromtimestamp(1791600000, UTC)
    status = WatchdogStatus(W.TRIPPED, 3, last, 2, "1.0.0")
    assert watchdog_alarms(status, required=False, now=T0) == [
        Alarm(
            "watchdog.tripped",
            Severity.ERROR,
            "Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt."
            " Fehlgeschlagene Aktionen: 2.",
            last,
        )
    ]


def test_unavailable_only_when_required() -> None:
    unknown = WatchdogStatus.unknown()
    assert watchdog_alarms(unknown, required=False, now=T0) == []
    assert watchdog_alarms(unknown, required=True, now=T0)[0].message == (
        "Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run."
    )
    disabled = WatchdogStatus(W.DISABLED, 0, None, 0, "1.0.0")
    assert watchdog_alarms(disabled, required=True, now=T0)[0].message == (
        "Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run."
    )
    assert (
        watchdog_alarms(WatchdogStatus(W.WAITING, 0, None, 0, "1.0.0"), required=True, now=T0) == []
    )


def test_version_mismatch_warns() -> None:
    status = WatchdogStatus(W.ARMED, 0, None, 0, "0.9.0")
    assert watchdog_alarms(status, required=False, now=T0) == [
        Alarm(
            "watchdog.version",
            Severity.WARNING,
            "Watchdog-Version 0.9.0 am Cerbo, erwartet 1.0.0; bitte mit install.sh aktualisieren.",
            T0,
        )
    ]


# --- Lesen: Randfälle -------------------------------------------------------------------------


def test_only_the_armed_state_is_armed() -> None:
    assert [s for s in W if WatchdogStatus(s, None, None, None, None).armed] == [W.ARMED]


def test_unknown_status_has_no_details() -> None:
    assert WatchdogStatus.unknown() == WatchdogStatus(W.UNKNOWN, None, None, None, None)


@pytest.mark.parametrize("quality", [Quality.STALE, Quality.INVALID])
def test_only_ok_values_count(quality: Quality) -> None:
    values = {
        "watchdog.state": 2,
        "watchdog.trip_count": 2,
        "watchdog.last_trip_time": 1791600000,
        "watchdog.last_trip_failures": 1,
        "watchdog.version": "1.0.0",
    }
    assert read_watchdog(snap(values, kind=STATE, quality=quality)) == WatchdogStatus.unknown()


def test_missing_details_stay_unknown() -> None:
    assert read_watchdog(snap({"watchdog.state": 1}, kind=STATE)) == WatchdogStatus(
        W.ARMED, None, None, None, None
    )


@pytest.mark.parametrize("value", [None, True, False, "1", 1.5, float("nan"), float("inf"), -1, 4])
def test_unusable_state_values_are_unknown(value: Any) -> None:
    assert read_watchdog(snap({"watchdog.state": value}, kind=STATE)).state is W.UNKNOWN


def test_whole_float_state_counts() -> None:
    assert read_watchdog(snap({"watchdog.state": 1.0}, kind=STATE)).state is W.ARMED


@pytest.mark.parametrize("value", [None, True, "2", 1.5, float("nan"), float("inf")])
def test_unusable_counts_are_unknown(value: Any) -> None:
    values = {"watchdog.trip_count": value, "watchdog.last_trip_failures": value}
    status = read_watchdog(snap(values, kind=STATE))
    assert (status.trip_count, status.last_trip_failures) == (None, None)


@pytest.mark.parametrize("value", [None, True, 5, 1.5])
def test_only_text_is_a_version(value: Any) -> None:
    assert read_watchdog(snap({"watchdog.version": value}, kind=STATE)).version is None


def test_last_trip_is_a_utc_time() -> None:
    last = read_watchdog(snap({"watchdog.last_trip_time": 1791600000}, kind=STATE)).last_trip
    assert last == datetime(2026, 10, 10, 2, 40, tzinfo=UTC)
    assert last is not None and last.utcoffset() is not None and not last.utcoffset()


@pytest.mark.parametrize(
    "value",
    [0, -5, None, True, "1791600000", float("nan"), float("inf"), 1e20, 10**30, 2**62, 1e12],
)
def test_no_last_trip_without_a_usable_time(value: Any) -> None:
    # Ein unbrauchbarer Zeitstempel darf den Zyklus nicht zum Scheitern bringen (kein Heartbeat).
    assert read_watchdog(snap({"watchdog.last_trip_time": value}, kind=STATE)).last_trip is None


def test_reads_what_the_topics_deliver(cfg: Config) -> None:
    # Die Signalnamen der Topic-Tabelle und des Lesers müssen zusammenpassen.
    resolver = InstanceResolver(cfg.victron.instances)
    delivered = {
        "State": 2,
        "TripCount": 3,
        "LastTripTime": 1791600000,
        "LastTripFailures": 1,
        "Version": "1.0.0",
    }
    values = {}
    for path, value in delivered.items():
        payload = json.dumps({"value": value}).encode()
        for update in parse_message(f"N/{P}/bindaems/0/{path}", payload, P, resolver):
            values[update.signal] = update.value
    assert read_watchdog(snap(values, kind=STATE)) == WatchdogStatus(
        W.TRIPPED, 3, datetime.fromtimestamp(1791600000, UTC), 1, "1.0.0"
    )


# --- Alarme: Randfälle ------------------------------------------------------------------------


@pytest.mark.parametrize("failures", [0, None])
def test_tripped_without_failures_has_no_suffix(failures: int | None) -> None:
    status = WatchdogStatus(W.TRIPPED, 1, None, failures, WATCHDOG_VERSION)
    assert watchdog_alarms(status, required=False, now=T0) == [
        Alarm("watchdog.tripped", Severity.ERROR, TRIPPED_TEXT, T0)
    ]


def test_tripped_alarm_since_the_trip_time() -> None:
    last = datetime.fromtimestamp(1791600000, UTC)
    status = WatchdogStatus(W.TRIPPED, 1, last, 0, WATCHDOG_VERSION)
    assert watchdog_alarms(status, required=True, now=T0)[0].since == last


@pytest.mark.parametrize(
    ("state", "text"), [(W.UNKNOWN, NO_ANSWER_TEXT), (W.DISABLED, DISABLED_TEXT)]
)
def test_unavailable_alarm_is_an_error_since_now(state: WatchdogState, text: str) -> None:
    status = WatchdogStatus(state, None, None, None, None)
    assert watchdog_alarms(status, required=True, now=T0) == [
        Alarm("watchdog.unavailable", Severity.ERROR, text, T0)
    ]
    assert watchdog_alarms(status, required=False, now=T0) == []


@pytest.mark.parametrize("state", [W.WAITING, W.ARMED])
def test_no_alarm_for_waiting_or_armed(state: WatchdogState) -> None:
    status = WatchdogStatus(state, 0, None, 0, WATCHDOG_VERSION)
    assert watchdog_alarms(status, required=True, now=T0) == []


def test_version_alarm_needs_a_known_different_version() -> None:
    unknown_version = WatchdogStatus(W.ARMED, 0, None, 0, None)
    assert watchdog_alarms(unknown_version, required=True, now=T0) == []
    expected_version = WatchdogStatus(W.ARMED, 0, None, 0, WATCHDOG_VERSION)
    assert watchdog_alarms(expected_version, required=True, now=T0) == []


def test_alarms_come_in_table_order() -> None:
    last = datetime.fromtimestamp(1791600000, UTC)
    tripped = WatchdogStatus(W.TRIPPED, 1, last, 0, "0.9.0")
    assert [a.id for a in watchdog_alarms(tripped, required=True, now=T0)] == [
        "watchdog.tripped",
        "watchdog.version",
    ]
    disabled = WatchdogStatus(W.DISABLED, 0, None, 0, "0.9.0")
    assert [a.id for a in watchdog_alarms(disabled, required=True, now=T0)] == [
        "watchdog.unavailable",
        "watchdog.version",
    ]
