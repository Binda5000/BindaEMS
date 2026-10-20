"""Kennzahlen der Prognosegüte (Plan 3a, Präzisierung 10).

Je lokalem Tag, Art und Horizontklasse stehen Summen in ``forecast_metric``; ``summary`` rechnet
daraus MAE, Bias und relativen MAE über beliebig viele Tage. Fehler = Prognose (``p50_w``) − Ist.
Reine Datenbankarbeit, synchron (Aufrufer nehmen ``asyncio.to_thread``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import cast, get_args

from sqlalchemy import Engine, delete, func, insert, select

from bindaems.app.db.schema import forecast_metric
from bindaems.app.forecast.archive import (
    ForecastArchive,
    HorizonClass,
    Kind,
    SlotSource,
    actuals,
)
from bindaems.shared.timeutil import SLOT, ensure_utc, local_day_slots

# alle Arten des Archivs; das Ist aller ``load*`` ist die Hauslast (Plan 5a, Präzisierung 2)
_KIND_ORDER: tuple[Kind, ...] = get_args(Kind)
_HORIZON_ORDER: tuple[HorizonClass, ...] = get_args(HorizonClass)


@dataclass(frozen=True)
class QualityRow:
    kind: Kind
    horizon: HorizonClass
    n: int
    mae_w: float
    bias_w: float
    rel_mae_pct: float | None


class ForecastQuality:
    def __init__(
        self, engine: Engine, archive: ForecastArchive, ledger: SlotSource, threshold_w: float
    ) -> None:
        self._engine = engine
        self._archive = archive
        self._ledger = ledger
        self._threshold_w = threshold_w

    def update(self, day: date) -> int:
        """Rechnet die Summen des lokalen Tages neu und ersetzt damit seine Zeilen.

        Bei PV zählen nur Slots, in denen Prognose oder Ist die Schwelle erreichen; sonst
        verwässern die Nachtstunden den Fehler. Gibt die Zahl der geschriebenen Zeilen zurück.
        """
        slots = local_day_slots(day)
        start, end = slots[0], slots[-1] + SLOT
        actual = {
            ensure_utc(slot): value for slot, value in actuals(self._ledger, start, end).items()
        }
        sums: dict[tuple[Kind, HorizonClass], list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
        for kind in _KIND_ORDER:
            for record in self._archive.records(kind, start, end):
                known = actual.get(ensure_utc(record.slot_start))
                if known is None:
                    continue
                observed = known.pv_w if kind == "pv" else known.house_w
                if kind == "pv" and max(record.p50_w, observed) < self._threshold_w:
                    continue
                error = record.p50_w - observed
                total = sums[(kind, record.horizon)]
                total[0] += 1
                total[1] += abs(error)
                total[2] += error
                total[3] += observed
        rows = [
            {
                "day": day.isoformat(),
                "kind": kind,
                "horizon": horizon,
                "n": int(total[0]),
                "abs_err_sum_w": total[1],
                "err_sum_w": total[2],
                "actual_sum_w": total[3],
            }
            for (kind, horizon), total in sums.items()
        ]
        with self._engine.begin() as conn:
            conn.execute(delete(forecast_metric).where(forecast_metric.c.day == day.isoformat()))
            if rows:
                conn.execute(insert(forecast_metric), rows)
        return len(rows)

    def summary(self, first: date, last: date) -> list[QualityRow]:
        """Kennzahlen über die lokalen Tage ``first`` bis ``last`` (einschließlich)."""
        table = forecast_metric.c
        query = (
            select(
                table.kind,
                table.horizon,
                func.sum(table.n),
                func.sum(table.abs_err_sum_w),
                func.sum(table.err_sum_w),
                func.sum(table.actual_sum_w),
            )
            .where(table.day >= first.isoformat())
            .where(table.day <= last.isoformat())
            .group_by(table.kind, table.horizon)
        )
        with self._engine.connect() as conn:
            found = conn.execute(query).all()
        result = [
            QualityRow(
                kind=cast(Kind, kind),
                horizon=cast(HorizonClass, horizon),
                n=int(n),
                mae_w=abs_err / n,
                bias_w=err / n,
                rel_mae_pct=abs_err / actual_sum * 100.0 if actual_sum > 0 else None,
            )
            for kind, horizon, n, abs_err, err, actual_sum in found
            if n > 0
        ]
        result.sort(
            key=lambda row: (_KIND_ORDER.index(row.kind), _HORIZON_ORDER.index(row.horizon))
        )
        return result
