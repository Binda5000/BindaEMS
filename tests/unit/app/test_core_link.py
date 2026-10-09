import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from tests.app_helpers import T_APP

from bindaems.app.core_link import (
    CoreUnavailableError,
    LiveFeed,
    LiveState,
    SlotFlowsHandler,
)
from bindaems.shared.timeutil import ManualClock

STATE_MSG = {
    "type": "state",
    "data": {
        "ts": "2026-10-09T08:00:00+00:00",
        "signals": {"battery.soc_pct": {"v": 55.0, "ts": "2026-10-09T08:00:00+00:00", "q": "ok"}},
        "derived": {"grid_w": 512.0},
    },
}
ALARM_MSG = {
    "type": "alarm",
    "data": [
        {
            "id": "plaus.soc",
            "severity": "warning",
            "message": "Akku-SOC unplausibel",
            "since": "2026-10-09T08:00:00+00:00",
        }
    ],
}
SLOT_MSG = {
    "type": "slot_flows",
    "data": {
        "slot_start": "2026-10-09T07:45:00+00:00",
        "covered_s": 900.0,
        "flows_wh": {"grid>house": 150.0},
        "counters": {},
    },
}
HEALTH_S = 0.05  # Abfragetakt im Test; die Backoff-Wartezeiten zeichnet sleeps auf


class FakeCoreClient:
    """Spielt je Verbindung eine Nachrichtenliste ab; danach ist der core nicht erreichbar."""

    def __init__(self, sessions: list[list[dict[str, Any]]], health: list[Any]) -> None:
        self._sessions = list(sessions)
        self._health = list(health)

    async def health(self) -> dict[str, Any]:
        if not self._health:
            return {"mode": "OBSERVE"}
        item = self._health.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stream(self) -> contextlib.AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]:
        return self._stream()

    @contextlib.asynccontextmanager
    async def _stream(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        if not self._sessions:
            raise CoreUnavailableError("ConnectError: weg")
        messages = self._sessions.pop(0)

        async def replay() -> AsyncIterator[dict[str, Any]]:
            for message in messages:
                yield message

        yield replay()


def feed_env(
    sessions: list[list[dict[str, Any]]],
    *,
    handlers: Sequence[SlotFlowsHandler] = (),
    health: list[Any] | None = None,
) -> tuple[LiveFeed, LiveState, list[float]]:
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        if delay == HEALTH_S:
            await asyncio.sleep(HEALTH_S)
        else:
            sleeps.append(delay)
            await asyncio.sleep(0.001)

    live = LiveState()
    client = FakeCoreClient(sessions, health or [])
    feed = LiveFeed(
        client,  # type: ignore[arg-type]
        live,
        ManualClock(T_APP),
        slot_flows_handlers=handlers,
        health_interval_s=HEALTH_S,
        sleep=sleep,
    )
    return feed, live, sleeps


async def run_feed_until(
    feed: LiveFeed, predicate: Callable[[], bool], max_wait_s: float = 2.0
) -> None:
    task = asyncio.create_task(feed.run())
    try:
        async with asyncio.timeout(max_wait_s):
            while not predicate():
                if task.done():
                    task.result()
                    raise AssertionError("Feed hat sich beendet, Bedingung nie erfüllt")
                await asyncio.sleep(0.005)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def take(messages: AsyncIterator[dict[str, Any]], n: int) -> AsyncIterator[dict[str, Any]]:
    for _ in range(n):
        yield await anext(messages)


async def test_feed_caches_state_and_alarms_and_relays_them() -> None:
    feed, live, _ = feed_env([[STATE_MSG, ALARM_MSG]])
    async with live.subscribe() as messages:
        await run_feed_until(feed, lambda: live.alarms == ALARM_MSG["data"] and not live.connected)
        types = [m["type"] async for m in take(messages, 4)]
    assert types == ["core", "state", "alarm", "core"]
    assert live.state == STATE_MSG["data"]


async def test_slot_flows_go_to_handlers_and_handler_errors_are_contained() -> None:
    received: list[dict[str, Any]] = []

    async def failing(data: dict[str, Any]) -> None:
        raise RuntimeError("kaputt")

    async def collect(data: dict[str, Any]) -> None:
        received.append(data)

    feed, live, _ = feed_env([[SLOT_MSG, STATE_MSG]], handlers=[failing, collect])
    await run_feed_until(feed, lambda: live.state is not None)
    assert received == [SLOT_MSG["data"]]


async def test_disconnect_marks_state_stale_and_notifies() -> None:
    feed, live, _ = feed_env([[STATE_MSG]])
    await run_feed_until(feed, lambda: live.state is not None and not live.connected)
    assert live.value("battery.soc_pct") is None and live.derived() == {}
    assert live.state == STATE_MSG["data"]  # bleibt zur Anzeige erhalten
    assert feed.component_status().message == "core nicht erreichbar seit 10:00"
    assert not feed.component_status().ok


async def test_reconnect_uses_backoff() -> None:
    feed, _, sleeps = feed_env([[], [], []])  # dreimal sofort getrennt
    await run_feed_until(feed, lambda: len(sleeps) >= 3)
    assert sleeps[:3] == [1.0, 2.0, 4.0]


def test_value_only_for_ok_quality_while_connected() -> None:
    live = LiveState()
    live.connected = True
    live.state = {
        "signals": {
            "battery.soc_pct": {"v": 55.0, "ts": "…", "q": "ok"},
            "grid.power_w": {"v": 100.0, "ts": "…", "q": "stale"},
        },
        "derived": {},
    }
    assert live.value("battery.soc_pct") == 55.0 and live.value("grid.power_w") is None
    live.connected = False
    assert live.value("battery.soc_pct") is None


async def test_health_polled_and_cleared_on_failure() -> None:
    feed, live, _ = feed_env(
        [[STATE_MSG]], health=[{"mode": "OBSERVE"}, CoreUnavailableError("weg")]
    )
    await run_feed_until(feed, lambda: live.health == {"mode": "OBSERVE"})
    await run_feed_until(feed, lambda: live.health is None)
