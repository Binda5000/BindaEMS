"""Tagestyp für Schätzpreise und Lastprofil.

Ein Tag ist ``workday``, ``saturday`` oder ``sunday``. Sonntage und die bundesweiten Feiertage in
Österreich zählen als ``sunday``. Maßgeblich ist das lokale Datum (Europe/Vienna).
"""

from __future__ import annotations

from datetime import date, datetime, time
from functools import cache
from typing import Literal

import holidays

from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

DayType = Literal["workday", "saturday", "sunday"]


@cache
def _holidays(year: int) -> holidays.HolidayBase:
    """Bundesweite Feiertage Österreichs eines Jahres; je Jahr einmal erzeugt."""
    return holidays.country_holidays("AT", years=year)


def day_type(day: date) -> DayType:
    """Tagestyp des lokalen Kalendertags ``day``."""
    if day.weekday() == 6 or day in _holidays(day.year):
        return "sunday"
    if day.weekday() == 5:
        return "saturday"
    return "workday"


def slot_key(slot: datetime) -> tuple[DayType, time]:
    """Tagestyp und lokale Uhrzeit des Slotbeginns.

    An der Herbstumstellung kommt 02:00 zweimal vor; beide Slots ergeben denselben Schlüssel.
    """
    local = ensure_utc(slot).astimezone(LOCAL_TZ)
    return day_type(local.date()), local.time()
