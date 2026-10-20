"""Profil je Auto: Abfahrts- und Zielvorschläge, Konfidenz und Anwesenheitsprognose (Plan 5b, 6).

Aus den Tagesdatensätzen (``driving_days``) und den Ansteckvorgängen entsteht je Wochentag ein
``WeekdayProfile``. Wochentage 0 bis 4 bilden eine Gruppe, Samstag und Sonntag/Feiertag je eine
eigene (6 ist Sonntag und Feiertag). Hat ein Wochentag weniger als ``MIN_DAYS`` bewertete Tage,
rechnet das Profil mit der Gruppe (``basis = "group"``). Unbekannt bleibt unbekannt: Ohne Werte
gibt es keinen Vorschlag, nie einen Ersatzwert. Alle Zeiten des Tages sind lokal (Wiener Zeit).
"""

from __future__ import annotations

import datetime as dt
import math
from bisect import bisect_right
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Final, Literal, Self

from pydantic import AwareDatetime, model_validator

from bindaems.app.daytype import is_holiday
from bindaems.app.learning.driving_data import Source
from bindaems.app.learning.driving_days import DayRecord
from bindaems.shared.config import _Model
from bindaems.shared.control import _local_instant
from bindaems.shared.settings import DrivingSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc

MIN_DAYS: Final = 3
TRIP_SHARE_MIN: Final = 0.5
QUARTERS: Final = 96
NEXT_TRIP_DAYS: Final = 7

# Konfidenz der Abfahrt: Fahrtage des Wochentags selbst und Streuung P80 − P20 in Minuten
DEPARTURE_HIGH: Final = (6, 60.0)
DEPARTURE_MEDIUM: Final = (3, 120.0)
# Konfidenz des Ziels: Werte des Verbrauchs und Streuung P80 − P20 in Prozentpunkten
TARGET_HIGH: Final = (6, 15.0)
TARGET_MEDIUM: Final = (3, 30.0)

GROUPS: Final = ((0, 1, 2, 3, 4), (5,), (6,))

Confidence = Literal["high", "medium", "low"]


def percentile(values: Sequence[float], p: float) -> float:
    """Das ``p``-Perzentil (0 bis 100) mit linearer Interpolation zwischen den Rängen.

    Wie ``numpy.percentile`` mit ``method="linear"``; eine leere Folge ist ein Programmfehler.
    """
    if not values:
        raise ValueError("Ein Perzentil braucht mindestens einen Wert.")
    ordered = sorted(values)
    rank = p / 100 * (len(ordered) - 1)
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


class WeekdayProfile(_Model):
    weekday: int  # 0 = Montag … 6 = Sonntag und Feiertag
    basis: Literal["weekday", "group"]
    days: int  # bewertete Tage, nicht ``away``, auf denen das Profil beruht
    trips: int  # davon Tage mit einer Abfahrt
    trip_share: float
    departure_min: (
        int | None
    )  # Vorschlag: Minute des lokalen Tages, auf die Viertelstunde abgerundet
    departure_p50_min: int | None
    departure_p80_min: int | None
    departure_confidence: Confidence | None
    arrival_p50_min: int | None
    arrival_soc_p50: float | None
    consumption_p50_pp: float | None
    consumption_p80_pp: float | None
    target_soc_pct: int | None
    target_clamped: bool
    target_confidence: Confidence | None


class VehicleProfile(_Model):
    key: str
    source: Source
    data_from: AwareDatetime
    data_to: AwareDatetime
    days: int  # bewertete Tage insgesamt, auch ``away``
    weekdays: list[WeekdayProfile]  # 7, Index = Wochentag
    presence: list[list[float]]  # 7 × 96: Anteil der Tage mit angestecktem Auto je Viertelstunde

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if [w.weekday for w in self.weekdays] != list(range(7)):
            raise ValueError(
                "Ein Profil braucht genau sieben Wochentage in der Reihenfolge 0 bis 6."
            )
        if len(self.presence) != 7 or any(len(row) != QUARTERS for row in self.presence):
            raise ValueError("Die Anwesenheit braucht sieben Tage zu je 96 Viertelstunden.")
        return self


class TripSuggestion(_Model):
    date: dt.date
    departure: str  # HH:MM, lokal
    departure_confidence: Confidence
    target_soc_pct: int | None
    target_confidence: Confidence | None


