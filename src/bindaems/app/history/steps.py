"""Zustandsbits aus InfluxDB ``status`` als Stufenfunktion (Plan 5c, Plan Ölheizung).

Der Sampler schreibt ein Bit nur bei Änderung. Den Zustand in einem Fenster ergeben darum der
letzte Wert vor dem Fenster (``seed``) und alle Änderungen darin; daraus entsteht der Zeitanteil
„wahr“ eines Abschnitts. Ohne Wert vor dem Fenster ist der Anfang unbekannt (``None``).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bindaems.app.history.influx import InfluxReader, Series, quote_str
from bindaems.shared.influx.lineprotocol import epoch_ms


def series_rows(series: Sequence[Series]) -> list[tuple[datetime, dict[str, Any]]]:
    """Die Zeilen der Antwort als (Zeit UTC, Spalten), nach Zeit sortiert; Zeilen ohne
    gültige Zeit (ms seit der Epoche) fallen weg."""
    rows: list[tuple[datetime, dict[str, Any]]] = []
    for item in series:
        for row in item.values:
            if not row or isinstance(row[0], bool) or not isinstance(row[0], int):
                continue
            ts = datetime.fromtimestamp(row[0] / 1000, UTC)
            rows.append((ts, dict(zip(item.columns, row, strict=False))))
    rows.sort(key=lambda entry: entry[0])
    return rows


@dataclass(frozen=True)
class StatusSteps:
    """Stufenfunktion eines Bits: ``seed`` vor dem Fenster und die Änderungen darin."""

    seed: bool | None
    times: list[datetime]
    values: list[bool]

    def share(self, start: datetime, end: datetime) -> float | None:
        """Zeitanteil „wahr“ in ``[start, end)``; ``None``, wenn der Anfang unbekannt ist."""
        index = bisect_right(self.times, start) - 1
        state = self.values[index] if index >= 0 else self.seed
        if state is None:
            return None
        true_s = 0.0
        cursor = start
        for ts, value in zip(self.times[index + 1 :], self.values[index + 1 :], strict=True):
            if ts >= end:
                break
            if state:
                true_s += (ts - cursor).total_seconds()
            state, cursor = value, ts
        if state:
            true_s += (end - cursor).total_seconds()
        return true_s / (end - start).total_seconds()


async def read_status_steps(
    reader: InfluxReader, ids: Sequence[str], start: datetime, end: datetime
) -> dict[str, StatusSteps]:
    """Die Stufenfunktion je ``id`` aus ``status`` für ``[start, end)``: der letzte Wert vor
    ``start`` (sonst ``seed=None``) und die Änderungen im Fenster. Jede ``id`` steht im Ergebnis.
    Ein Fehler beim Lesen wirft ``InfluxQueryError``."""
    if not ids:
        return {}
    wanted = list(dict.fromkeys(ids))
    condition = " OR ".join(f'"id" = {quote_str(item_id)}' for item_id in wanted)
    window = f"time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms"
    # Werte sind maskiert, Feld und Messung sind Konstanten
    changes = await reader.query(
        f'SELECT "value_bool" FROM "status" WHERE ({condition}) AND {window} '  # noqa: S608
        'GROUP BY "id"'
    )
    earlier = await reader.query(
        f'SELECT last("value_bool") FROM "status" WHERE ({condition}) '  # noqa: S608
        f'AND time < {epoch_ms(start)}ms GROUP BY "id"'
    )
    seeds: dict[str, bool | None] = dict.fromkeys(wanted)
    for item in earlier:
        item_id = item.tags.get("id", "")
        for _, cells in series_rows([item]):
            value = cells.get("last")
            if item_id in seeds and isinstance(value, bool):
                seeds[item_id] = value
    events: dict[str, list[tuple[datetime, bool]]] = {item_id: [] for item_id in wanted}
    for item in changes:
        item_id = item.tags.get("id", "")
        if item_id not in events:
            continue
        for ts, cells in series_rows([item]):
            value = cells.get("value_bool")
            if isinstance(value, bool):
                events[item_id].append((ts, value))
    result: dict[str, StatusSteps] = {}
    for item_id in wanted:
        ordered = sorted(events[item_id], key=lambda event: event[0])
        result[item_id] = StatusSteps(
            seeds[item_id], [t for t, _ in ordered], [v for _, v in ordered]
        )
    return result
