"""Gemeinsame Test-Helfer (nur für Tests)."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from bindaems.app.history.influx import Series
from bindaems.app.ledger.counters import counter_tags
from bindaems.core.state.derived import Derived
from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot, Value
from bindaems.shared.influx.lineprotocol import Point, epoch_ms

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


async def stop(task: asyncio.Task) -> None:
    """Bricht ``task`` ab und wartet darauf; ein ``CancelledError`` des Tasks wird geschluckt."""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class FakeSink:
    """Sammelt geschriebene Punkte (erfüllt ``PointSink``)."""

    def __init__(self) -> None:
        self.points: list[Point] = []

    def write(self, point: Point) -> None:
        self.points.append(point)


_STATEMENT = re.compile(
    r'SELECT (?P<select>last|first)\("kwh"\) AS "(?P<column>\w+)" '
    r'FROM "(?P<rp>raw|long)"\."energy" '
    r"WHERE time >= (?P<low>\d+)ms AND time < (?P<high>\d+)ms"
)
MINUTE_MS = 60_000


def _stamped(values: dict[datetime, float], rp: str) -> list[tuple[int, float]]:
    """Die Punkte, wie sie in der Aufbewahrung ``rp`` stehen: in ``raw`` mit dem Lesezeitpunkt, in
    ``long`` wie von der CQ ``cq_energy_1m`` (``last(kwh) … GROUP BY time(1m)``) je Minute der
    letzte Wert, gestempelt mit dem Anfang der Minute."""
    readings = sorted((epoch_ms(t), v) for t, v in values.items())
    if rp == "raw":
        return readings
    buckets: dict[int, float] = {}
    for stamp, value in readings:
        buckets[stamp // MINUTE_MS * MINUTE_MS] = value  # sortiert: der letzte bleibt
    return sorted(buckets.items())


class FakeInflux:
    """Ersatz für den ``InfluxReader``: merkt sich die Abfragen und liefert vorbereitete Reihen.

    Mit ``set_counters`` liegen Rohwerte der Zähler bereit; Anweisungen der Form
    ``SELECT last|first("kwh") AS "<Spalte>" FROM "raw"|"long"."energy" WHERE time >= …ms AND
    time < …ms`` (mehrere durch ``;`` getrennt) beantwortet die Attrappe dann wie InfluxDB: je
    Zähler der letzte bzw. erste Punkt im Fenster mit seiner Zeit. In ``raw`` ist das der
    Lesezeitpunkt, in ``long`` der Anfang der Minute wie bei der CQ (``_stamped``)."""

    def __init__(self) -> None:
        self.series: list[Series] = []
        self.queries: list[str] = []
        self.error: Exception | None = None
        self.points: dict[str, dict[datetime, float]] | None = None

    async def query(self, q: str, *, database: str | None = None) -> list[Series]:
        self.queries.append(q)
        if self.error is not None:
            raise self.error
        if self.points is None:
            return self.series
        result: list[Series] = []
        for statement in q.split(";"):
            match = _STATEMENT.search(statement)
            if match is None:
                continue
            low, high = int(match["low"]), int(match["high"])
            for signal, values in self.points.items():
                inside = [(t, v) for t, v in _stamped(values, match["rp"]) if low <= t < high]
                if not inside:
                    continue
                stamp, value = inside[-1] if match["select"] == "last" else inside[0]
                result.append(
                    Series("energy", _tags(signal), ["time", match["column"]], [[stamp, value]])
                )
        return result

    def set_counters(self, readings: dict[str, dict[datetime, float]]) -> None:
        """Rohwerte der Zähler je Signal und Lesezeitpunkt (``long`` entsteht daraus wie per CQ)."""
        self.points = readings


def _tags(signal: str) -> dict[str, str]:
    source, ident, direction = counter_tags(signal) or ("", "", "")
    return {"source": source, "id": ident, "direction": direction}


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

# Die Felder der Absicht für die Laderegelung und den Gast im Standard, so wie JSON-Antwort und
# Datei sie zeigen
INTENT_CHARGING_DEFAULTS: dict[str, Any] = {
    "vehicles": {},
    "priority": None,
    "surplus_priority": {"kind": "battery_until_soc", "soc_pct": 30.0},
    "guest": {"mode": "pv", "now_max_a": None},
    "plan": None,
    "support": {"allowed": True, "soc_pct": None},
}
