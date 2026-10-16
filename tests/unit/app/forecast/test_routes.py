import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from tests.app_helpers import ingest, login_as
from tests.unit.app.forecast.conftest import NOW

from bindaems.app.forecast.archive import RecordValue


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


def test_quality_route_validates_days(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality?days=0").status_code == 422
    assert client.get("/api/forecast/quality?days=61").status_code == 422
    assert client.get("/api/forecast/quality?days=60").status_code == 200
    assert client.get("/api/forecast/quality?days=1").status_code == 200


def test_quality_route_without_records(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/forecast/quality").json() == {"days": 14, "rows": []}


def test_quality_route_counts_back_from_today(
    client, auth, clock, archive, ledger, quality
) -> None:
    clock.advance(NOW - clock.now())  # heute ist der 16.10.2026 (Ortszeit)
    slot = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)  # 13 Tage zuvor: noch im Fenster
    archive.record("load", slot - timedelta(hours=3), {slot: RecordValue(1000, 1000)})
    ingest(ledger, slot, {"grid>house": 200})  # 800 W
    old = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)  # 14 Tage zuvor: außerhalb
    archive.record("load", old - timedelta(hours=3), {old: RecordValue(1000, 1000)})
    ingest(ledger, old, {"grid>house": 200})
    quality.update(date(2026, 10, 3))
    quality.update(date(2026, 10, 2))
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/quality?days=14").json()
    assert body == {
        "days": 14,
        "rows": [
            {
                "kind": "load",
                "horizon": "h0_6",
                "n": 1,
                "mae_w": 200.0,
                "bias_w": 200.0,
                "rel_mae_pct": 25.0,
            }
        ],
    }


def test_quality_route_needs_login(client) -> None:
    assert client.get("/api/forecast/quality").status_code == 401
