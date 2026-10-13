from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

import pytest
from tests.helpers import T0

from bindaems.core.watchdog_client.heartbeat import HEARTBEAT_MAX, HeartbeatClient, heartbeat_start
from bindaems.core.watchdog_client.status import WatchdogState, WatchdogStatus, armed_for_core

S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def recorder(results: Iterable[bool] = ()) -> tuple[list[int], Callable[[int], bool]]:
    sent: list[int] = []
    answers = iter(results)

    def send(value: int) -> bool:
        sent.append(value)
        return next(answers, True)

    return sent, send


def recording(interval_s: float) -> tuple[list[int], HeartbeatClient]:
    """Die Liste der gesendeten Werte und ein Client, dessen Senden immer gelingt."""
    sent, send = recorder()
    return sent, HeartbeatClient(send, interval_s)


def test_sends_every_interval_when_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    for s in range(21):
        client.on_cycle(S(s), required=True)
    assert (sent, client.status) == ([1, 2, 3], "active")


def test_nothing_when_not_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=False)
    assert (sent, client.status) == ([], "off")


def test_release_once_when_no_longer_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(2), required=False)
    assert (sent, client.status, client.last_sent) == ([1, 0], "released", None)


def test_failed_send_retried_next_cycle() -> None:
    sent, send = recorder([False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=True)
    assert (sent, client.last_sent) == ([1, 1], S(1))


def test_counter_wraps_to_one() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0, first=HEARTBEAT_MAX)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(10), required=True)
    assert sent == [HEARTBEAT_MAX, 1]


# --- Randfälle --------------------------------------------------------------------------------


def test_starts_off_and_never_sent() -> None:
    _, send = recorder()
    client = HeartbeatClient(send, 10.0)
    assert (client.status, client.last_sent) == ("off", None)


def test_first_counter_is_configurable() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0, first=41)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(10), required=True)
    assert sent == [41, 42]


def test_waits_for_the_interval_with_a_tolerance_of_50_ms() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(9.94), required=True)
    assert sent == [1]
    client.on_cycle(S(9.95), required=True)  # der Takt darf um bis zu 50 ms zu früh sein
    assert (sent, client.last_sent) == ([1, 2], S(9.95))


def test_heartbeat_counter_fits_a_dbus_int32() -> None:
    assert HEARTBEAT_MAX == 2_147_483_647