def _weekday_of(day: dt.date) -> int:
    return 6 if is_holiday(day) else day.weekday()


def _group_of(weekday: int) -> tuple[int, ...]:
    return next(group for group in GROUPS if weekday in group)


def _local_minutes(moment: datetime) -> float:
    local = ensure_utc(moment).astimezone(LOCAL_TZ)
    return local.hour * 60 + local.minute + local.second / 60


def _floor15(minutes: float) -> int:
    return int(minutes // 15) * 15


def _ceil5(value: float) -> int:
    return math.ceil(round(value, 6) / 5) * 5


def _spread(values: Sequence[float]) -> float:
    return percentile(values, 80) - percentile(values, 20)


def _level(
    count: int, spread: float, high: tuple[int, float] | None, medium: tuple[int, float]
) -> Confidence:
    if high is not None and count >= high[0] and spread <= high[1]:
        return "high"
    if count >= medium[0] and spread <= medium[1]:
        return "medium"
    return "low"


def _weekday_profile(
    weekday: int, records: Sequence[DayRecord], settings: DrivingSettings
) -> WeekdayProfile:
    rated = [r for r in records if not r.away]
    own = [r for r in rated if r.weekday == weekday]
    group = _group_of(weekday)
    if len(own) >= MIN_DAYS or group == (weekday,):
        basis: Literal["weekday", "group"] = "weekday"
        sample = own
    else:
        basis = "group"
        sample = [r for r in rated if r.weekday in group]

    trip_days = [r for r in sample if r.departure is not None]
    trips = len(trip_days)
    share = trips / len(sample) if sample else 0.0

    departure_min: int | None = None
    p50: int | None = None
    p80: int | None = None
    departure_confidence: Confidence | None = None
    if trips and share >= TRIP_SHARE_MIN:
        times = [_local_minutes(r.departure) for r in trip_days if r.departure is not None]
        departure_min = _floor15(percentile(times, 20))
        p50 = round(percentile(times, 50))
        p80 = round(percentile(times, 80))
        departure_confidence = _level(
            trips, _spread(times), DEPARTURE_HIGH if basis == "weekday" else None, DEPARTURE_MEDIUM
        )

    arrivals = [_local_minutes(r.arrival) for r in sample if r.arrival is not None]
    arrival_socs = [r.arrival_soc for r in sample if r.arrival_soc is not None]
    consumption = [r.consumption_pp for r in sample if r.consumption_pp is not None]

    target: int | None = None
    clamped = False
    target_confidence: Confidence | None = None
    if consumption:
        wanted = _ceil5(settings.return_reserve_pct + max(0.0, percentile(consumption, 80)))
        target = min(max(wanted, settings.target_min_pct), settings.target_max_pct)
        clamped = target != wanted
        target_confidence = _level(
            len(consumption), _spread(consumption), TARGET_HIGH, TARGET_MEDIUM
        )

    return WeekdayProfile(
        weekday=weekday,
        basis=basis,
        days=len(sample),
        trips=trips,
        trip_share=share,
        departure_min=departure_min,
        departure_p50_min=p50,
        departure_p80_min=p80,
        departure_confidence=departure_confidence,
        arrival_p50_min=round(percentile(arrivals, 50)) if arrivals else None,
        arrival_soc_p50=percentile(arrival_socs, 50) if arrival_socs else None,
        consumption_p50_pp=percentile(consumption, 50) if consumption else None,
        consumption_p80_pp=percentile(consumption, 80) if consumption else None,
        target_soc_pct=target,
        target_clamped=clamped,
        target_confidence=target_confidence,
    )


def _presence(
    sessions: Sequence[tuple[datetime, datetime | None]], *, today: dt.date
) -> list[list[float]]:
    """Anteil der Tage je Wochentag und lokaler Viertelstunde mit angestecktem Auto.

    Gezählt werden die vollständigen Tage ab dem lokalen Tag des ersten Ansteckvorgangs bis vor
    ``today``; ein offener Vorgang gilt bis dahin. Eine Viertelstunde zählt als angesteckt, wenn
    ein Vorgang ihre Mitte überdeckt. An Umstellungstagen mit 92 oder 100 Slots gilt die lokale
    Uhrzeit des Slots: fehlende Viertelstunden fließen nicht ein, doppelte zählen je zur Hälfte.
    """
    zero = [[0.0] * QUARTERS for _ in range(7)]
    spans = sorted(
        (
            ensure_utc(start),
            ensure_utc(end) if end is not None else datetime.max.replace(tzinfo=UTC),
        )
        for start, end in sessions
    )
    if not spans:
        return zero
    starts = [start for start, _ in spans]
    ends_prefix_max: list[datetime] = []
    for _, end in spans:
        ends_prefix_max.append(max(end, ends_prefix_max[-1]) if ends_prefix_max else end)

    def covered(moment: datetime) -> bool:
        # überlappende Vorgänge: der letzte beginnende reicht nicht, das größte Ende davor zählt
        index = bisect_right(starts, moment) - 1
        return index >= 0 and ends_prefix_max[index] > moment

    hits = [[0.0] * QUARTERS for _ in range(7)]
    seen = [[0.0] * QUARTERS for _ in range(7)]
    days_of = [0] * 7
    day = starts[0].astimezone(LOCAL_TZ).date()
    while day < today:
        weekday = _weekday_of(day)
        days_of[weekday] += 1
        begin = _local_instant(day, time(0))
        end_of_day = _local_instant(day + timedelta(days=1), time(0))
        slots = []
        slot = begin
        while slot < end_of_day:
            slots.append(slot)
            slot += SLOT
        per_quarter: dict[int, list[bool]] = {}
        for slot in slots:
            local = slot.astimezone(LOCAL_TZ)
            quarter = (local.hour * 60 + local.minute) // 15
            per_quarter.setdefault(quarter, []).append(covered(slot + SLOT / 2))
        for quarter, flags in per_quarter.items():
            hits[weekday][quarter] += sum(flags) / len(flags)
            seen[weekday][quarter] += 1
        day += timedelta(days=1)

    result = zero
    for weekday in range(7):
        group = _group_of(weekday) if days_of[weekday] < MIN_DAYS else (weekday,)
        for quarter in range(QUARTERS):
            total = sum(seen[w][quarter] for w in group)
            if total:
                result[weekday][quarter] = round(sum(hits[w][quarter] for w in group) / total, 4)
    return result


def build_profile(
    key: str,
    source: Source,
    records: Sequence[DayRecord],
    sessions: Sequence[tuple[datetime, datetime | None]],
    settings: DrivingSettings,
    *,
    today: dt.date,
) -> VehicleProfile:
    """Das Profil eines Autos aus seinen Tagesdatensätzen und Ansteckvorgängen."""
    data_to = _local_instant(today, time(0))
    first = min((r.day for r in records), default=None)
    data_from = _local_instant(first, time(0)) if first is not None else data_to
    return VehicleProfile(
        key=key,
        source=source,
        data_from=data_from,
        data_to=data_to,
        days=len(records),
        weekdays=[_weekday_profile(w, records, settings) for w in range(7)],
        presence=_presence(sessions, today=today),
    )


def next_trip(profile: VehicleProfile, now: datetime) -> TripSuggestion | None:
    """Die nächste erwartete Fahrt: der erste lokale Tag ab heute (höchstens 7 Tage) mit
    Abfahrtsvorschlag, dessen Abfahrt echt nach ``now`` liegt."""
    moment = ensure_utc(now)
    today = moment.astimezone(LOCAL_TZ).date()
    for offset in range(NEXT_TRIP_DAYS):
        day = today + timedelta(days=offset)
        wp = profile.weekdays[_weekday_of(day)]
        if wp.departure_min is None or wp.departure_confidence is None:
            continue
        hour, minute = divmod(wp.departure_min, 60)
        if _local_instant(day, time(hour, minute)) > moment:
            return TripSuggestion(
                date=day,
                departure=f"{hour:02d}:{minute:02d}",
                departure_confidence=wp.departure_confidence,
                target_soc_pct=wp.target_soc_pct,
                target_confidence=wp.target_confidence,
            )
    return None


def presence_at(profile: VehicleProfile, moment: datetime) -> float:
    """Wahrscheinlichkeit, dass das Auto zu ``moment`` angesteckt ist (Viertelstunde, lokal)."""
    local = ensure_utc(moment).astimezone(LOCAL_TZ)
    return profile.presence[_weekday_of(local.date())][(local.hour * 60 + local.minute) // 15]
