"""Prüfungen der Preisdaten (Spec 6.7): Slot-Raster, Plausibilität und Brutto/Netto-Erkennung.

smartENERGY liefert Stundenmittel (brutto) im 15-min-Raster. Die Brutto/Netto-Erkennung
vergleicht daher Stundenmittel beider Quellen, nicht einzelne Slots.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from bindaems.app.prices.sources import PriceInterval
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc, local_day_slots, slot_start

RANGE_MIN_CT = -50.0
RANGE_MAX_CT = 100.0
MAX_RANGE_FINDINGS = 3
VAT_TOLERANCE = 0.05
MIN_REFERENCE_CT = 2.0  # kleinere Stundenmittel machen das Verhältnis unbrauchbar
MIN_VAT_SLOTS = 24
SLOTS_PER_HOUR = 4


def _hhmm(ts: datetime) -> str:
    return ensure_utc(ts).astimezone(LOCAL_TZ).strftime("%H:%M")


def to_slots(intervals: Sequence[PriceInterval]) -> dict[datetime, float]:
    """Zeitgewichtetes Mittel je Slot; nur vollständig abgedeckte Slots."""
    pieces: dict[datetime, list[tuple[datetime, datetime, float]]] = {}
    for interval in intervals:
        start, end = ensure_utc(interval.start), ensure_utc(interval.end)
        slot = slot_start(start)
        while slot < end:
            begin, finish = max(start, slot), min(end, slot + SLOT)
            if begin < finish:
                pieces.setdefault(slot, []).append((begin, finish, interval.ct_kwh))
            slot += SLOT
    slots: dict[datetime, float] = {}
    for slot in sorted(pieces):
        parts = pieces[slot]
        if not _covers(slot, parts):
            continue
        values = {value for _, _, value in parts}
        if len(values) == 1:
            slots[slot] = parts[0][2]  # exakt übernehmen, ohne Rundung
            continue
        weights = [(finish - begin).total_seconds() for begin, finish, _ in parts]
        weighted = math.fsum(w * value for w, (_, _, value) in zip(weights, parts, strict=True))
        slots[slot] = weighted / math.fsum(weights)
    return slots


def _covers(slot: datetime, parts: Sequence[tuple[datetime, datetime, float]]) -> bool:
    reached = slot
    for begin, finish, _ in sorted(parts):
        if begin > reached:
            return False  # Lücke
        reached = max(reached, finish)
    return reached >= slot + SLOT


def duplicate_starts(intervals: Sequence[PriceInterval]) -> list[datetime]:
    seen: set[datetime] = set()
    duplicates: set[datetime] = set()
    for interval in intervals:
        start = ensure_utc(interval.start)
        (duplicates if start in seen else seen).add(start)
    return sorted(duplicates)


def by_local_day(intervals: Sequence[PriceInterval]) -> dict[date, list[PriceInterval]]:
    days: dict[date, list[PriceInterval]] = {}
    for interval in intervals:
        day = ensure_utc(interval.start).astimezone(LOCAL_TZ).date()
        days.setdefault(day, []).append(interval)
    return days


def structure_findings(day: date, intervals: Sequence[PriceInterval]) -> list[str]:
    findings: list[str] = []
    expected = local_day_slots(day)
    slots = to_slots(intervals)
    missing = sum(1 for slot in expected if slot not in slots)
    if missing:
        findings.append(f"{missing} Slots fehlen")
    findings += [f"Slot doppelt: {_hhmm(start)}" for start in duplicate_starts(intervals)]
    values = [slots[slot] for slot in expected if slot in slots]
    # erst ab mehr als einer Stunde: Stundenwerte im 15-min-Raster sind je Stunde gleich
    if len(values) > SLOTS_PER_HOUR and len(set(values)) == 1:
        findings.append(f"alle Werte gleich ({values[0]:.3f} ct)")
    return findings


def range_findings(net: Mapping[datetime, float]) -> list[str]:
    outside = [
        f"Wert außerhalb −50…100 ct/kWh: {value:.2f} ct um {_hhmm(slot)}"
        for slot, value in sorted(net.items())
        if not RANGE_MIN_CT <= value <= RANGE_MAX_CT
    ]
    if len(outside) > MAX_RANGE_FINDINGS:
        return [*outside[:MAX_RANGE_FINDINGS], "…"]
    return outside


@dataclass(frozen=True)
class VatDetection:
    result: Literal["net", "gross"] | None
    ratio: float | None
    slots: int  # Slots der verglichenen Stunden


def _complete_hours(slots: Mapping[datetime, float]) -> dict[datetime, list[float]]:
    """Werte je Stunde, in der alle vier Slots vorliegen (Ortszeit-Offsets sind volle Stunden)."""
    hours: dict[datetime, list[float]] = {}
    for slot, value in slots.items():
        hour = ensure_utc(slot).replace(minute=0, second=0, microsecond=0)
        hours.setdefault(hour, []).append(value)
    return {hour: values for hour, values in hours.items() if len(values) == SLOTS_PER_HOUR}


def _hour_means(slots: Mapping[datetime, float]) -> dict[datetime, float]:
    return {
        hour: math.fsum(values) / SLOTS_PER_HOUR for hour, values in _complete_hours(slots).items()
    }


def detect_vat(
    primary: Mapping[datetime, float], reference: Mapping[datetime, float], gross_factor: float
) -> VatDetection:
    """Vergleicht Stundenmittel: Verhältnis ≈ 1 → netto, ≈ ``gross_factor`` → brutto."""
    ours, theirs = _hour_means(primary), _hour_means(reference)
    ratios = [
        ours[hour] / theirs[hour]
        for hour in sorted(ours.keys() & theirs.keys())
        if abs(theirs[hour]) > MIN_REFERENCE_CT
    ]
    slots = SLOTS_PER_HOUR * len(ratios)
    if slots < MIN_VAT_SLOTS:
        return VatDetection(None, None, slots)
    ratio = statistics.median(ratios)
    if abs(ratio - 1.0) <= VAT_TOLERANCE:
        return VatDetection("net", ratio, slots)
    if abs(ratio - gross_factor) <= VAT_TOLERANCE:
        return VatDetection("gross", ratio, slots)
    return VatDetection(None, ratio, slots)


def is_hourly(slots: Mapping[datetime, float]) -> bool:
    """Ob in jeder vollständigen Stunde alle vier Werte gleich sind (mindestens eine Stunde)."""
    complete = _complete_hours(slots).values()
    return bool(complete) and all(len(set(values)) == 1 for values in complete)
