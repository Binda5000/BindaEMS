"""Zeiträume der PV-Seite: Buckets, Ertrag je Bucket und wie weit der Start Tage nachholt.

Ein Bucket ohne einen bekannten Tag ist ``None`` (keine Daten), nicht 0 kWh; Tage nach heute
zählen nie, heute kommt aus dem Cache, frühere Tage aus der Tagestabelle.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    by_day: dict[date, float] = field(default_factory=dict)  # die bekannten Tage


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
            known,
        )
    return result


def unassigned(
    total: SourceEnergy, systems: Sequence[SourceEnergy], period_buckets: Sequence[PvBucket]
) -> tuple[list[float | None], float | None]:
    """„Nicht zugeordnet“ je Tag: PV gesamt minus oberste Anlagen (mindestens 0), nur an Tagen,
    an denen alle bekannt sind; je Bucket summiert (``None``: kein solcher Tag). Beginnt eine
    Anlage mitten im Monat, zählt der Monat so erst ab ihrem ersten Tag. Dazu die Summe der
    bekannten Buckets."""
    rest: list[float | None] = []
    for bucket in period_buckets:
        values: list[float] = []
        for day in _days(bucket.start, bucket.end):
            whole = total.by_day.get(day)
            parts = [system.by_day[day] for system in systems if day in system.by_day]
            if whole is not None and len(parts) == len(systems):
                values.append(max(0.0, whole - math.fsum(parts)))
        rest.append(_sum(values))
    return rest, _sum([value for value in rest if value is not None])
