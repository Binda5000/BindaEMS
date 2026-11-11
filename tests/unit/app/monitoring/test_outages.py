"""Erkennung und Protokoll der Netzausfälle (Plan „Lebenszeichen und USV-Reichweite“, Aufgabe 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select

from bindaems.app.db.schema import grid_outage
from bindaems.app.monitoring.outages import ISLAND_SOURCE, Outage, OutageTracker
from bindaems.shared.timeutil import ManualClock

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
SOURCE = "system.active_in_source"
SOC = "battery.soc_pct"
LOADS = [f"consumption_output.l{n}.power_w" for n in (1, 2, 3)]


class FakeLive:
    def __init__(self) -> None:
        self.values: dict[str, float | None] = {}

    def set(
        self, source: float | None, soc: float | None = 60.0, loads: tuple[float, ...] = (0, 0, 0)
    ) -> None:
        self.values = {SOURCE: source, SOC: soc}
        self.values.update(dict(zip(LOADS, loads, strict=True)))

    def value(self, signal: str) -> float | None:
        return self.values.get(signal)


def _tracker(engine: Engine, live: FakeLive, clock: ManualClock) -> OutageTracker:
    return OutageTracker(engine, live, clock)  # type: ignore[arg-type]


def _rows(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(grid_outage)).scalar_one()


def test_island_starts_outage(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62)
    tracker.poll()
    current = tracker.current()
    assert current is not None and current.end is None
    assert current.soc_start == 62 and current.start == clock.now()
    assert _rows(engine) == 1


def test_return_closes_outage(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62)
    tracker.poll()
    clock.advance(10)
    live.set(1, 58)
    tracker.poll()
    assert tracker.current() is None
    (outage,) = tracker.recent()
    assert outage.end == clock.now() and outage.soc_end == 58
    assert outage.end_estimated is False
    assert _rows(engine) == 1


def test_unknown_source_changes_nothing(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62)
    tracker.poll()
    clock.advance(10)
    live.set(None, None)
    tracker.poll()
    current = tracker.current()
    assert current is not None and current.end is None
    assert _rows(engine) == 1


def test_ac_out_energy_and_min_soc(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    for soc in (62, 61, 60):
        live.set(ISLAND_SOURCE, soc, (500, 400, 300))
        tracker.poll()
        clock.advance(10)
    current = tracker.current()
    assert current is not None
    assert current.load_wh == pytest.approx(1200 * 20 / 3600)
    assert current.covered_s == pytest.approx(20)
    assert current.soc_min == 60


def test_long_gap_counts_at_most_30_seconds(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62, (100, 100, 100))
    tracker.poll()
    clock.advance(300)
    tracker.poll()
    current = tracker.current()
    assert current is not None and current.covered_s == pytest.approx(30)
    assert current.load_wh == pytest.approx(300 * 30 / 3600)


def test_missing_phase_is_not_counted(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62, (100, 100, 100))
    tracker.poll()
    clock.advance(10)
    live.set(ISLAND_SOURCE, 62, (100, 100, 100))
    live.values[LOADS[1]] = None
    tracker.poll()
    current = tracker.current()
    assert current is not None and current.covered_s == 0 and current.load_wh == 0


def test_progress_is_stored_at_most_every_60_seconds(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62, (100, 100, 100))
    tracker.poll()
    for _ in range(7):
        clock.advance(10)
        tracker.poll()
    with engine.connect() as conn:
        stored = conn.execute(select(grid_outage.c.covered_s)).scalar_one()
    assert stored == pytest.approx(60)


def test_restart_during_outage_continues(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    first = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62)
    first.poll()
    opened = first.current()
    clock.advance(60)
    second = _tracker(engine, live, clock)
    second.poll()
    current = second.current()
    assert opened is not None and current is not None and current.id == opened.id
    assert _rows(engine) == 1


def test_restart_after_return_closes_estimated(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    first = _tracker(engine, live, clock)
    live.set(ISLAND_SOURCE, 62)
    first.poll()
    clock.advance(600)
    second = _tracker(engine, live, clock)
    live.set(None, None)
    second.poll()
    assert second.current() is not None  # Quelle unbekannt: nichts geändert
    clock.advance(10)
    live.set(1, 55)
    second.poll()
    assert second.current() is None
    (outage,) = second.recent()
    assert outage.end == clock.now() and outage.end_estimated is True
    assert outage.soc_end == 55


def test_recent_newest_first(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    for _ in range(25):
        live.set(ISLAND_SOURCE, 60)
        tracker.poll()
        clock.advance(10)
        live.set(1, 60)
        tracker.poll()
        clock.advance(10)
    recent = tracker.recent()
    assert len(recent) == 20
    assert [o.start for o in recent] == sorted((o.start for o in recent), reverse=True)
    assert recent[0].start == clock.now() - timedelta(seconds=20)


def test_mean_w() -> None:
    base = dict(
        id=1, start=T0, end=None, end_estimated=False, soc_start=None, soc_end=None, soc_min=None
    )
    assert Outage(**base, load_wh=100.0, covered_s=3600.0).mean_w == pytest.approx(100)  # type: ignore[arg-type]
    assert Outage(**base, load_wh=10.0, covered_s=899.0).mean_w is None  # type: ignore[arg-type]


def test_open_now_reads_memory_only(engine: Engine, clock: ManualClock) -> None:
    live = FakeLive()
    tracker = _tracker(engine, live, clock)
    assert tracker.open_now() is None
    live.set(ISLAND_SOURCE, 62)
    tracker.poll()
    opened = tracker.open_now()
    assert opened is not None and opened.soc_start == 62
    live.set(1, 60)
    tracker.poll()
    assert tracker.open_now() is None
