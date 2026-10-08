"""Zeit- und Slot-Hilfen.

Intern wird ausschließlich mit zeitzonenbewussten UTC-Zeitpunkten gerechnet. Ein Slot dauert
15 Minuten und beginnt um :00, :15, :30 oder :45 (UTC; die Wiener Zeitzonen-Offsets sind volle
Stunden, daher deckt sich das Raster mit dem lokalen Smart-Meter-Raster).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

SLOT = timedelta(minutes=15)
LOCAL_TZ = ZoneInfo("Europe/Vienna")


def ensure_utc(dt: datetime) -> datetime:
    """Gibt ``dt`` in UTC zurück; naive Zeitpunkte sind ein Programmierfehler."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naiver Zeitpunkt ohne Zeitzone: {dt.isoformat()}")
    return dt.astimezone(UTC)


def slot_start(dt: datetime) -> datetime:
    """Beginn des 15-Minuten-Slots, in dem ``dt`` liegt (UTC)."""
    utc = ensure_utc(dt)
    return utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)


def slot_end(dt: datetime) -> datetime:
    """Ende (exklusiv) des Slots, in dem ``dt`` liegt (UTC)."""
    return slot_start(dt) + SLOT


def slots_between(start: datetime, end: datetime) -> list[datetime]:
    """Slot-Anfänge im halboffenen Intervall ``[start, end)``."""
    current = slot_start(start)
    if current < ensure_utc(start):
        current += SLOT
    stop = ensure_utc(end)
    slots: list[datetime] = []
    while current < stop:
        slots.append(current)
        current += SLOT
    return slots


def local_day_slots(day: date) -> list[datetime]:
    """UTC-Slot-Anfänge des lokalen Kalendertags (92, 96 oder 100 Slots)."""
    start = datetime.combine(day, time(0), tzinfo=LOCAL_TZ)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=LOCAL_TZ)
    return slots_between(start, end)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Echte Uhr (UTC)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Von Hand vorgestellte Uhr für Tests und Simulation."""

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta | float) -> None:
        step = delta if isinstance(delta, timedelta) else timedelta(seconds=delta)
        self._now += step
