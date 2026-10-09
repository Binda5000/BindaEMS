import httpx
import pytest
from tests.app_helpers import login_as, mock_all_sources, price_row

from bindaems.app.prices.checks import to_slots
from bindaems.app.prices.pipeline import PricePipeline
from bindaems.app.prices.routes import prices_router
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.sources import SmartEnergySource, make_reference
from bindaems.app.prices.store import PriceStore


@pytest.fixture
def store(engine, clock) -> PriceStore:
    return PriceStore(engine, clock)


@pytest.fixture
def filled_store(store, smartenergy_intervals) -> PriceStore:
    slots = to_slots(smartenergy_intervals)
    store.save([price_row(slot, value / 1.2, "primary") for slot, value in slots.items()])
    return store


@pytest.fixture
def client(make_client, store, settings_service, clock, guard):
    http = httpx.AsyncClient()
    service = PriceService(store, settings_service)
    pipeline = PricePipeline(
        store,
        SmartEnergySource(http, clock),
        lambda name: make_reference(name, http, clock),
        settings_service,
        service,
        clock,
    )
    return make_client(prices_router(service, pipeline, clock, guard))


def test_prices_endpoint_returns_slots_and_status(client, auth, filled_store) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/prices").json()
    assert len(body["slots"]) == 96 and body["slots"][0]["origin"] == "primary"
    assert set(body["status"]) == {
        "last_attempt",
        "last_success",
        "vat_mode",
        "vat_detection",
        "days",
        "errors",
    }
    assert set(body["slots"][0]) == {
        "start",
        "spot_net_ct",
        "import_net_ct",
        "import_gross_ct",
        "feed_in_ct",
        "origin",
        "missing",
    }


def test_prices_span_is_limited(client, auth) -> None:
    login_as(client, auth, "viewer")
    params = {"from": "2026-10-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"}
    assert client.get("/api/prices", params=params).status_code == 422
    params = {"from": "2026-10-01T00:00:00", "to": "2026-10-02T00:00:00"}
    assert client.get("/api/prices", params=params).status_code == 422


def test_prices_now_and_next_3h(client, auth, filled_store) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/prices/now").json()
    assert body["now"]["start"] == "2026-10-09T08:00:00+00:00" and len(body["next_3h"]) == 12
    assert body["next_3h"][0]["start"] == "2026-10-09T08:15:00+00:00"


def test_refresh_is_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "viewer")
    assert client.post("/api/prices/refresh", headers=csrf).status_code == 403


def test_refresh_returns_status(client, auth, respx_mock) -> None:
    mock_all_sources(respx_mock)
    csrf = login_as(client, auth, "admin")
    body = client.post("/api/prices/refresh", headers=csrf).json()
    assert body["vat_detection"] == {
        "result": "gross",
        "ratio": pytest.approx(1.2, abs=1e-3),
        "slots": 96,
    }
    assert body["days"] == [{"date": "2026-10-09", "origin": "primary", "findings": []}]
    assert body["last_success"] == "2026-10-09T08:00:00+00:00" and body["errors"] == []
