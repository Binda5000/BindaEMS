"""Zeiträume der PV-Seite: Buckets, Ertrag je Bucket und wie weit der Start Tage nachholt.

Ein Bucket ohne einen bekannten Tag ist ``None`` (keine Daten), nicht 0 kWh; Tage nach heute
zählen nie, heute kommt aus dem Cache, frühere Tage aus der Tagestabelle.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from bindaems.app.consumers.days import NO_DAY
from bindaems.app.stats.service import Period

MAX_FILL_DAYS: Final = 800


def pv_fill_days(today: date) -> int:
    """Tage zurück bis zum 1. Januar des Vorjahres, höchstens ``MAX_FILL_DAYS``."""
    return min((today - date(today.year - 1, 1, 1)).days, MAX_FILL_DAYS)


@dataclass(frozen=True)
class PvBucket:
    """Ein Balken: die Tage ``start … end`` (beide eingeschlossen)."""

    start: date
    end: date


def buckets(period: Period, first: date, last: date) -> list[PvBucket]:
    """Tag: ein Bucket; Woche und Monat: je Tag; Jahr: je Monat."""
    if period == "day":
        return [PvBucket(first, last)]
    if period != "year":
        return [PvBucket(d, d) for d in _days(first, last)]
    result: list[PvBucket] = []
    start = first
    while start <= last:
        end = min(last, start.replace(day=calendar.monthrange(start.year, start.month)[1]))
        result.append(PvBucket(start, end))
        start = end + timedelta(days=1)
    return result


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


@dataclass(frozen=True)
class SourceEnergy:
    kwh: list[float | None]  # je Bucket; ``None``: kein Tag des Buckets bekannt
    sum_kwh: float | None  # über die bekannten Tage
    first_known: date | None
    note: str | None  # warum nichts bekannt ist


PeriodEnergy = dict[int, SourceEnergy]
Daily = Mapping[int, Mapping[date, tuple[float | None, str | None]]]


def _sum(values: Sequence[float]) -> float | None:
    return round(math.fsum(values), 3) if values else None


def period_energy(
    daily: Daily,
    today_kwh: Mapping[int, float | None],
    period_buckets: Sequence[PvBucket],
    today: date,
) -> PeriodEnergy:
    """Ertrag je Quelle (und ``0`` = PV gesamt) und Bucket.

    ``daily`` sind die gespeicherten Tage, ``today_kwh`` der Stand von heute je Quelle; deren
    Schlüssel und die von ``daily`` sind die Quellen."""
    result: PeriodEnergy = {}
    for sid in sorted(set(daily) | set(today_kwh)):
        stored = daily.get(sid, {})
        known: dict[date, float] = {}
        reason: str | None = None
        per_bucket: list[float | None] = []
        for bucket in period_buckets:
            values: list[float] = []
            for day in _days(bucket.start, bucket.end):
                if day > today:
                    continue
                if day == today:
                    kwh = today_kwh.get(sid)
                else:
                    kwh, note = stored.get(day, (None, None))
                    if kwh is None:
                        reason = reason or note
                if kwh is not None:
                    known[day] = kwh
                    values.append(kwh)
            per_bucket.append(_sum(values))
        result[sid] = SourceEnergy(
            per_bucket,
            _sum(list(known.values())),
            min(known) if known else None,
            None if known else (reason or NO_DAY),
        )
    return result


def unassigned(
    total: SourceEnergy, systems: Sequence[SourceEnergy]
) -> tuple[list[float | None], float | None]:
    """„Nicht zugeordnet“ je Bucket: PV gesamt minus oberste Anlagen (mindestens 0); ``None``,
    wenn eines davon im Bucket unbekannt ist. Dazu die Summe der bekannten Buckets."""
    rest: list[float | None] = []
    for index, whole in enumerate(total.kwh):
        parts = [system.kwh[index] for system in systems]
        if whole is None or any(part is None for part in parts):
            rest.append(None)
        else:
            known = [part for part in parts if part is not None]
            rest.append(round(max(0.0, whole - math.fsum(known)), 3))
    return rest, _sum([value for value in rest if value is not None])
