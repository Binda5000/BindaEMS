from datetime import UTC, date, datetime

import pytest
from tests.app_helpers import price_row, raw_response

from bindaems.app.prices.store import PriceStore
from bindaems.shared.timeutil import SLOT, local_day_slots

T_SLOT = datetime(2026, 10, 9, 8, tzinfo=UTC)


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


def test_archive_deduplicates_identical_bodies(store, clock) -> None:
    first = store.archive(raw_response("smartenergy", "{}"))
    clock.advance(3600)
    assert store.archive(raw_response("smartenergy", "{}")) == first
    assert store.archive(raw_response("energy_charts", "{}")) != first  # andere Quelle
    assert store.archive(raw_response("smartenergy", '{"neu": 1}')) != first


def test_fallback_never_overwrites_primary(store) -> None:
    store.save([price_row(T_SLOT, 10.0, "primary")])
    store.save([price_row(T_SLOT, 99.0, "fallback", reference_ct=9.0)])
    [stored] = store.get(T_SLOT, T_SLOT + SLOT)
    assert (stored.spot_net_ct, stored.origin, stored.reference_ct) == (10.0, "primary", 9.0)
    assert stored.spot_raw_ct == 10.0


def test_primary_replaces_fallback(store) -> None:
    store.save([price_row(T_SLOT, 9.0, "fallback", reference_ct=9.0)])
    store.save([price_row(T_SLOT, 10.0, "primary")])
    [stored] = store.get(T_SLOT, T_SLOT + SLOT)
    assert (stored.origin, stored.spot_net_ct, stored.reference_ct) == ("primary", 10.0, 9.0)


def test_complete_day(store) -> None:
    day = date(2026, 10, 25)
    store.save([price_row(t, 10.0, "primary") for t in local_day_slots(day)[:-1]])
    assert not store.complete_day(day)
    store.save([price_row(local_day_slots(day)[-1], 10.0, "fallback")])
    assert store.complete_day(day) and not store.complete_day(day, "primary")
    assert len(store.get(local_day_slots(day)[0], local_day_slots(day)[-1] + SLOT)) == 100
