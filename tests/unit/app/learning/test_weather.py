from datetime import UTC, datetime, timedelta

import pytest

from bindaems.app.forecast.openmeteo import WeatherSlot
from bindaems.app.learning.weather import BACKFILL_MIN_AGE, LEAD, WeatherArchive

ISSUED = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


@pytest.fixture
def archive(engine) -> WeatherArchive:
    return WeatherArchive(engine)


def slots(start: datetime, count: int, temp_c: float) -> list[WeatherSlot]:
    return [WeatherSlot(start + i * SLOT, 0.0, temp_c) for i in range(count)]


def test_record_keeps_only_slots_up_to_six_hours_ahead(archive) -> None:
    first = ISSUED - timedelta(hours=24)  # der Vortag aus past_days=1 gehört dazu
    written = archive.record(slots(first, 24 * 4 + 40, 10.0), ISSUED)
    kept = archive.temperatures(first - timedelta(days=1), ISSUED + timedelta(days=3))
    assert written == len(kept) == 30 * 4  # 24 h davor und 6 h danach: bis 11:45 Uhr
    assert max(kept) == ISSUED + LEAD - SLOT
    assert archive.oldest() == first


def test_record_returns_zero_without_usable_slots(archive) -> None:
    assert archive.record([], ISSUED) == 0
    assert archive.record(slots(ISSUED + LEAD, 4, 10.0), ISSUED) == 0
    assert archive.oldest() is None


def test_newer_issue_replaces_older(archive) -> None:
    archive.record(slots(ISSUED, 8, 10.0), ISSUED)
    later = ISSUED + timedelta(hours=1)
    archive.record(slots(ISSUED + timedelta(hours=1), 8, 12.5), later)
    found = archive.temperatures(ISSUED, ISSUED + timedelta(hours=3))
    assert found[ISSUED] == 10.0 and found[ISSUED + timedelta(minutes=45)] == 10.0
    assert found[ISSUED + timedelta(hours=1)] == 12.5  # überschrieben
    assert found[ISSUED + timedelta(hours=2, minutes=45)] == 12.5  # neu dazu


def test_older_issue_does_not_replace_newer(archive) -> None:
    archive.record(slots(ISSUED, 4, 12.5), ISSUED)
    assert archive.record(slots(ISSUED, 4, 10.0), ISSUED - timedelta(hours=1)) == 0
    assert set(archive.temperatures(ISSUED, ISSUED + timedelta(hours=1)).values()) == {12.5}


def test_temperatures_are_half_open(archive) -> None:
    archive.record(slots(ISSUED, 8, 10.0), ISSUED)
    end = ISSUED + timedelta(hours=1)
    found = archive.temperatures(ISSUED + SLOT, end)
    assert min(found) == ISSUED + SLOT and max(found) == end - SLOT


def history(start: datetime, count: int, temp_c: float) -> dict[datetime, float]:
    return {start + i * SLOT: temp_c for i in range(count)}


class Fetcher:
    def __init__(self, result: dict[datetime, float] | Exception) -> None:
        self.result, self.calls = result, 0

    async def __call__(self) -> dict[datetime, float]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_backfill_runs_once_when_archive_is_young(archive) -> None:
    start = ISSUED - timedelta(days=92)
    fetch = Fetcher(history(start, 92 * 96 + 40, 8.0))  # reicht bis in den Rest des Tages
    assert await archive.backfill(fetch, ISSUED) == 92 * 96 + 24  # nur bis 6 h nach „jetzt“
    assert archive.oldest() == start
    assert await archive.backfill(fetch, ISSUED) == 0
    assert fetch.calls == 1  # nach dem Nachholen ist das Archiv 92 Tage alt


async def test_backfill_runs_when_archive_is_just_under_56_days_old(archive) -> None:
    archive.record(slots(ISSUED - BACKFILL_MIN_AGE + SLOT, 4, 5.0), ISSUED - timedelta(days=1))
    fetch = Fetcher({})
    await archive.backfill(fetch, ISSUED)
    assert fetch.calls == 1


async def test_backfill_is_skipped_when_archive_has_56_days(archive) -> None:
    archive.record(slots(ISSUED - BACKFILL_MIN_AGE, 4, 5.0), ISSUED - BACKFILL_MIN_AGE)
    fetch = Fetcher(history(ISSUED - timedelta(days=92), 10, 8.0))
    assert await archive.backfill(fetch, ISSUED) == 0 and fetch.calls == 0


async def test_backfill_does_not_overwrite_recorded_slots(archive) -> None:
    archive.record(slots(ISSUED - timedelta(hours=2), 8, 3.0), ISSUED)
    fetched = history(ISSUED - timedelta(hours=4), 16, 9.0)
    assert await archive.backfill(Fetcher(fetched), ISSUED + timedelta(hours=1)) == 8
    found = archive.temperatures(ISSUED - timedelta(hours=4), ISSUED + timedelta(hours=1))
    assert found[ISSUED - timedelta(hours=4)] == 9.0  # Lücke gefüllt
    assert found[ISSUED - timedelta(hours=2)] == 3.0  # Bestand bleibt
    assert found[ISSUED - SLOT] == 3.0


async def test_backfill_error_propagates_and_leaves_archive_empty(archive) -> None:
    with pytest.raises(RuntimeError):
        await archive.backfill(Fetcher(RuntimeError("kein Netz")), ISSUED)
    assert archive.oldest() is None
