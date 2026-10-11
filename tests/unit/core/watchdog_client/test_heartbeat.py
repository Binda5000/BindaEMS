from collections.abc import Callable, Iterable
from datetime import timedelta

from tests.helpers import T0

from bindaems.core.watchdog_client.heartbeat import HEARTBEAT_MAX, HeartbeatClient

S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def recorder(results: Iterable[bool] = ()) -> tuple[list[int], Callable[[int], bool]]:
    sent: list[int] = []
    answers = iter(results)

    def send(value: int) -> bool:
        sent.append(value)
        return next(answers, True)

    return sent, send


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
