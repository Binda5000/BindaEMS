from tests.app_helpers import SLOT_FLOWS_MSG as MSG
from tests.app_helpers import login_as

from bindaems.app.ledger.service import parse_slot_flows


def test_days_endpoint(client, auth, ledger) -> None:
    login_as(client, auth, "viewer")
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = client.get("/api/ledger/days", params={"from": "2026-10-09", "to": "2026-10-09"}).json()
    assert day["pv_kwh"] == 1.0 and day["expected_slots"] == 96 and day["date"] == "2026-10-09"
    assert day["coverage"] == 0.01 and day["wallbox_kwh"] == {"evcs": 0.2, "twc": 0.25}


def test_days_span_is_limited(client, auth) -> None:
    login_as(client, auth, "viewer")
    params = {"from": "2025-01-01", "to": "2026-10-09"}
    assert client.get("/api/ledger/days", params=params).status_code == 422
    params = {"from": "2026-10-09", "to": "2026-10-08"}
    assert client.get("/api/ledger/days", params=params).status_code == 422


def test_slots_endpoint(client, auth, ledger) -> None:
    login_as(client, auth, "viewer")
    ledger.ingest(parse_slot_flows(MSG), "stream")
    params = {"from": "2026-10-09T00:00:00Z", "to": "2026-10-10T00:00:00Z"}
    [slot] = client.get("/api/ledger/slots", params=params).json()
    assert slot["slot_start"] == "2026-10-09T08:00:00+00:00" and slot["import_wh"] == 400.0
    params = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-09T00:00:00Z"}
    assert client.get("/api/ledger/slots", params=params).status_code == 422


def test_reprice_is_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    body = {"from": "2026-10-09", "to": "2026-10-09"}
    assert client.post("/api/ledger/reprice", json=body, headers=csrf).status_code == 403


def test_reprice_as_admin(client, auth, ledger) -> None:
    csrf = login_as(client, auth, "admin")
    ledger.ingest(parse_slot_flows(MSG), "stream")
    body = {"from": "2026-10-09", "to": "2026-10-09"}
    assert client.post("/api/ledger/reprice", json=body, headers=csrf).json() == {"repriced": 1}
