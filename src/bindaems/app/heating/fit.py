"""Wärmebedarf aus Brennerstunden und Heizgradtagen (rein, ohne Runden)."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from bindaems.shared.settings import HeatingSettings

HEATING_LIMIT_C: Final = 15.0
FIT_MIN_DAYS: Final = 14
FIT_MIN_HDD: Final = 1.0
COMPLETE_SHARE: Final = 0.95


@dataclass(frozen=True)
class HeatDay:
    day: date
    burner_h: float | None
    known_h: float
    day_h: float
    burner_end: bool | None
    hdd_kd: float | None
    outdoor_mean_c: float | None
    ac_heat_el_kwh: float | None
    note: str | None

    @property
    def complete(self) -> bool:
        return self.burner_h is not None and self.known_h >= COMPLETE_SHARE * self.day_h


@dataclass(frozen=True)
class DayHeat:
    oil_heat_kwh: float | None
    oil_l: float | None
    oil_eur: float | None
    ac_heat_kwh: float | None
    demand_kwh: float | None


@dataclass(frozen=True)
class HeatFit:
    per_kd: float
    base_kwh_day: float
    r2: float
    n: int
    through_origin: bool


def day_heat(day: HeatDay, settings: HeatingSettings, *, ac_power_known: bool) -> DayHeat:
    """Wärme, Öl und Kosten eines Tages; was nicht bekannt ist, bleibt ``None``."""
    hours = day.burner_h
    oil_heat = hours * settings.burner_kw if hours is not None and settings.burner_kw else None
    oil_l = hours * settings.oil_l_per_h if hours is not None and settings.oil_l_per_h else None
    oil_eur = (
        oil_l * settings.oil_price_eur_l
        if oil_l is not None and settings.oil_price_eur_l is not None
        else None
    )
    ac_heat = (
        day.ac_heat_el_kwh * settings.ac_cop
        if ac_power_known and day.ac_heat_el_kwh is not None
        else None
    )
    demand: float | None = None
    if oil_heat is not None:
        if not ac_power_known:
            demand = oil_heat
        elif ac_heat is not None:
            demand = oil_heat + ac_heat
    return DayHeat(oil_heat, oil_l, oil_eur, ac_heat, demand)


def fit_demand(
    days: Sequence[HeatDay], settings: HeatingSettings, *, ac_power_known: bool
) -> HeatFit | str:
    """Tagesbedarf gegen Heizgradtage anpassen; Text statt Ergebnis nennt den Grund."""
    if settings.burner_kw is None:
        return "Nennleistung fehlt."
    points: list[tuple[float, float]] = []
    for d in days:
        if not d.complete or d.hdd_kd is None or d.hdd_kd < FIT_MIN_HDD:
            continue
        demand = day_heat(d, settings, ac_power_known=ac_power_known).demand_kwh
        if demand is not None:
            points.append((d.hdd_kd, demand))
    n = len(points)
    if n < FIT_MIN_DAYS:
        return f"Noch {n} von {FIT_MIN_DAYS} Tagen."
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_mean = math.fsum(xs) / n
    y_mean = math.fsum(ys) / n
    sxx = math.fsum((x - x_mean) ** 2 for x in xs)
    if sxx < 1e-9:
        return "Die Tage waren alle gleich kalt."
    slope = math.fsum((x - x_mean) * (y - y_mean) for x, y in points) / sxx
    base = y_mean - slope * x_mean
    through_origin = base < 0
    if through_origin:
        slope = math.fsum(x * y for x, y in points) / math.fsum(x * x for x in xs)
        base = 0.0
    ss_res = math.fsum((y - (base + slope * x)) ** 2 for x, y in points)
    ss_tot = math.fsum((y - y_mean) ** 2 for y in ys)
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return HeatFit(slope, base, r2, n, through_origin)


def season_of(day: date) -> tuple[date, date, str]:
    """Heizsaison 1.7. bis 30.6., Beschriftung wie ``2026/27``."""
    start_year = day.year if day.month >= 7 else day.year - 1
    label = f"{start_year}/{(start_year + 1) % 100:02d}"
    return date(start_year, 7, 1), date(start_year + 1, 6, 30), label


def known_total(values: Iterable[float | None]) -> float | None:
    """Summe der bekannten Werte; ``None``, wenn keiner bekannt ist."""
    known = [value for value in values if value is not None]
    return math.fsum(known) if known else None
