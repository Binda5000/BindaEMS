"""Preise je Slot mit dem aktuellen Tarif: Bezugspreis netto und brutto, Einspeisepreis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bindaems.app.prices.estimate import WINDOW, SpotHistory
from bindaems.app.prices.store import PriceStore, StoredPrice
from bindaems.app.settings.service import CurrentSettings
from bindaems.app.tariff.calc import ImportPrice, feed_in_price, import_price
from bindaems.shared.settings import RuntimeSettings
from bindaems.shared.timeutil import SLOT, slot_start, slots_between


@dataclass(frozen=True)
class PriceView:
    start: datetime
    spot_net_ct: float
    import_net_ct: float
    import_gross_ct: float
    feed_in_ct: float | None
    origin: str
    missing: tuple[str, ...]  # Tarifbestandteile ohne Wert (als 0 gerechnet)


@dataclass(frozen=True)
class PlanningPrice:
    """Preis eines Slots für die Planung; ``estimated`` bei geschätztem Börsenpreis."""

    slot_start: datetime
    spot_net_ct: float
    import_net_ct: float
    import_gross_ct: float
    feed_in_ct: float | None
    origin: Literal["primary", "fallback", "estimated"]


def _tariff_parts(
    slot: datetime, spot_net_ct: float, settings: RuntimeSettings
) -> tuple[ImportPrice, float | None]:
    """Bezugspreis und Einspeisepreis eines Slots mit dem aktuellen Tarif."""
    return (
        import_price(slot, spot_net_ct, settings.tariff),
        feed_in_price(slot, settings.feed_in, settings.tariff),
    )


def _view(stored: StoredPrice, settings: RuntimeSettings) -> PriceView:
    price, feed_in = _tariff_parts(stored.slot_start, stored.spot_net_ct, settings)
    return PriceView(
        start=stored.slot_start,
        spot_net_ct=stored.spot_net_ct,
        import_net_ct=price.net_ct,
        import_gross_ct=price.gross_ct,
        feed_in_ct=feed_in,
        origin=stored.origin,
        missing=price.missing,
    )


class PriceService:
    def __init__(self, store: PriceStore, settings: CurrentSettings) -> None:
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

    def planning(self, start: datetime, end: datetime, now: datetime) -> list[PlanningPrice]:
        """Preise der Slots ``[start, end)``: gespeichert, sonst geschätzt, sonst fehlt der Slot."""
        settings = self._settings.current().settings
        stored = self._store.get(now - WINDOW, end)
        by_slot = {price.slot_start: price for price in stored}
        history = SpotHistory(stored, now)
        rows: list[PlanningPrice] = []
        for slot in slots_between(start, end):
            known = by_slot.get(slot)
            origin: Literal["primary", "fallback", "estimated"]
            if known is not None:
                spot, origin = known.spot_net_ct, known.origin
            else:
                estimate = history.estimate(slot)
                if estimate is None:
                    continue
                spot, origin = estimate, "estimated"
            price, feed_in = _tariff_parts(slot, spot, settings)
            rows.append(
                PlanningPrice(
                    slot_start=slot,
                    spot_net_ct=spot,
                    import_net_ct=price.net_ct,
                    import_gross_ct=price.gross_ct,
                    feed_in_ct=feed_in,
                    origin=origin,
                )
            )
        return rows
