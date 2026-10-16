import asyncio

import pytest
from tests.app_helpers import ingest, login_as
from tests.unit.app.forecast.conftest import NOW


def test_pv_route_has_quantiles(client, auth, filled_forecast):
    login_as(client, auth, "viewer")
    slots = client.get("/api/forecast/pv").json()["slots"]
    assert slots and set(slots[0]) == {"start", "p50_w", "p25_w", "p75_w"}
    assert all(s["p25_w"] <= s["p50_w"] <= s["p75_w"] for s in slots)
    assert any(s["p25_w"] < s["p50_w"] < s["p75_w"] for s in slots)


def test_load_route_shape_without_forecast(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/load").json()
    assert set(body) == {"issued_at", "slots", "days", "basis", "status"}
    assert body["issued_at"] is None and body["slots"] == []
    assert body["basis"] == {"ledger": 0, "ha": 0}
    assert [d["kwh"] for d in body["days"]] == [None, None, None]
    assert body["status"]["message"] == "Noch keine Daten für die Lastprognose"


def test_load_route_with_forecast(client, auth, load_service, ledger, clock) -> None:
    clock.advance(NOW - clock.now())  # die Route zählt die Tage von der Uhr der app
    for day in ("2026-10-13", "2026-10-14", "2026-10-15"):
        ingest(ledger, f"{day}T08:00:00+00:00", {"grid>house": 125})  # 500 W
    asyncio.run(load_service.refresh())
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/load").json()
    assert body["issued_at"] == "2026-10-16T06:00:00+00:00"
    assert set(body["slots"][0]) == {"start", "p50_w"} and body["slots"][0]["p50_w"] == 500
    assert body["basis"] == {"ledger": 3, "ha": 0}
    assert [d["date"] for d in body["days"]] == ["2026-10-16", "2026-10-17", "2026-10-18"]
    assert [d["kwh"] for d in body["days"]] == pytest.approx([8.0, 12.0, 12.0])  # ab 08:00 Uhr
    assert body["status"]["ok"] is True


def test_load_route_needs_login(client) -> None:
    assert client.get("/api/forecast/load").status_code == 401
