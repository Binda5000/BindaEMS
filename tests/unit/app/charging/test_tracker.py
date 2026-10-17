import asyncio
from datetime import timedelta

from sqlalchemy import select
from structlog.testing import capture_logs
from tests.unit.app.charging.conftest import T0

from bindaems.app.charging.tracker import ChargingTracker, WallboxSeen, observe
from bindaems.app.db.schema import assignment_log


def seen(occupant: str | None = None, kind: str | None = None, connected: bool | None = True):
    return [WallboxSeen("evcs", connected, occupant, kind)]


def log_rows(engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.execute(select(assignment_log).order_by(assignment_log.c.id)).all()
    return [(r.ts, r.wallbox, r.occupant, r.kind) for r in rows]


def test_assignment_change_is_logged_once(tracker, engine) -> None:
    tracker.record(T0, seen("polestar", "default"))
    tracker.record(T0 + timedelta(seconds=10), seen("polestar", "default"))
    assert log_rows(engine) == [(T0, "evcs", "polestar", "default")]
    later = T0 + timedelta(minutes=5)
    tracker.record(later, seen("tesla", "detected"))
    assert log_rows(engine) == [
        (T0, "evcs", "polestar", "default"),
        (later, "evcs", "tesla", "detected"),
    ]


def test_first_observation_after_start_is_always_logged(engine, clock, cfg, live) -> None:
    first = ChargingTracker(engine, clock, cfg, live)
    first.record(T0, seen("tesla", "detected"))
    second = ChargingTracker(engine, clock, cfg, live)  # Neustart, dieselbe Belegung
    second.record(T0 + timedelta(minutes=1), seen("tesla", "detected"))
    assert len(log_rows(engine)) == 2


def test_empty_occupant_is_stored_as_empty_text(tracker, engine) -> None:
    tracker.record(T0, seen(None, None))
    assert log_rows(engine) == [(T0, "evcs", "", "")]


def test_observation_without_health_has_no_occupant(live, cfg) -> None:
    live.health = None
    live.state = {"signals": {"wallbox.evcs.status": {"v": 1, "q": "ok"}}}
    result = {s.wallbox: s for s in observe(cfg, live)}
    assert result["evcs"] == WallboxSeen("evcs", True, None, None)
    assert result["twc"] == WallboxSeen("twc", None, None, None)


def test_observation_takes_the_occupant_from_health(live, cfg) -> None:
    live.health = {
        "wallboxes": [
            {"id": "evcs", "occupant": "tesla", "name": "Tesla", "assignment": "detected"},
            {"id": "twc", "occupant": None, "name": None, "assignment": "unclear"},
        ]
    }
    result = {s.wallbox: s for s in observe(cfg, live)}
    assert result["evcs"].occupant == "tesla" and result["evcs"].kind == "detected"
    assert result["twc"].occupant is None and result["twc"].kind == "unclear"


async def test_run_records_on_wake(tracker, live, engine) -> None:
    live.health = {"wallboxes": [{"id": "evcs", "occupant": "tesla", "assignment": "default"}]}
    task = asyncio.create_task(tracker.run())
    try:
        tracker.wake()
        for _ in range(200):
            if log_rows(engine):
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert {(row[1], row[2]) for row in log_rows(engine)} == {("evcs", "tesla"), ("twc", "")}


async def test_run_survives_a_failing_observation(tracker, live, engine, monkeypatch) -> None:
    calls: list[int] = []
    original = tracker.record

    def flaky(now, seen_now) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Datenbank gesperrt")
        original(now, seen_now)

    monkeypatch.setattr(tracker, "record", flaky)
    live.health = {"wallboxes": [{"id": "evcs", "occupant": "tesla", "assignment": "default"}]}
    task = asyncio.create_task(tracker.run())
    try:
        with capture_logs() as logs:
            tracker.wake()
            for _ in range(200):
                if calls:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            tracker.wake()
            for _ in range(200):
                if log_rows(engine):
                    break
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert any(entry["log_level"] == "error" for entry in logs)
    assert log_rows(engine)