def test_interval_counts_from_the_last_successful_send() -> None:
    sent, send = recorder([True, False, False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)  # 1 gesendet
    client.on_cycle(S(10), required=True)  # 2 scheitert
    client.on_cycle(S(11), required=True)  # 2 scheitert erneut
    client.on_cycle(S(12), required=True)  # 2 gelingt
    client.on_cycle(S(21), required=True)  # erst 9 s seit S(12)
    assert (sent, client.last_sent) == ([1, 2, 2, 2], S(12))
    client.on_cycle(S(22), required=True)
    assert sent == [1, 2, 2, 2, 3]


def test_failed_first_send_leaves_the_client_off() -> None:
    sent, send = recorder([False])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    assert (sent, client.status, client.last_sent) == ([1], "off", None)


def test_no_release_when_no_heartbeat_ever_went_out() -> None:
    sent, send = recorder([False])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    assert (sent, client.status) == ([1], "off")


def test_failed_release_retried_next_cycle() -> None:
    sent, send = recorder([True, False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    assert (sent, client.status, client.last_sent) == ([1, 0], "active", T0)
    client.on_cycle(S(2), required=False)
    assert (sent, client.status, client.last_sent) == ([1, 0, 0], "released", None)


def test_required_again_after_release_sends_the_next_counter_at_once() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(2), required=True)
    assert (sent, client.status, client.last_sent) == ([1, 0, 2], "active", S(2))


def test_counter_skips_zero_after_the_wrap() -> None:
    # 0 heißt „Freigabe“: Als Zähler darf sie nie vorkommen.
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0, first=HEARTBEAT_MAX - 1)
    for s in range(0, 41, 10):
        client.on_cycle(S(s), required=True)
    assert sent == [HEARTBEAT_MAX - 1, HEARTBEAT_MAX, 1, 2, 3]


# --- Heartbeat nur, solange der core regelt: Zähler nur mit ``allowed`` -----------------------


def test_no_counter_when_not_allowed() -> None:
    sent, client = recording(interval_s=10.0)
    client.on_cycle(T0, required=True, allowed=False)
    assert sent == [] and client.status == "off"


def test_counter_withheld_while_not_allowed_is_sent_once_allowed() -> None:
    sent, client = recording(10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(10), required=True, allowed=False)
    client.on_cycle(S(11), required=True, allowed=False)
    assert (sent, client.status) == ([1], "active")
    client.on_cycle(S(12), required=True)
    assert sent == [1, 2]


def test_never_released_while_required() -> None:
    # Auch nicht, wenn der Zähler ausbleibt: Dann soll der Watchdog auslösen.
    sent, client = recording(10.0)
    client.on_cycle(T0, required=True)
    for s in (1, 30, 100):
        client.on_cycle(
            S(s), required=True, allowed=False, release_allowed=True, watchdog=WatchdogState.TRIPPED
        )
    assert (sent, client.status) == ([1], "active")


# --- Freigabe nur mit ``release_allowed`` -----------------------------------------------------


def test_release_needs_release_allowed() -> None:
    sent, client = recording(interval_s=10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(T0 + timedelta(seconds=1), required=False, release_allowed=False)
    assert sent == [1]
    client.on_cycle(T0 + timedelta(seconds=2), required=False, release_allowed=True)
    assert sent == [1, 0]


def test_no_release_without_release_allowed_however_long_it_takes() -> None:
    sent, client = recording(10.0)
    client.on_cycle(T0, required=True)
    for s in (1, 20, 300):
        client.on_cycle(S(s), required=False, release_allowed=False)
    assert (sent, client.status) == ([1], "active")


# --- Verlorene Freigabe wiederholen -----------------------------------------------------------


def test_lost_release_is_sent_again() -> None:
    sent, client = recording(interval_s=10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(T0 + timedelta(seconds=1), required=False)
    for s in (5, 20, 21, 41):
        client.on_cycle(T0 + timedelta(seconds=s), required=False, watchdog=WatchdogState.ARMED)
    assert sent == [1, 0, 0, 0]  # bei 21 s und bei 41 s


@pytest.mark.parametrize(
    "state", [WatchdogState.WAITING, WatchdogState.DISABLED, WatchdogState.UNKNOWN]
)
def test_release_is_only_resent_to_an_armed_watchdog(state: WatchdogState) -> None:
    sent, client = recording(10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    for s in (21, 41, 300):
        client.on_cycle(S(s), required=False, watchdog=state)
    assert sent == [1, 0]


def _released_with_counters_1_to_3() -> tuple[list[int], HeartbeatClient]:
    """Zähler 1 bis 3 gesendet (``recent == (1, 2, 3)``), die Freigabe 0 bei 21 s."""
    sent, client = recording(10.0)
    for s in (0, 10, 20):
        client.on_cycle(S(s), required=True)
    client.on_cycle(S(21), required=False)
    assert (sent, client.recent) == ([1, 2, 3, 0], (1, 2, 3))
    return sent, client


@pytest.mark.parametrize("echo", [None, 1, 2, 3])
def test_lost_release_is_resent_for_the_own_counters_or_an_unknown_echo(echo: int | None) -> None:
    sent, client = _released_with_counters_1_to_3()
    client.on_cycle(S(41), required=False, watchdog=WatchdogState.ARMED, echo=echo)
    assert sent == [1, 2, 3, 0, 0]


@pytest.mark.parametrize("echo", [9999, 4])
def test_a_foreign_echo_suppresses_the_resend(echo: int) -> None:
    # Die Probe oder das Werkzeug für Teil 2 hat den Watchdog scharf gemacht, nicht dieser core.
    sent, client = _released_with_counters_1_to_3()
    for s in (41, 61, 300):
        client.on_cycle(S(s), required=False, watchdog=WatchdogState.ARMED, echo=echo)
    assert sent == [1, 2, 3, 0]


def test_an_echo_that_left_recent_counts_as_foreign() -> None:
    sent, client = recording(10.0)
    for s in (0, 10, 20, 30):
        client.on_cycle(S(s), required=True)
    client.on_cycle(S(31), required=False)
    assert (sent, client.recent) == ([1, 2, 3, 4, 0], (2, 3, 4))
    client.on_cycle(S(51), required=False, watchdog=WatchdogState.ARMED, echo=1)
    assert sent == [1, 2, 3, 4, 0]
    client.on_cycle(S(52), required=False, watchdog=WatchdogState.ARMED, echo=2)
    assert sent == [1, 2, 3, 4, 0, 0]


def test_the_resend_counts_from_the_last_zero() -> None:
    sent, client = _released_with_counters_1_to_3()
    for s in (40, 41, 60, 61):
        client.on_cycle(S(s), required=False, watchdog=WatchdogState.ARMED, echo=3)
    assert sent == [1, 2, 3, 0, 0, 0]  # bei 41 s und bei 61 s


def test_a_resent_release_needs_release_allowed() -> None:
    sent, client = recording(10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(21), required=False, release_allowed=False, watchdog=WatchdogState.ARMED)
    assert sent == [1, 0]
    client.on_cycle(S(22), required=False, release_allowed=True, watchdog=WatchdogState.ARMED)
    assert sent == [1, 0, 0]


def test_failed_resend_is_retried_next_cycle() -> None:
    sent, send = recorder([True, True, False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    for s in (21, 22, 23):
        client.on_cycle(S(s), required=False, watchdog=WatchdogState.ARMED)
    assert sent == [1, 0, 0, 0]  # 21 s scheitert, 22 s gelingt, 23 s ist zu früh nach der 0


# --- Nach einer Auslösung aufräumen -----------------------------------------------------------


def test_tripped_without_need_is_released_after_60_s() -> None:
    sent, client = recording(interval_s=10.0)
    for s in (0, 59, 60):
        client.on_cycle(T0 + timedelta(seconds=s), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


def test_tripped_release_is_repeated_at_most_every_60_s() -> None:
    sent, client = recording(10.0)
    for s in (0, 60, 61, 119, 120, 180):
        client.on_cycle(S(s), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0, 0, 0]  # bei 60 s, 120 s und 180 s


def test_tripped_release_counts_from_the_first_cycle_that_sees_it() -> None:
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=False, watchdog=WatchdogState.UNKNOWN)
    client.on_cycle(S(100), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(159), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == []
    client.on_cycle(S(160), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


@pytest.mark.parametrize(
    "other", [WatchdogState.ARMED, WatchdogState.WAITING, WatchdogState.UNKNOWN]
)
def test_tripped_count_restarts_when_the_state_changes(other: WatchdogState) -> None:
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(30), required=False, watchdog=other)
    client.on_cycle(S(31), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(60), required=False, watchdog=WatchdogState.TRIPPED)  # 60 s nach dem ersten
    assert sent == []
    client.on_cycle(S(91), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


def test_tripped_release_needs_release_allowed() -> None:
    sent, client = recording(10.0)
    for s in (0, 30, 60, 100):
        client.on_cycle(S(s), required=False, release_allowed=False, watchdog=WatchdogState.TRIPPED)
    assert sent == []
    # hier beginnt die Zählung
    client.on_cycle(S(101), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(160), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == []
    client.on_cycle(S(161), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


def test_tripped_release_is_not_sent_while_required_and_restarts_the_count() -> None:
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(50), required=True, allowed=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(51), required=False, watchdog=WatchdogState.TRIPPED)
    # ohne den Bedarf dazwischen wäre es jetzt fällig
    client.on_cycle(S(60), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == []
    client.on_cycle(S(111), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


def test_tripped_release_does_not_change_the_status() -> None:
    # Der Status folgt den Zählern und der Freigabe nach dem Heartbeat, nicht dem Aufräumen.
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(60), required=False, watchdog=WatchdogState.TRIPPED)
    assert (sent, client.status, client.last_sent) == ([0], "off", None)


def test_tripped_after_a_release_is_cleaned_up_too() -> None:
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(5), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(64), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [1, 0]
    client.on_cycle(S(65), required=False, watchdog=WatchdogState.TRIPPED)
    assert (sent, client.status) == ([1, 0, 0], "released")


def test_active_client_releases_a_tripped_watchdog_at_once() -> None:
    # Die normale Freigabe geht vor: Es ist keine Wartezeit nötig. Danach gilt die Frist von 60 s.
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(1), required=False, watchdog=WatchdogState.TRIPPED)
    assert (sent, client.status) == ([1, 0], "released")
    for s in (2, 30, 60):
        client.on_cycle(S(s), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [1, 0]
    client.on_cycle(S(61), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [1, 0, 0]


def test_failed_tripped_release_is_retried_next_cycle() -> None:
    sent, send = recorder([False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(S(0), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(60), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(61), required=False, watchdog=WatchdogState.TRIPPED)
    client.on_cycle(S(62), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0, 0]  # 60 s scheitert, 61 s gelingt, danach erst wieder 60 s später


# --- Die zuletzt gesendeten Zähler ------------------------------------------------------------


def test_recent_keeps_last_three() -> None:
    _, client = recording(interval_s=10.0)
    for s in range(0, 50, 10):
        client.on_cycle(T0 + timedelta(seconds=s), required=True)
    assert client.recent == (3, 4, 5)


def test_recent_starts_empty() -> None:
    assert recording(10.0)[1].recent == ()


def test_recent_holds_only_counters_that_went_out() -> None:
    sent, send = recorder([False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(S(0), required=True)
    assert client.recent == ()
    client.on_cycle(S(1), required=True)
    assert (sent, client.recent) == ([1, 1], (1,))


def test_recent_survives_the_release() -> None:
    # Eine verlorene Freigabe erkennt der core am Echo: Es muss ein Zähler von vorher sein.
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(10), required=True)
    client.on_cycle(S(11), required=False)
    assert (sent, client.recent) == ([1, 2, 0], (1, 2))


def test_recent_follows_the_wrap() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0, first=HEARTBEAT_MAX)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(10), required=True)
    assert (sent, client.recent) == ([HEARTBEAT_MAX, 1], (HEARTBEAT_MAX, 1))


# --- Startwert je Lauf ------------------------------------------------------------------------


def test_start_value_differs_per_run() -> None:
    noon = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    assert heartbeat_start(noon) == 792_806_400
    assert heartbeat_start(noon + timedelta(seconds=1)) == 792_806_401
    assert 1_000_000 <= heartbeat_start(datetime(2100, 1, 1, tzinfo=UTC)) <= HEARTBEAT_MAX


def test_start_value_stays_above_the_counters_of_the_tools_and_below_the_maximum() -> None:
    # Die Probe und das Teil-2-Werkzeug zählen 1, 2, 3, …; der Startwert beginnt erst bei 1 000 000.
    assert heartbeat_start(datetime.fromtimestamp(0, UTC)) == 1_000_000
    assert heartbeat_start(datetime.fromtimestamp(999_999_999, UTC)) == 1_000_999_999
    assert heartbeat_start(datetime.fromtimestamp(1_000_000_000, UTC)) == 1_000_000  # wieder unten


# --- Eine bestätigte Freigabe lässt den core seine Zähler vergessen ---------------------------


def test_confirmed_release_forgets_own_counters() -> None:
    sent: list[int] = []
    client = HeartbeatClient(lambda v: sent.append(v) or True, 10.0, first=5)
    for s in (0, 10, 20):
        client.on_cycle(S(s), required=True)
    assert client.recent == (5, 6, 7)
    client.on_cycle(S(30), required=False, watchdog=WatchdogState.ARMED, echo=7)
    assert client.recent == (5, 6, 7)  # Freigabe gesendet, noch nicht bestätigt
    client.on_cycle(S(31), required=False, watchdog=WatchdogState.WAITING, echo=0)
    assert client.recent == ()


@pytest.mark.parametrize(
    ("watchdog", "echo"),
    [
        (WatchdogState.WAITING, None),  # „wartet“ genügt, auch ohne bekanntes Echo
        (WatchdogState.WAITING, 3),
        (WatchdogState.ARMED, 0),  # das Echo 0 genügt, gleich was der Zustand sagt
    ],
)
def test_the_release_is_confirmed_by_waiting_or_by_echo_zero(
    watchdog: WatchdogState, echo: int | None
) -> None:
    _, client = _released_with_counters_1_to_3()
    client.on_cycle(S(22), required=False, watchdog=watchdog, echo=echo)
    assert client.recent == ()


@pytest.mark.parametrize(
    ("watchdog", "echo"),
    [
        (WatchdogState.ARMED, 3),  # noch der letzte Zähler: die 0 ging vielleicht verloren
        (WatchdogState.ARMED, None),
        (WatchdogState.TRIPPED, 3),
        (WatchdogState.DISABLED, None),
        (WatchdogState.UNKNOWN, None),
    ],
)
def test_an_unconfirmed_release_keeps_the_counters(
    watchdog: WatchdogState, echo: int | None
) -> None:
    _, client = _released_with_counters_1_to_3()
    client.on_cycle(S(22), required=False, watchdog=watchdog, echo=echo)
    assert client.recent == (1, 2, 3)


def test_the_state_seen_with_the_release_does_not_confirm_it() -> None:
    # Der Zustand, den der Zyklus kennt, in dem die 0 hinausgeht, stammt von davor.
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(1), required=False, watchdog=WatchdogState.WAITING, echo=0)
    assert (sent, client.recent) == ([1, 0], (1,))
    client.on_cycle(S(2), required=False, watchdog=WatchdogState.WAITING, echo=0)
    assert client.recent == ()


def test_waiting_does_not_confirm_a_release_that_was_never_sent() -> None:
    # Beim Scharfschalten meldet der Watchdog „wartet“, während die Zähler schon hinausgehen.
    _, client = recording(10.0)
    for s in (0, 10, 20):
        client.on_cycle(S(s), required=True, watchdog=WatchdogState.WAITING, echo=0)
    assert client.recent == (1, 2, 3)


def test_counting_goes_on_after_a_confirmed_release() -> None:
    sent, client = recording(10.0)
    client.on_cycle(S(0), required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(2), required=False, watchdog=WatchdogState.WAITING, echo=0)
    client.on_cycle(S(3), required=True)
    assert (sent, client.recent) == ([1, 0, 2], (2,))


def test_after_a_confirmed_release_the_old_counters_arm_nothing_for_this_core() -> None:
    # Die Probe macht den Watchdog nach der Freigabe mit 1, 2, 3, … scharf: Das ist nicht der core.
    _, client = _released_with_counters_1_to_3()
    probe = WatchdogStatus(WatchdogState.ARMED, None, None, None, None, heartbeat=2, timeout_s=90)
    assert armed_for_core(probe, client.recent, 90.0) is True  # solange die Freigabe offen ist
    client.on_cycle(S(22), required=False, watchdog=WatchdogState.WAITING, echo=0)
    assert armed_for_core(probe, client.recent, 90.0) is False
