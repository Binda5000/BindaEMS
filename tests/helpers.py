"""Gemeinsame Test-Helfer (nur für Tests)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from bindaems.core.state.derived import Derived
from bindaems.core.telemetry.lineprotocol import Point
from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def snap(
    values: Mapping[str, Value],
    *,
    kind: SignalKind = SignalKind.MEASUREMENT,
    quality: Quality = Quality.OK,
    ts: datetime = T0,
    source: str = "victron",
) -> Snapshot:
    """Snapshot, in dem alle ``values`` dieselbe Qualität, Art und Quelle haben."""
    readings = {name: Reading(value, ts, quality, source, kind) for name, value in values.items()}
    return Snapshot(ts, MappingProxyType(readings))


def snap_at(ts: datetime, values: Mapping[str, Value], **kw: object) -> Snapshot:
    return snap(values, ts=ts, **kw)  # type: ignore[arg-type]


async def run_until(adapter: Any, predicate: Callable[[], bool], max_wait_s: float = 1.0) -> None:
    """Startet ``adapter.run()`` als Task, bis ``predicate`` wahr ist; danach wird abgebrochen."""
    task = asyncio.create_task(adapter.run())
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait_s
        while not predicate():
            if task.done():
                task.result()  # Ausnahme des Adapters sichtbar machen
                raise AssertionError("Adapter hat sich beendet, Bedingung nie erfüllt")
            if loop.time() > deadline:
                raise AssertionError("Bedingung innerhalb des Timeouts nicht erfüllt")
            await asyncio.sleep(0.005)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class FakeSink:
    """Sammelt geschriebene Punkte (erfüllt ``PointSink``)."""

    def __init__(self) -> None:
        self.points: list[Point] = []

    def write(self, point: Point) -> None:
        self.points.append(point)


EMPTY_DERIVED = Derived(
    grid_w=None,
    pv_total_w=None,
    consumption_w=None,
    battery_ac_w=None,
    battery_w=None,
    house_load_w=None,
    consumption_l={},
    wallbox_w={},
    wallbox_phase_a={},
    phase_import_a={},
    balance_residual_w=None,
)
