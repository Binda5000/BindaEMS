"""Preise je Slot mit dem aktuellen Tarif: Bezugspreis netto und brutto, Einspeisepreis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bindaems.app.prices.store import PriceStore, StoredPrice
from bindaems.app.settings.service import SettingsService
from bindaems.app.tariff.calc import feed_in_price, import_price
from bindaems.shared.settings import RuntimeSettings
from bindaems.shared.timeutil import SLOT, slot_start


@dataclass(frozen=True)
class PriceView:
    start: datetime
    spot_net_ct: float
    import_net_ct: float
    import_gross_ct: float
    feed_in_ct: float | None
    origin: str
    missing: tuple[str, ...]  # Tarifbestandteile ohne Wert (als 0 gerechnet)


def _view(stored: StoredPrice, settings: RuntimeSettings) -> PriceView:
    price = import_price(stored.slot_start, stored.spot_net_ct, settings.tariff)
    return PriceView(
        start=stored.slot_start,
        spot_net_ct=stored.spot_net_ct,
        import_net_ct=price.net_ct,
        import_gross_ct=price.gross_ct,
        feed_in_ct=feed_in_price(stored.slot_start, settings.feed_in),
        origin=stored.origin,
        missing=price.missing,
    )


class PriceService:
    def __init__(self, store: PriceStore, settings: SettingsService) -> None:
        self._store = store
        self._settings = settings

    def slots(self, start: datetime, end: datetime) -> list[PriceView]:
        settings = self._settings.current().settings
        return [_view(stored, settings) for stored in self._store.get(start, end)]

    def at(self, slot: datetime) -> PriceView | None:
        start = slot_start(slot)
        views = self.slots(start, start + SLOT)
        return views[0] if views else None

    def import_gross_at(self, slot: datetime) -> float | None:
        view = self.at(slot)
        return view.import_gross_ct if view is not None else None
