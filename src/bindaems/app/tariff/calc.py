"""Bezugspreis je Slot aus Börsenpreis und Tarifbestandteilen, Einspeisepreis aus OeMAG.

Zeitfenster, Gültigkeitstage und Monate gelten in Ortszeit (Europe/Vienna). Alle Beträge in
ct/kWh; ``value_ct`` der Bestandteile ist netto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time

from bindaems.shared.settings import FeedInSettings, PriceComponent, TariffSettings, TimeWindow
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


def _window(component: PriceComponent, local_start: datetime) -> TimeWindow | None:
    """Das erste Fenster des Bestandteils, das zum Slot passt."""
    return next((w for w in component.windows if window_applies(w, local_start)), None)


def component_net_ct(
    component: PriceComponent, slot_start: datetime, spot_net_ct: float
) -> float | None:
    """Nettobetrag eines Bestandteils im Slot; ``None``, wenn er an dem Tag nicht gilt."""
    local = _local(slot_start)
    day = local.date()
    if component.valid_from is not None and day < component.valid_from:
        return None
    if component.valid_until is not None and day > component.valid_until:
        return None
    base = spot_net_ct if component.source == "spot" else (component.value_ct or 0.0)
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


def feed_in_price(slot_start: datetime, feed_in: FeedInSettings) -> float | None:
    """OeMAG-Wert des jüngsten eingetragenen Monats bis einschließlich zum Monat des Slots."""
    month = _local(slot_start).strftime("%Y-%m")
    known = [key for key in feed_in.monthly_ct if key <= month]  # „JJJJ-MM“ sortiert als Text
    return feed_in.monthly_ct[max(known)] if known else None
