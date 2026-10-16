"""Bezugspreis je Slot aus Börsenpreis und Tarifbestandteilen, Einspeisepreis aus OeMAG,
Leistungspreis und Gebühren je Monat.

Zeitfenster, Gültigkeitstage und Monate gelten in Ortszeit (Europe/Vienna). Preise je Slot in
ct/kWh, Leistungspreis und Gebühren in €; ``value_ct`` und ``value_eur`` der Bestandteile sind
netto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time

from bindaems.shared.settings import (
    EnergyComponent,
    FeedInComponent,
    FeedInSettings,
    MonthlyFeeComponent,
    PowerComponent,
    PriceComponent,
    TariffSettings,
    TimeWindow,
)
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc


@dataclass(frozen=True)
class ImportPrice:
    net_ct: float
    gross_ct: float
    parts_net: Mapping[str, float]
    missing: tuple[str, ...]  # feste Bestandteile ohne Wert (zählen als 0)


def _local(slot_start: datetime) -> datetime:
    return ensure_utc(slot_start).astimezone(LOCAL_TZ)


def window_applies(window: TimeWindow, local_start: datetime) -> bool:
    if local_start.tzinfo is not None:
        local_start = local_start.astimezone(LOCAL_TZ)
    if local_start.month not in window.months or local_start.weekday() not in window.weekdays:
        return False
    clock = local_start.time()
    return window.start <= clock and (window.end == time(0) or clock < window.end)


def _valid_on(component: PriceComponent, day: date) -> bool:
    """Gilt der Bestandteil am (lokalen) Tag? ``valid_until`` ist inklusive."""
    if component.valid_from is not None and day < component.valid_from:
        return False
    return component.valid_until is None or day <= component.valid_until


def _window(
    component: EnergyComponent | FeedInComponent, local_start: datetime
) -> TimeWindow | None:
    """Das erste Fenster des Bestandteils, das zum Slot passt."""
    return next((w for w in component.windows if window_applies(w, local_start)), None)


def component_net_ct(
    component: EnergyComponent, slot_start: datetime, spot_net_ct: float
) -> float | None:
    """Nettobetrag eines Bestandteils im Slot; ``None``, wenn er an dem Tag nicht gilt."""
    local = _local(slot_start)
    if not _valid_on(component, local.date()):
        return None
    base = spot_net_ct if component.source == "spot" else (component.value_ct or 0.0)
    return _apply_window(component, local, base)


def _apply_window(
    component: EnergyComponent | FeedInComponent, local: datetime, base: float
) -> float:
    """Grundwert des Bestandteils, ersetzt oder skaliert durch das passende Zeitfenster."""
    window = _window(component, local)
    if window is None:
        return base
    if window.factor is not None:  # TimeWindow hat genau eines von factor und value_ct
        return base * window.factor
    return window.value_ct if window.value_ct is not None else base


def import_price(slot_start: datetime, spot_net_ct: float, tariff: TariffSettings) -> ImportPrice:
    local = _local(slot_start)
    vat_factor = 1 + tariff.vat_pct / 100
    parts: dict[str, float] = {}
    gross = 0.0
    missing: list[str] = []
    for component in tariff.components:
        if not isinstance(component, EnergyComponent):
            continue  # Leistungspreis, Gebühren und Einspeisung gehören nicht zum Bezugspreis
        value = component_net_ct(component, slot_start, spot_net_ct)
        if value is None:
            continue
        parts[component.id] = value
        gross += value * vat_factor if component.vat else value
        if component.source == "fixed" and component.value_ct is None:
            window = _window(component, local)
            if window is None or window.value_ct is None:
                missing.append(component.id)
    return ImportPrice(
        net_ct=sum(parts.values()), gross_ct=gross, parts_net=parts, missing=tuple(missing)
    )


def feed_in_price(
    slot_start: datetime, feed_in: FeedInSettings, tariff: TariffSettings
) -> float | None:
    """OeMAG-Wert des jüngsten eingetragenen Monats bis einschließlich zum Monat des Slots,
    dazu die am Tag gültigen Einspeisebestandteile (brutto, wo ``vat``); ohne OeMAG-Wert
    unbekannt."""
    local = _local(slot_start)
    month = local.strftime("%Y-%m")
    known = [key for key in feed_in.monthly_ct if key <= month]  # „JJJJ-MM“ sortiert als Text
    if not known:
        return None
    vat_factor = 1 + tariff.vat_pct / 100
    price = feed_in.monthly_ct[max(known)]
    for component in tariff.components:
        if not isinstance(component, FeedInComponent) or not _valid_on(component, local.date()):
            continue
        value = _apply_window(component, local, component.value_ct or 0.0)
        price += value * vat_factor if component.vat else value
    return price


@dataclass(frozen=True)
class PowerRate:
    """Leistungspreis eines Tages in €/kW/Jahr netto."""

    component_id: str
    below_eur_kw_year: float
    above_eur_kw_year: float
    tier_kw: float
    minimum_kw: float
    vat_factor: float
    missing: bool  # kein Satz eingetragen: beide Sätze zählen als 0


def power_rate(day: date, tariff: TariffSettings) -> PowerRate | None:
    """Der am Tag gültige Leistungspreis; ``None``, wenn keiner gilt."""
    for component in tariff.components:
        if not isinstance(component, PowerComponent) or not _valid_on(component, day):
            continue
        below = component.value_eur
        above = component.value_above_eur if component.value_above_eur is not None else below
        return PowerRate(
            component_id=component.id,
            below_eur_kw_year=below or 0.0,
            above_eur_kw_year=above or 0.0,
            tier_kw=component.tier_kw,
            minimum_kw=component.minimum_kw,
            vat_factor=1 + tariff.vat_pct / 100 if component.vat else 1.0,
            missing=below is None,
        )
    return None


def billed_kw(peak_kw: float | None, rate: PowerRate) -> float:
    """Verrechnete Leistung: Monatsspitze, mindestens ``minimum_kw``."""
    return max(peak_kw or 0.0, rate.minimum_kw)


def power_cost_eur(peak_kw: float | None, rate: PowerRate) -> float:
    """Leistungspreis eines Monats in € brutto."""
    kw = billed_kw(peak_kw, rate)
    net_year = rate.below_eur_kw_year * min(kw, rate.tier_kw) + rate.above_eur_kw_year * max(
        0.0, kw - rate.tier_kw
    )
    return net_year / 12 * rate.vat_factor


def monthly_fees_eur(day: date, tariff: TariffSettings) -> float:
    """Summe der am Tag gültigen Monatsgebühren in € brutto (fehlender Wert zählt als 0)."""
    vat_factor = 1 + tariff.vat_pct / 100
    return sum(
        (c.value_eur or 0.0) * (vat_factor if c.vat else 1.0)
        for c in tariff.components
        if isinstance(c, MonthlyFeeComponent) and _valid_on(c, day)
    )
