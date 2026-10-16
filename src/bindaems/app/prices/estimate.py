"""Schätzpreis für Slots ohne gespeicherten Börsenpreis.

Median der gespeicherten ``spot_net_ct`` derselben lokalen Uhrzeit und desselben Tagestyps in den
28 Tagen vor jetzt (mindestens 2 Werte), sonst derselbe Median ohne Tagestyp, sonst kein Preis.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime, time, timedelta

from bindaems.app.daytype import DayType, slot_key
from bindaems.app.prices.store import StoredPrice
from bindaems.shared.timeutil import ensure_utc

WINDOW = timedelta(days=28)
MIN_VALUES = 2

Key = tuple[DayType, time]


class SpotHistory:
    """Börsenpreise der letzten 28 Tage, einmal nach Tagestyp und Uhrzeit vorsortiert.

    An der Herbstumstellung teilen sich beide 02:xx-Durchläufe einen Schlüssel.
    """

    def __init__(self, history: Sequence[StoredPrice], now: datetime) -> None:
        now = ensure_utc(now)
        self._by_key: dict[Key, list[float]] = {}
        self._by_time: dict[time, list[float]] = {}
        for price in history:
            if not now - WINDOW <= ensure_utc(price.slot_start) < now:
                continue
            day, clock = slot_key(price.slot_start)
            self._by_key.setdefault((day, clock), []).append(price.spot_net_ct)
            self._by_time.setdefault(clock, []).append(price.spot_net_ct)

    def estimate(self, slot: datetime) -> float | None:
        day, clock = slot_key(slot)
        for values in (self._by_key.get((day, clock), []), self._by_time.get(clock, [])):
            if len(values) >= MIN_VALUES:
                return statistics.median(values)
        return None


def estimate_spot(history: Sequence[StoredPrice], slot: datetime, now: datetime) -> float | None:
    """Geschätzter Börsenpreis netto (ct/kWh) von ``slot`` oder ``None``."""
    return SpotHistory(history, now).estimate(slot)
