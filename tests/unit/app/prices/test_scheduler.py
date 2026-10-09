from datetime import UTC, date, datetime

import pytest
from tests.app_helpers import StopScheduler, record_then_stop

from bindaems.app.prices.pipeline import PriceStatus
from bindaems.app.prices.scheduler import PriceScheduler, next_price_fetch
from bindaems.shared.timeutil import LOCAL_TZ, ManualClock


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=LOCAL_TZ)


class FakePipeline:
    """Liefert je ``refresh`` einen Status mit oder ohne Erfolg (oder löst die Ausnahme aus)."""

    def __init__(self, successes: list[bool | Exception]) -> None:
        self._successes = list(successes)
        self.calls = 0

    async def refresh(self) -> PriceStatus:
        self.calls += 1
        outcome = self._successes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        now = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)
        return PriceStatus(now, now if outcome else None, "gross", None, [], [])


class FakeStore:
    def __init__(self, complete: bool) -> None:
        self.complete = complete
        self.asked: list[tuple[date, str | None]] = []

    def complete_day(self, day: date, origin: str | None = None) -> bool:
        self.asked.append((day, origin))
        return self.complete


@pytest.mark.parametrize(
    ("now", "complete", "expected"),
    [
        ("2026-10-09T10:00", False, "2026-10-09T10:02"),
        ("2026-10-09T12:20", False, "2026-10-09T12:30"),
        ("2026-10-09T12:31", False, "2026-10-09T12:45"),
        ("2026-10-09T15:50", False, "2026-10-09T16:02"),
        ("2026-10-09T12:31", True, "2026-10-09T13:02"),
    ],
)
def test_price_fetch_times(now: str, complete: bool, expected: str) -> None:
    assert next_price_fetch(local(now), complete) == local(expected)


def test_price_fetch_on_autumn_dst_day() -> None:  # 02:02 gibt es zweimal
    now = datetime(2026, 10, 25, 0, 3, tzinfo=UTC)  # 02:03 MESZ
    assert next_price_fetch(now, True) == datetime(2026, 10, 25, 1, 2, tzinfo=UTC)  # 02:02 MEZ


async def test_scheduler_refreshes_at_start_and_retries_after_failure() -> None:
    pipeline = FakePipeline(successes=[False, True])
    sleeps: list[float] = []
    store = FakeStore(complete=True)
    scheduler = PriceScheduler(
        pipeline,
        store,
        ManualClock(local("2026-10-09T10:30")),
        sleep=record_then_stop(sleeps, after=2),
    )
    with pytest.raises(StopScheduler):
        await scheduler.run()
    assert pipeline.calls == 2 and sleeps == [300.0, 32 * 60.0]
    assert store.asked[0] == (date(2026, 10, 10), "primary")


async def test_scheduler_survives_refresh_error() -> None:
    pipeline = FakePipeline(successes=[RuntimeError("kaputt"), True])
    sleeps: list[float] = []
    scheduler = PriceScheduler(
        pipeline,
        FakeStore(complete=False),
        ManualClock(local("2026-10-09T10:30")),
        sleep=record_then_stop(sleeps, after=2),
    )
    with pytest.raises(StopScheduler):
        await scheduler.run()
    assert pipeline.calls == 2 and sleeps[0] == 300.0
