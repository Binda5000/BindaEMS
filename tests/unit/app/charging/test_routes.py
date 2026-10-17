from datetime import timedelta

from sqlalchemy import insert
from tests.app_helpers import ingest, login_as, set_wallbox_names
from tests.unit.app.charging.conftest import T0

from bindaems.app.db.schema import charging_session
from bindaems.shared.timeutil import SLOT


def add_session(engine, start, end, vehicle="tesla", wallbox="twc") -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(charging_session).values(
                wallbox=wallbox, vehicle=vehicle, start=start, end=end, last_seen=end or start
            )
        )


PARAMS = {"from": "2026-10-09", "to": "2026-10-09"}


def test_sessions_route_is_newest_first_and_limited(
    client, auth, engine, ledger, clock, settings_service, cfg, monkeypatch
) -> None:
    clock.advance(timedelta(hours=3))
    login_as(client, auth, "viewer")
    set_wallbox_names(settings_service, {"twc": "Garage"})
    vehicle = next(iter(cfg.vehicles))
    add_session(engine, T0, T0 + SLOT, vehicle)
    add_session(engine, T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "guest")
    add_session(engine, T0 + timedelta(hours=2), None, "")
    ingest(ledger, T0, {"pv>wb:twc": 1000.0, "battery>wb:twc": 500.0})
    entries = client.get("/api/charging/sessions", params=PARAMS).json()
    assert [e["vehicle"] for e in entries] == ["unknown", "guest", vehicle]
    assert [e["vehicle_name"] for e in entries[:2]] == ["Unbekannt", "Gast"]
    assert entries[2]["vehicle_name"] == cfg.vehicles[vehicle].name
    last = entries[2]
    assert last["wallbox"] == "twc" and last["wallbox_name"] == "Garage"
    assert last["open"] is False and entries[0]["open"] is True and entries[0]["end"] is None
    assert last["start"] == "2026-10-09T08:00:00+00:00"
    assert (last["kwh"], last["pv_kwh"], last["battery_kwh"], last["grid_kwh"]) == (
        1.5,
        1.0,
        0.5,
        0.0,
    )
    assert last["cost_eur"] == 0.01  # 0,5 kWh Akku zu 0 ct Einstand + 2 ct Verschleiß
    filtered = client.get("/api/charging/sessions", params={**PARAMS, "vehicle": "guest"}).json()
    assert [e["vehicle"] for e in filtered] == ["guest"]
    monkeypatch.setattr("bindaems.app.charging.routes.MAX_SESSIONS", 2)
    assert len(client.get("/api/charging/sessions", params=PARAMS).json()) == 2


def test_sessions_route_rejects_more_than_400_days(client, auth) -> None:
    login_as(client, auth, "viewer")
    params = {"from": "2025-01-01", "to": "2026-10-09"}
    assert client.get("/api/charging/sessions", params=params).status_code == 422
    params = {"from": "2026-10-09", "to": "2026-10-08"}
    assert client.get("/api/charging/sessions", params=params).status_code == 422
    params = {"from": "2025-09-05", "to": "2026-10-09"}  # genau 400 Tage
    assert client.get("/api/charging/sessions", params=params).status_code == 200


def test_sessions_route_needs_a_login(client) -> None:
    assert client.get("/api/charging/sessions", params=PARAMS).status_code == 401


def test_sessions_route_rounds_to_three_digits(client, auth, engine, ledger) -> None:
    login_as(client, auth, "viewer")
    add_session(engine, T0, T0 + SLOT)
    ingest(ledger, T0, {"pv>wb:twc": 1234.5678})
    response = client.get("/api/charging/sessions", params=PARAMS)
    assert response.status_code == 200, response.text
    [entry] = response.json()
    assert entry["kwh"] == 1.235
