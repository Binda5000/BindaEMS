"""Backtest des Lastmodells mit rollierendem Startpunkt (Plan 5a, Entscheidung 10).

Reine Rechnung ohne IO. Startpunkte sind die lokalen Mitternächte 7, 14, … Tage vor heute. Je
Startpunkt lernt ein Modell nur mit Daten davor (höchstens 56 Tage) und sagt die Ausgaben der
folgenden sieben Tage voraus. Verglichen wird es mit dem Profil, das am Tag der Ausgabe gegolten
hätte; beide Fehler werden auf denselben Slots gezählt, nämlich dort, wo das Profil einen Wert hat.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from bindaems.app.forecast.archive import HorizonClass, horizon_class
from bindaems.app.learning.features import (
    MAX_LEAD,
    FeatureInputs,
    feature_rows,
    issuances,
    training_set,
)
from bindaems.app.learning.model import TrainingError, train
from bindaems.shared.timeutil import LOCAL_TZ, slot_start, slots_between

log = logging.getLogger(__name__)

MIN_TRAIN_DAYS: Final = 14
MAX_WEEKS: Final = 8
MIN_WEEKS: Final = 2
TRAIN_WINDOW: Final = timedelta(days=56)
WEEK: Final = timedelta(days=7)
_CLASSES: Final[tuple[HorizonClass, ...]] = ("h0_6", "h6_24", "h24")


@dataclass(frozen=True)
class WeekResult:
    origin: datetime
    end: datetime
    horizon: HorizonClass
    n: int
    model_mae_w: float
    profile_mae_w: float


@dataclass(frozen=True)
class DaySums:
    """Summen der Absolutfehler (W) der Ausgaben eines lokalen Tages über alle Vorlaufklassen."""

    n: int
    model_abs_w: float
    profile_abs_w: float


@dataclass(frozen=True)
class BacktestResult:
    weeks: list[WeekResult]
    daily: dict[date, DaySums]  # je lokalem Tag der Ausgabe

    def improvement(self) -> float | None:
        """``1 − Σ|Fehler Modell| / Σ|Fehler Profil|`` über alle Wochen (nach Slots gewichtet);
        ``None``, wenn das Profil keinen Fehler hat."""
        model = sum(week.n * week.model_mae_w for week in self.weeks)
        profile = sum(week.n * week.profile_mae_w for week in self.weeks)
        return 1.0 - model / profile if profile > 0.0 else None


def week_count(days_with_data: int) -> int | None:
    """Zahl der Testwochen, die die Daten nach den ersten 14 Tagen erlauben (höchstens 8);
    ``None`` unter 2 Wochen."""
    weeks = min(MAX_WEEKS, (days_with_data - MIN_TRAIN_DAYS) // 7)
    return weeks if weeks >= MIN_WEEKS else None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=LOCAL_TZ).astimezone(UTC)


@dataclass
class _Sums:
    n: int = 0
    model_abs: float = 0.0
    profile_abs: float = 0.0


def _test_week(
    inputs: FeatureInputs, names: Sequence[str], origin: datetime, limit: datetime
) -> tuple[dict[HorizonClass, _Sums], dict[date, _Sums]] | None:
    """Summen der Absolutfehler einer Testwoche, oder ``None``, wenn das Training scheitert."""
    try:
        model = train(training_set(inputs, origin - TRAIN_WINDOW, origin, names), names)
    except TrainingError as exc:
        log.warning("Backtest: Woche ab %s ausgelassen, das Training scheiterte: %s", origin, exc)
        return None
    cases: list[tuple[float, float, HorizonClass, date]] = []  # Ist, Profil, Klasse, Tag
    rows: list[list[float]] = []
    for issued in issuances(origin, origin + WEEK):
        day = issued.astimezone(LOCAL_TZ).date()
        profile = inputs.profile_for(day)
        if profile is None:
            continue
        slots: list[datetime] = []
        for slot in slots_between(slot_start(issued), min(issued + MAX_LEAD, limit)):
            actual = inputs.load_w.get(slot)
            expected = profile.at(slot)
            cls = horizon_class(slot - issued)
            if actual is None or not math.isfinite(actual) or expected is None or cls is None:
                continue
            slots.append(slot)
            cases.append((actual, expected, cls, day))
        rows += feature_rows(issued, slots, inputs, names)
    by_class: dict[HorizonClass, _Sums] = defaultdict(_Sums)
    by_day: dict[date, _Sums] = defaultdict(_Sums)
    for (actual, expected, cls, day), value in zip(cases, model.predict(rows), strict=True):
        for sums in (by_class[cls], by_day[day]):
            sums.n += 1
            sums.model_abs += abs(value - actual)
            sums.profile_abs += abs(expected - actual)
    return by_class, by_day


def rolling_backtest(
    inputs: FeatureInputs, names: Sequence[str], today: date, days_with_data: int
) -> BacktestResult | None:
    """Backtest über so viele Wochen, wie die Daten erlauben; ``None``, wenn die Daten dafür nicht
    reichen oder keine Woche ausgewertet werden konnte. Ziele zählen nur vor der lokalen
    Mitternacht von ``today``."""
    weeks = week_count(days_with_data)
    if weeks is None:
        return None
    limit = _midnight(today)
    results: list[WeekResult] = []
    daily: dict[date, DaySums] = {}
    for k in range(weeks, 0, -1):  # älteste Woche zuerst
        origin = _midnight(today - timedelta(days=7 * k))
        tested = _test_week(inputs, names, origin, limit)
        if tested is None:
            continue
        by_class, by_day = tested
        for cls in _CLASSES:
            sums = by_class.get(cls)
            if sums is not None and sums.n:
                results.append(
                    WeekResult(
                        origin,
                        origin + WEEK,
                        cls,
                        sums.n,
                        sums.model_abs / sums.n,
                        sums.profile_abs / sums.n,
                    )
                )
        for day, sums in by_day.items():
            daily[day] = DaySums(sums.n, sums.model_abs, sums.profile_abs)
    return BacktestResult(results, daily) if results else None
