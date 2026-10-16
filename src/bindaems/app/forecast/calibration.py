"""Klarheit, Kalibrierung und Quantile der PV-Prognose (Plan 3a, Präzisierung 9).

Reine Rechnung auf Paaren aus Prognose und Ist-Leistung; das Sammeln der Paare übernimmt der Dienst.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bindaems.app.forecast.archive import Clearness, HorizonClass
from bindaems.shared.config import Config

MIN_GHI_W_M2 = 50.0  # darunter (Nacht, Dämmerung) gibt es keine Klasse
CLEAR_FROM = 0.6
MIXED_FROM = 0.3


def clearness_class(ghi_w_m2: float | None, diffuse_w_m2: float | None) -> Clearness | None:
    """Klarheit aus ``k = 1 − diffus / global``; ohne Werte oder unter 50 W/m² global ``None``."""
    if ghi_w_m2 is None or diffuse_w_m2 is None or ghi_w_m2 < MIN_GHI_W_M2:
        return None
    k = round(1.0 - diffuse_w_m2 / ghi_w_m2, 9)
    if k >= CLEAR_FROM:
        return "clear"
    if k >= MIXED_FROM:
        return "mixed"
    return "overcast"


MIN_PAIRS_FACTOR = 8
FACTOR_RANGE = (0.5, 1.5)
MIN_PAIRS_BAND = 20
DEFAULT_BAND = (0.7, 1.2)
MIN_THRESHOLD_W = 100.0
THRESHOLD_SHARE = 0.02


@dataclass(frozen=True)
class CalPair:
    """Ein Vergleich für die Faktoren: Roh-Prognose gegen Ist-Leistung eines Slots."""

    local_hour: int
    clearness: Clearness | None
    raw_w: float
    actual_w: float


@dataclass(frozen=True)
class Calibration:
    """Faktoren je (Stunde, Klarheit) und je Stunde; ``samples`` zählt die genutzten Paare."""

    factors: Mapping[tuple[int, Clearness | None], float]
    hour_factors: Mapping[int, float]
    samples: int

    def factor(self, hour: int, clearness: Clearness | None) -> float:
        found = self.factors.get((hour, clearness))
        if found is not None:
            return found
        return self.hour_factors.get(hour, 1.0)


NEUTRAL = Calibration({}, {}, 0)


def _clamp(value: float) -> float:
    return min(max(value, FACTOR_RANGE[0]), FACTOR_RANGE[1])


def calibrate(pairs: Sequence[CalPair], threshold_w: float) -> Calibration:
    """Median von ``Ist / roh`` je Stunde und Klarheit, sonst je Stunde (mindestens 8 Paare).

    Paare mit ``raw_w`` unter der Schwelle bleiben außen vor (Dämmerung verfälscht das Verhältnis).
    ``samples`` ist die Zahl der Paare in Stunden, für die ein Faktor entstanden ist.
    """
    usable = [pair for pair in pairs if pair.raw_w >= threshold_w and pair.raw_w > 0]
    by_class: dict[tuple[int, Clearness | None], list[float]] = defaultdict(list)
    by_hour: dict[int, list[float]] = defaultdict(list)
    for pair in usable:
        ratio = pair.actual_w / pair.raw_w
        by_class[(pair.local_hour, pair.clearness)].append(ratio)
        by_hour[pair.local_hour].append(ratio)
    factors = {
        key: _clamp(statistics.median(ratios))
        for key, ratios in by_class.items()
        if len(ratios) >= MIN_PAIRS_FACTOR
    }
    hour_factors = {
        hour: _clamp(statistics.median(ratios))
        for hour, ratios in by_hour.items()
        if len(ratios) >= MIN_PAIRS_FACTOR
    }
    samples = sum(len(ratios) for ratios in by_hour.values() if len(ratios) >= MIN_PAIRS_FACTOR)
    return Calibration(factors, hour_factors, samples)


@dataclass(frozen=True)
class ErrPair:
    """Ein Vergleich für die Bänder: kalibrierte Prognose gegen Ist-Leistung eines Slots."""

    horizon: HorizonClass
    clearness: Clearness | None
    p50_w: float
    actual_w: float


@dataclass(frozen=True)
class Bands:
    """Quantilfaktoren (q25, q75) von ``Ist / P50`` je (Horizont, Klarheit) und je Horizont."""

    by_class: Mapping[tuple[HorizonClass, Clearness | None], tuple[float, float]]
    by_horizon: Mapping[HorizonClass, tuple[float, float]]

    def band(self, horizon: HorizonClass, clearness: Clearness | None) -> tuple[float, float]:
        found = self.by_class.get((horizon, clearness))
        if found is not None:
            return found
        return self.by_horizon.get(horizon, DEFAULT_BAND)


NEUTRAL_BANDS = Bands({}, {})


def _quartiles(ratios: Sequence[float]) -> tuple[float, float]:
    q25, _, q75 = statistics.quantiles(ratios, n=4, method="inclusive")
    return q25, q75


def fit_bands(pairs: Sequence[ErrPair], threshold_w: float) -> Bands:
    """25. und 75. Perzentil von ``Ist / P50`` je Horizont und Klarheit, sonst je Horizont
    (mindestens 20 Paare); Paare mit ``p50_w`` unter der Schwelle zählen nicht."""
    usable = [pair for pair in pairs if pair.p50_w >= threshold_w and pair.p50_w > 0]
    by_class: dict[tuple[HorizonClass, Clearness | None], list[float]] = defaultdict(list)
    by_horizon: dict[HorizonClass, list[float]] = defaultdict(list)
    for pair in usable:
        ratio = pair.actual_w / pair.p50_w
        by_class[(pair.horizon, pair.clearness)].append(ratio)
        by_horizon[pair.horizon].append(ratio)
    return Bands(
        {key: _quartiles(r) for key, r in by_class.items() if len(r) >= MIN_PAIRS_BAND},
        {key: _quartiles(r) for key, r in by_horizon.items() if len(r) >= MIN_PAIRS_BAND},
    )


def pv_threshold_w(cfg: Config) -> float:
    """Schwelle für Paare: ``max(100 W, 2 % der Summe der kWp)``."""
    kwp = sum(plane.kwp for plane in cfg.pv.planes)
    return max(MIN_THRESHOLD_W, THRESHOLD_SHARE * kwp * 1000.0)


def apply_band(p50_w: float, band: tuple[float, float], max_w: float) -> tuple[float, float, float]:
    """``(P25, P50, P75)``; P25 ≤ P50 ≤ P75 und P75 höchstens ``max_w``."""
    q25, q75 = band
    return min(p50_w * q25, p50_w), p50_w, min(max(p50_w * q75, p50_w), max_w)
