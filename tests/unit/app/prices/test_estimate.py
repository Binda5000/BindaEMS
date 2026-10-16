from datetime import UTC, datetime

import pytest
from tests.app_helpers import price_row

from bindaems.app.prices.estimate import estimate_spot
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore, StoredPrice

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)  # Freitag 12:00 lokal
SLOT_MON = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # Montag


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def stored(iso: str, ct: float) -> StoredPrice:
    return StoredPrice(at(iso), ct, ct, None, "primary", None, NOW)


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def price_service(store, settings_service) -> PriceService:
    return PriceService(store, settings_service)


def test_estimate_is_median_of_same_quarter_and_day_type():
    history = [
        stored(f"2026-10-{d:02d}T10:00:00+00:00", ct)
        for d, ct in [(12, 10.0), (13, 12.0), (14, 14.0), (15, 30.0), (10, 99.0)]
    ]  # der 10. ist ein Samstag
    assert estimate_spot(history, SLOT_MON, NOW) == 13.0


def test_estimate_ignores_values_older_than_28_days():
    history = [
        stored("2026-09-17T10:00:00+00:00", 50.0),
        stored("2026-10-15T10:00:00+00:00", 8.0),
        stored("2026-10-14T10:00:00+00:00", 10.0),
    ]
    assert estimate_spot(history, SLOT_MON, NOW) == 9.0


def test_estimate_ignores_values_not_before_now():
    history = [stored("2026-10-16T10:00:00+00:00", 50.0), stored("2026-10-15T10:00:00+00:00", 8.0)]
    assert estimate_spot(history, SLOT_MON, NOW) is None


def test_estimate_falls_back_to_all_day_types():
    history = [stored("2026-10-10T10:00:00+00:00", 20.0), stored("2026-10-11T10:00:00+00:00", 24.0)]
    assert estimate_spot(history, SLOT_MON, NOW) == 22.0


def test_estimate_without_enough_values_is_none():
    assert estimate_spot([stored("2026-10-15T10:00:00+00:00", 8.0)], SLOT_MON, NOW) is None


def test_estimate_on_dst_day_uses_local_clock():
    # 02:00 MEZ am 25. 10. (zweiter Durchlauf) passt zu 02:00 der Sonntage davor
    history = [stored("2026-10-11T00:00:00+00:00", 5.0), stored("2026-10-18T00:00:00+00:00", 7.0)]
    slot = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    assert estimate_spot(history, slot, datetime(2026, 10, 24, 12, 0, tzinfo=UTC)) == 6.0


def test_planning_marks_estimated_slots(price_service, store):
    store.save(
        [price_row(at("2026-10-16T10:00:00+00:00"), 10.0, "primary")]
        + [price_row(at(f"2026-10-{d}T10:15:00+00:00"), 12.0, "primary") for d in (12, 13)]
    )
    rows = price_service.planning(
        at("2026-10-16T10:00:00+00:00"), at("2026-10-16T10:30:00+00:00"), NOW
    )
    assert [(r.origin, r.spot_net_ct) for r in rows] == [("primary", 10.0), ("estimated", 12.0)]


def test_planning_applies_tariff_and_omits_slots_without_price(price_service, store):
    store.save([price_row(at("2026-10-16T10:00:00+00:00"), 10.0, "fallback")])
    rows = price_service.planning(
        at("2026-10-16T10:00:00+00:00"), at("2026-10-16T10:45:00+00:00"), NOW
    )
    assert [r.slot_start for r in rows] == [at("2026-10-16T10:00:00+00:00")]
    assert rows[0].origin == "fallback"
    assert rows[0].import_net_ct == pytest.approx(11.2)
    assert rows[0].import_gross_ct == pytest.approx(13.44)
    assert rows[0].feed_in_ct is None
