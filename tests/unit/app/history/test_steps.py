from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.app.learning.conftest import FakeReader

from bindaems.app.history.influx import InfluxQueryError, Series
from bindaems.app.history.steps import StatusSteps, read_status_steps
from bindaems.shared.influx.lineprotocol import epoch_ms

START = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
END = START + timedelta(hours=1)
SLOT = timedelta(minutes=15)
A = "thermal.klima.oil_burner"
B = "thermal.klima.running"


def _changes(item_id: str, *rows: tuple[datetime, object]) -> Series:
    return Series(
        "status", {"id": item_id}, ["time", "value_bool"], [[epoch_ms(t), v] for t, v in rows]
    )


def _seed(item_id: str, ts: datetime, value: object) -> Series:
    return Series("status", {"id": item_id}, ["time", "last"], [[epoch_ms(ts), value]])


def test_share_is_time_fraction_true() -> None:
    steps = StatusSteps(False, [START + timedelta(minutes=3)], [True])
    assert steps.share(START, START + SLOT) == pytest.approx(0.8)
    assert steps.share(START + SLOT, START + 2 * SLOT) == 1.0


def test_share_with_unknown_start_is_none() -> None:
    steps = StatusSteps(None, [START + timedelta(minutes=5)], [True])
    assert steps.share(START, START + SLOT) is None
    assert steps.share(START + SLOT, START + 2 * SLOT) == 1.0


def test_change_exactly_at_slot_boundary() -> None:
    steps = StatusSteps(True, [START + SLOT], [False])
    assert steps.share(START, START + SLOT) == 1.0  # die Änderung gehört zur nächsten
    assert steps.share(START + SLOT, START + 2 * SLOT) == 0.0


def test_several_changes_in_one_slot() -> None:
    steps = StatusSteps(
        True,
        [START + timedelta(minutes=5), START + timedelta(minutes=10)],
        [False, True],
    )
    assert steps.share(START, START + SLOT) == pytest.approx(10 / 15)


async def test_read_status_steps_seeds_and_changes() -> None:
    reader = FakeReader()
    reader.answer("last(", [_seed(A, START - timedelta(days=3), True)])
    reader.answer(
        'SELECT "value_bool"',
        [
            _changes(A, (START + 2 * SLOT, True), (START + SLOT, False)),
            _changes(B, (START + SLOT, True), (START + 3 * SLOT, "kaputt")),
            _changes("thermal.anders.running", (START, True)),
        ],
    )
    steps = await read_status_steps(reader, [A, B], START, END)
    assert set(steps) == {A, B}
    assert steps[A] == StatusSteps(True, [START + SLOT, START + 2 * SLOT], [False, True])
    assert steps[B] == StatusSteps(None, [START + SLOT], [True])  # ohne Wert davor: unbekannt
    changes, earlier = (q for q, _ in reader.queries)
    assert f"time >= {epoch_ms(START)}ms AND time < {epoch_ms(END)}ms" in changes
    assert f"\"id\" = '{A}' OR \"id\" = '{B}'" in changes and 'GROUP BY "id"' in changes
    assert f"time < {epoch_ms(START)}ms" in earlier and '"status"' in earlier


async def test_read_status_steps_quotes_ids() -> None:
    reader = FakeReader()
    await read_status_steps(reader, ["a'b"], START, END)
    assert all("'a\\'b'" in q for q, _ in reader.queries)


async def test_read_status_steps_without_ids_reads_nothing() -> None:
    reader = FakeReader()
    assert await read_status_steps(reader, [], START, END) == {}
    assert reader.queries == []


async def test_read_error_propagates() -> None:
    reader = FakeReader()
    reader.answer('"status"', InfluxQueryError("weg"))
    with pytest.raises(InfluxQueryError):
        await read_status_steps(reader, [A], START, END)
