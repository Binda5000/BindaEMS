"""Trefferquote des Fahrverhaltens (Plan 5b, Entscheidung 14, Präzisierung 7, Rulings T6-1, T6-2).

Für jeden Fahrtag der letzten Tage vergleicht die app die tatsächliche erste Abfahrt mit dem
Vorschlag, der am Vorabend galt: dem gespeicherten Stand mit dem größten ``computed_at`` vor der
lokalen Mitternacht des Tages. Ohne einen solchen Stand ist der Tag nicht bewertet; ebenso ein
Vorschlag ohne Konfidenz ab „mittel“. Unbekannt bleibt unbekannt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from bindaems.app.learning.driving_days import DayRecord
from bindaems.app.learning.driving_profile import Confidence, VehicleProfile
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

RATED: Final[frozenset[Confidence | None]] = frozenset({"medium", "high"})


@dataclass(frozen=True)
class HitRates:
    """Bewertete Tage und Treffer, getrennt für die Abfahrt und das Ziel."""

    departure_n: int
    departure_hits: int
    target_n: int
    target_hits: int

    @staticmethod
    def percent(hits: int, n: int) -> int | None:
        """Trefferquote in ganzen Prozent; ``None`` ohne bewertete Tage."""
        return round(100 * hits / n) if n else None

    @property
    def departure_pct(self) -> int | None:
        return self.percent(self.departure_hits, self.departure_n)

    @property
    def target_pct(self) -> int | None:
        return self.percent(self.target_hits, self.target_n)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ).astimezone(UTC)


def _minute_of_day(moment: datetime) -> int:
    local = ensure_utc(moment).astimezone(LOCAL_TZ)
    return local.hour * 60 + local.minute


def hit_rates(
    records: Sequence[DayRecord],
    snapshots: Sequence[tuple[datetime, VehicleProfile]],
    reserve_pct: float,
    *,
    today: date,
    days: int = 28,
) -> HitRates:
    """Trefferquote über die ``days`` Tage vor ``today`` für die Tagesdatensätze eines Autos.

    ``snapshots`` sind die gespeicherten Stände desselben Autos als (``computed_at``, Profil).
    Fahrtag ist ein Tag, der nicht ``away`` ist und eine erste Abfahrt hat. Die Abfahrt zählt als
    Treffer, wenn das Auto nicht vor dem Vorschlag fuhr; das Ziel, wenn Ziel minus tatsächlicher
    Verbrauch mindestens ``reserve_pct`` ergibt.
    """
    ordered = sorted(((ensure_utc(at), profile) for at, profile in snapshots), key=lambda s: s[0])
    first = today - timedelta(days=days)
    departure_n = departure_hits = target_n = target_hits = 0
    for record in records:
        if not first <= record.day < today or record.away or record.departure is None:
            continue
        midnight = _midnight(record.day)
        earlier = [profile for at, profile in ordered if at < midnight]
        if not earlier:
            continue
        suggestion = earlier[-1].weekdays[record.weekday]
        if suggestion.departure_min is not None and suggestion.departure_confidence in RATED:
            departure_n += 1
            departure_hits += _minute_of_day(record.departure) >= suggestion.departure_min
        if (
            suggestion.target_soc_pct is not None
            and suggestion.target_confidence in RATED
            and record.consumption_pp is not None
        ):
            target_n += 1
            target_hits += suggestion.target_soc_pct - record.consumption_pp >= reserve_pct
    return HitRates(departure_n, departure_hits, target_n, target_hits)
