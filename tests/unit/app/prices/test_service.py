from datetime import UTC, datetime

import pytest
from tests.app_helpers import price_row

from bindaems.app.prices.service import PriceService
from bindaems.app.prices.store import PriceStore

T_SLOT = datetime(2026, 10, 9, 8, tzinfo=UTC)


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def service(store, settings_service) -> PriceService:
    return PriceService(store, settings_service)


def test_price_view_applies_tariff(service, store, settings_service) -> None:
    store.save([price_row(T_SLOT, 10.0, "primary")])
    view = service.at(T_SLOT)
    assert view.import_net_ct == pytest.approx(11.2) and view.import_gross_ct == pytest.approx(
        13.44
    )
    assert view.missing == ("grid_usage", "grid_loss", "electricity_tax", "renewables_levy")
    assert view.feed_in_ct is None and view.origin == "primary" and view.spot_net_ct == 10.0
    assert service.import_gross_at(T_SLOT.replace(minute=7)) == pytest.approx(13.44)


def test_unknown_slot_has_no_price(service) -> None:
    assert service.at(T_SLOT) is None and service.import_gross_at(T_SLOT) is None
