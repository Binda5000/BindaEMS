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
    armed_for_core,
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
        "watchdog.heartbeat": 7,
        "watchdog.timeout_s": 90,
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
        "Heartbeat": 7,
        "TimeoutSeconds": 90,
    }
    values = {}
    for path, value in delivered.items():
        payload = json.dumps({"value": value}).encode()
        for update in parse_message(f"N/{P}/bindaems/0/{path}", payload, P, resolver):
            values[update.signal] = update.value
    assert read_watchdog(snap(values, kind=STATE)) == WatchdogStatus(
        W.TRIPPED, 3, datetime.fromtimestamp(1791600000, UTC), 1, "1.0.0", 7, 90.0
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


# --- Echo und Timeout: scharf für diesen core -------------------------------------------------


def status(**changes: Any) -> WatchdogStatus:
    """Ein scharfer Watchdog mit Echo 7 und dem erwarteten Timeout von 90 s; ``changes`` ändert."""
    fields = {
        "state": WatchdogState.ARMED,
        "trip_count": 0,
        "last_trip": None,
        "last_trip_failures": 0,
        "version": WATCHDOG_VERSION,
        "heartbeat": 7,
        "timeout_s": 90.0,
    }
    return WatchdogStatus(**(fields | changes))


def test_armed_for_core_needs_echo_and_timeout() -> None:
    assert armed_for_core(status(), (5, 6, 7), 90.0)
    # Zähler eines früheren Laufs
    assert not armed_for_core(status(heartbeat=1234), (5, 6, 7), 90.0)
    assert not armed_for_core(status(timeout_s=120.0), (5, 6, 7), 90.0)
    assert not armed_for_core(status(state=WatchdogState.TRIPPED), (5, 6, 7), 90.0)


def test_timeout_mismatch_alarm() -> None:
    alarms = watchdog_alarms(status(timeout_s=120.0), False, T0, expected_timeout_s=90.0)
    assert [(a.id, a.message) for a in alarms] == [
        (
            "watchdog.timeout",
            "Watchdog-Timeout 120 s am Cerbo, erwartet 90 s; "
            "config.ini neu erzeugen und mit install.sh übertragen.",
        )
    ]


@pytest.mark.parametrize("state", [s for s in W if s is not W.ARMED])
def test_only_an_armed_watchdog_is_armed_for_the_core(state: WatchdogState) -> None:
    assert not armed_for_core(status(state=state), (5, 6, 7), 90.0)


@pytest.mark.parametrize("changes", [{"heartbeat": None}, {"timeout_s": None}])
def test_unknown_echo_or_timeout_is_not_armed_for_the_core(changes: dict[str, Any]) -> None:
    assert not armed_for_core(status(**changes), (5, 6, 7), 90.0)


@pytest.mark.parametrize("echo", [5, 6, 7])
def test_each_recent_counter_arms_for_the_core(echo: int) -> None:
    assert armed_for_core(status(heartbeat=echo), (5, 6, 7), 90.0)


@pytest.mark.parametrize("echo", [4, 8, 0])
def test_a_counter_outside_recent_does_not_arm_for_the_core(echo: int) -> None:
    assert not armed_for_core(status(heartbeat=echo), (5, 6, 7), 90.0)


def test_nothing_sent_yet_is_never_armed_for_the_core() -> None:
    assert not armed_for_core(status(), (), 90.0)


def test_armed_for_core_compares_the_timeout_as_a_number() -> None:
    assert armed_for_core(status(timeout_s=90.0), (7,), 90)  # 90 == 90.0
    assert armed_for_core(status(timeout_s=90.5), (7,), 90.5)
    assert not armed_for_core(status(timeout_s=90.5), (7,), 90.0)


def test_timeout_alarm_is_an_error_since_now() -> None:
    (alarm,) = watchdog_alarms(status(timeout_s=120.0), False, T0, expected_timeout_s=90.0)
    assert (alarm.severity, alarm.since) == (Severity.ERROR, T0)


@pytest.mark.parametrize("required", [True, False])
def test_timeout_alarm_does_not_depend_on_required(required: bool) -> None:
    alarms = watchdog_alarms(status(timeout_s=120.0), required, T0, expected_timeout_s=90.0)
    assert [a.id for a in alarms] == ["watchdog.timeout"]


def test_timeout_alarm_writes_numbers_the_german_way() -> None:
    alarms = watchdog_alarms(status(timeout_s=90.5), False, T0, expected_timeout_s=90.0)
    assert alarms[0].message.startswith("Watchdog-Timeout 90,5 s am Cerbo, erwartet 90 s;")
    alarms = watchdog_alarms(status(timeout_s=90.0), False, T0, expected_timeout_s=120.5)
    assert alarms[0].message.startswith("Watchdog-Timeout 90 s am Cerbo, erwartet 120,5 s;")


@pytest.mark.parametrize(
    ("actual", "expected"),
    [(90.0, 90.0), (90.0, 90), (None, 90.0), (120.0, None)],
    ids=["equal", "equal-as-number", "unknown-at-cerbo", "nothing-expected"],
)
def test_no_timeout_alarm_when_equal_or_unknown(
    actual: float | None, expected: float | None
) -> None:
    alarms = watchdog_alarms(status(timeout_s=actual), True, T0, expected_timeout_s=expected)
    assert alarms == []


def test_timeout_is_not_compared_without_an_expected_one() -> None:
    assert watchdog_alarms(status(timeout_s=120.0), True, T0) == []


def test_timeout_alarm_sits_between_unavailable_and_version() -> None:
    last = datetime.fromtimestamp(1791600000, UTC)
    tripped = status(state=W.TRIPPED, last_trip=last, version="0.9.0", timeout_s=120.0)
    assert [a.id for a in watchdog_alarms(tripped, True, T0, expected_timeout_s=90.0)] == [
        "watchdog.tripped",
        "watchdog.timeout",
        "watchdog.version",
    ]
    disabled = status(state=W.DISABLED, version="0.9.0", timeout_s=120.0)
    assert [a.id for a in watchdog_alarms(disabled, True, T0, expected_timeout_s=90.0)] == [
        "watchdog.unavailable",
        "watchdog.timeout",
        "watchdog.version",
    ]


def test_read_echo_and_timeout() -> None:
    values = {"watchdog.state": 1, "watchdog.heartbeat": 7, "watchdog.timeout_s": 90}
    read = read_watchdog(snap(values, kind=STATE))
    assert (read.heartbeat, read.timeout_s) == (7, 90.0)
    assert isinstance(read.timeout_s, float)


def test_fractional_timeout_is_kept() -> None:
    assert read_watchdog(snap({"watchdog.timeout_s": 90.5}, kind=STATE)).timeout_s == 90.5


def test_missing_echo_and_timeout_stay_unknown() -> None:
    read = read_watchdog(snap({"watchdog.state": 1}, kind=STATE))
    assert (read.heartbeat, read.timeout_s) == (None, None)
    assert (WatchdogStatus.unknown().heartbeat, WatchdogStatus.unknown().timeout_s) == (None, None)


@pytest.mark.parametrize("value", [None, True, "7", 1.5, float("nan"), float("inf")])
def test_unusable_echo_is_unknown(value: Any) -> None:
    assert read_watchdog(snap({"watchdog.heartbeat": value}, kind=STATE)).heartbeat is None


@pytest.mark.parametrize("value", [None, True, "90", float("nan"), float("inf")])
def test_unusable_timeout_is_unknown(value: Any) -> None:
    assert read_watchdog(snap({"watchdog.timeout_s": value}, kind=STATE)).timeout_s is None
