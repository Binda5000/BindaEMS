from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from tests.app_helpers import ingest, login_as
from tests.unit.app.stats.stats_helpers import T0

from bindaems.shared.timeutil import SLOT


@pytest.fixture
def client_as(client, auth) -> Callable[[str], TestClient]:
    def build(role: str) -> TestClient:
        login_as(client, auth, role)  # type: ignore[arg-type]
        return client

    return build


def test_stats_route_rejects_unknown_period(client_as) -> None:
    client = client_as("viewer")
    for period in ("quarter", "", "DAY"):
        response = client.get("/api/stats", params={"period": period, "date": "2026-10-09"})
        assert response.status_code == 422, period
    assert client.get("/api/stats", params={"date": "2026-10-09"}).status_code == 422
    assert client.get("/api/stats", params={"period": "day", "date": "gestern"}).status_code == 422


def test_stats_route_needs_a_login(client) -> None:
    params = {"period": "day", "date": "2026-10-09"}
    assert client.get("/api/stats", params=params).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_every_role_may_read(client_as, role) -> None:
    response = client_as(role).get("/api/stats", params={"period": "week", "date": "2026-10-09"})
    assert response.status_code == 200


def test_stats_route_shape(client_as, ledger, price_all) -> None:
    price_all(T0, T0 + SLOT)
    ingest(ledger, T0, {"grid>house": 1234.5678, "pv>house": 500.0})
    response = client_as("viewer").get(
        "/api/stats", params={"period": "month", "date": "2026-10-09"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == [
        "period",
        "first",
        "last",
        "complete",
        "coverage",
        "estimated_slots",
        "energy",
        "money",
        "ratios",
        "buckets",
        "vehicles",
        "battery_to_vehicles_kwh",
        "planned",
        "consumers",
        "month_peaks",
        "checks",
    ]
    assert (body["period"], body["first"], body["last"]) == ("month", "2026-10-01", "2026-10-31")
    assert list(body["energy"]) == [
        "pv_kwh",
        "import_kwh",
        "export_kwh",
        "house_kwh",
        "wallbox_kwh",
        "battery_charge_kwh",
        "battery_discharge_kwh",
        "consumption_kwh",
    ]
    assert list(body["money"]) == [
        "import_eur",
        "revenue_eur",
        "net_eur",
        "fees_eur",
        "power_eur",
        "total_eur",
        "savings_no_plant_eur",
        "savings_same_import_eur",
    ]
    assert list(body["ratios"]) == ["autarky", "self_consumption"]
    assert list(body["buckets"][0]) == [
        "start",
        "pv_kwh",
        "import_kwh",
        "export_kwh",
        "consumption_kwh",
        "net_eur",
    ]
    assert list(body["checks"]) == ["days", "flagged_days", "totals"]
    assert body["energy"]["import_kwh"] == 1.235  # auf drei Stellen gerundet
    assert body["buckets"][8]["import_kwh"] == 1.235  # auch in den Listen
    assert body["money"]["power_eur"] is None and body["month_peaks"][0]["kw"] == 4.938
    assert body["month_peaks"][0]["power_eur"] is None


def test_day_buckets_start_with_a_zone(client_as) -> None:
    body = (
        client_as("viewer").get("/api/stats", params={"period": "day", "date": "2026-10-25"}).json()
    )
    assert len(body["buckets"]) == 25
    assert body["buckets"][0]["start"] == "2026-10-25T00:00:00+02:00"


def test_date_defaults_to_today(client_as) -> None:
    body = client_as("viewer").get("/api/stats", params={"period": "week"}).json()
    assert (body["first"], body["last"]) == ("2026-10-05", "2026-10-11")
