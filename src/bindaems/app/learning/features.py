"""Merkmalsbasis des Lastmodells (Plan 5a, Entscheidungen 5 und 8, Präzisierung 4).

Reine Rechnung ohne IO. Ein Merkmalsvektor gehört zu einem Ziel-Slot und einem Ausgabezeitpunkt;
er enthält nur, was zur Ausgabe bekannt war: Kalender, Temperatur (archiviert im Training, als
Prognose im Betrieb), verzögerte Last vor dem Slot der Ausgabe, das Profil des Ausgabetags und die
Sensorwerte des letzten vollständigen Slots. Fehlende Werte sind ``NaN``.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from bindaems.app.daytype import day_type, is_holiday
from bindaems.app.forecast.load import WINDOW_DAYS, LoadProfile, LoadSample, build_profile
from bindaems.shared.timeutil import (
    LOCAL_TZ,
    SLOT,
    ensure_utc,
    local_day_slots,
    slot_start,
    slots_between,
)

ISSUE_HOURS: Final = (0, 6, 12, 18)
MAX_LEAD: Final = timedelta(hours=72)
BASE_FEATURES: Final[tuple[str, ...]] = (
    "lead_h",
    "minute_of_day",
    "weekday",
    "day_type",
    "holiday",
    "month",
    "temp_c",
    "temp_day_c",
    "lag_1d_w",
    "lag_7d_w",
    "recent_w",
    "profile_w",
)
SENSOR_PREFIX: Final = "ha:"
RECENT_SLOTS: Final = 4  # die letzte Stunde vor der Ausgabe
_NAN: Final = float("nan")
_DAY_TYPE_CODE: Final = {"workday": 0.0, "saturday": 1.0, "sunday": 2.0}
_HOUR: Final = timedelta(hours=1)


def feature_names(sensors: Sequence[str]) -> tuple[str, ...]:
    """Die festen Merkmale, dann ``ha:<entity_id>`` je Sensor in der Reihenfolge der Auswahl."""
    return (*BASE_FEATURES, *(f"{SENSOR_PREFIX}{entity_id}" for entity_id in sensors))


@dataclass(frozen=True)
class FeatureInputs:
    """Alle Eingaben der Merkmale, je Slot-Beginn (UTC) in W, °C bzw. der Einheit des Sensors."""

    load_w: Mapping[datetime, float]
    temp_c: Mapping[datetime, float]
    sensors: Mapping[str, Mapping[datetime, float]]
    # Profil, das am lokalen Tag gilt: gebaut nur aus Stichproben vor dessen Mitternacht
    profile_for: Callable[[date], LoadProfile | None]


@dataclass(frozen=True)
class TrainingSet:
    rows: list[list[float]]
    targets: list[float]
    issued: list[datetime]
    slots: list[datetime]


def issuances(start: datetime, end: datetime) -> list[datetime]:
    """Die lokalen Ausgabezeitpunkte 00, 06, 12 und 18 Uhr in ``[start, end)``, als UTC."""
    first, stop = ensure_utc(start), ensure_utc(end)
    day = first.astimezone(LOCAL_TZ).date()
    last = stop.astimezone(LOCAL_TZ).date()
    found: list[datetime] = []
    while day <= last:
        for hour in ISSUE_HOURS:
            at = datetime.combine(day, time(hour), tzinfo=LOCAL_TZ).astimezone(UTC)
            if first <= at < stop:
                found.append(at)
        day += timedelta(days=1)
    return found


def local_midnight(day: date) -> datetime:
    """Beginn des lokalen Kalendertags, als UTC."""
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ).astimezone(UTC)


def profile_cache(samples: Sequence[LoadSample]) -> Callable[[date], LoadProfile | None]:
    """Profil je lokalem Tag, gebaut nur aus Stichproben vor dessen Mitternacht (wie im Betrieb
    am Morgen) und je Tag einmal gerechnet. Ohne Stichproben im Fenster gibt es kein Profil."""
    ordered = sorted(samples, key=lambda item: ensure_utc(item.slot_start))
    starts = [ensure_utc(item.slot_start) for item in ordered]
    cache: dict[date, LoadProfile | None] = {}

    def profile_for(day: date) -> LoadProfile | None:
        if day not in cache:
            midnight = local_midnight(day)
            lo = bisect_left(starts, midnight - timedelta(days=WINDOW_DAYS))
            hi = bisect_left(starts, midnight)
            profile = build_profile(ordered[lo:hi], midnight) if hi > lo else None
            cache[day] = profile if profile is not None and profile.overall is not None else None
        return cache[day]

    return profile_for


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else _NAN


def _local_shift(slot: datetime, days: int) -> datetime | None:
    """Der Slot mit derselben lokalen Uhrzeit ``days`` Kalendertage früher. Gibt es die Uhrzeit
    an diesem Tag nicht (Frühjahrsumstellung), ``None``; gibt es sie zweimal (Herbstumstellung),
    gilt das erste Mal."""
    local = slot.astimezone(LOCAL_TZ)
    wall = datetime.combine(local.date() - timedelta(days=days), local.time())
    shifted = wall.replace(tzinfo=LOCAL_TZ).astimezone(UTC)
    if shifted.astimezone(LOCAL_TZ).replace(tzinfo=None) != wall:
        return None
    return shifted


class _Lookups:
    """Zwischenspeicher je Tag, damit Tausende Zeilen nicht dasselbe immer neu rechnen."""

    def __init__(self, inputs: FeatureInputs) -> None:
        self.inputs = inputs
        self._calendar: dict[date, tuple[float, float, float, float]] = {}
        self._temp_day: dict[date, float] = {}
        self._profile: dict[date, LoadProfile | None] = {}

    def calendar(self, day: date) -> tuple[float, float, float, float]:
        """Wochentag, Tagestyp, Feiertag und Monat des lokalen Tags."""
        if day not in self._calendar:
            self._calendar[day] = (
                float(day.weekday()),
                _DAY_TYPE_CODE[day_type(day)],
                float(is_holiday(day)),
                float(day.month),
            )
        return self._calendar[day]

    def temp_day(self, day: date) -> float:
        if day not in self._temp_day:
            temps = self.inputs.temp_c
            self._temp_day[day] = _mean([temps[s] for s in local_day_slots(day) if s in temps])
        return self._temp_day[day]

    def profile(self, day: date) -> LoadProfile | None:
        if day not in self._profile:
            self._profile[day] = self.inputs.profile_for(day)
        return self._profile[day]


def _check(names: Sequence[str]) -> None:
    for name in names:
        if name not in BASE_FEATURES and not name.startswith(SENSOR_PREFIX):
            raise ValueError(f"Merkmal unbekannt: {name}")


def _rows(
    lookups: _Lookups, issued_at: datetime, slots: Sequence[datetime], names: Sequence[str]
) -> list[list[float]]:
    inputs = lookups.inputs
    load = inputs.load_w
    issued = ensure_utc(issued_at)
    cutoff = slot_start(issued)  # was ab diesem Slot liegt, war zur Ausgabe nicht vollständig
    recent = _mean(
        [load[s] for s in (cutoff - k * SLOT for k in range(1, RECENT_SLOTS + 1)) if s in load]
    )
    profile = lookups.profile(issued.astimezone(LOCAL_TZ).date())
    held: list[float] = []  # Sensorwerte der gefragten Namen, gehalten über den ganzen Vorlauf
    picks: list[int] = []  # Spalte der Zeile: Index in (Basismerkmale, held)
    base_index = {name: index for index, name in enumerate(BASE_FEATURES)}
    for name in names:
        if name in base_index:
            picks.append(base_index[name])
        else:
            series = inputs.sensors.get(name.removeprefix(SENSOR_PREFIX), {})
            picks.append(len(BASE_FEATURES) + len(held))
            held.append(series.get(cutoff - SLOT, _NAN))

    def lagged(slot: datetime, days: int) -> float:
        earlier = _local_shift(slot, days)
        if earlier is None or earlier + SLOT > cutoff:
            return _NAN
        return load.get(earlier, _NAN)

    rows: list[list[float]] = []
    for slot in slots:
        local = slot.astimezone(LOCAL_TZ)
        weekday, kind, holiday, month = lookups.calendar(local.date())
        expected = profile.at(slot) if profile is not None else None
        values = [
            (slot - issued) / _HOUR,
            float(local.hour * 60 + local.minute),
            weekday,
            kind,
            holiday,
            month,
            inputs.temp_c.get(slot, _NAN),
            lookups.temp_day(local.date()),
            lagged(slot, 1),
            lagged(slot, 7),
            recent,
            _NAN if expected is None else expected,
            *held,
        ]
        rows.append([values[index] for index in picks])
    return rows


def feature_rows(
    issued_at: datetime,
    slots: Sequence[datetime],
    inputs: FeatureInputs,
    names: Sequence[str],
) -> list[list[float]]:
    """Merkmalszeilen (Spalten wie ``names``) für die Ziel-Slots einer Ausgabe."""
    _check(names)
    return _rows(_Lookups(inputs), issued_at, slots, names)


def training_set(
    inputs: FeatureInputs, start: datetime, end: datetime, names: Sequence[str]
) -> TrainingSet:
    """Zeilen aller Ausgaben in ``[start, end)``: je Ausgabe die Slots vom Slot der Ausgabe bis
    72 h danach, die vor ``end`` liegen und einen Ist-Wert haben (Ziel)."""
    _check(names)
    lookups = _Lookups(inputs)
    stop = ensure_utc(end)
    rows: list[list[float]] = []
    targets: list[float] = []
    issued_list: list[datetime] = []
    slot_list: list[datetime] = []
    for issued in issuances(start, end):
        slots = [
            slot
            for slot in slots_between(slot_start(issued), min(issued + MAX_LEAD, stop))
            if slot in inputs.load_w and math.isfinite(inputs.load_w[slot])
        ]
        rows += _rows(lookups, issued, slots, names)
        targets += [inputs.load_w[slot] for slot in slots]
        issued_list += [issued] * len(slots)
        slot_list += slots
    return TrainingSet(rows, targets, issued_list, slot_list)
