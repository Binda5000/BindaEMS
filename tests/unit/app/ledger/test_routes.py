from datetime import timedelta

from tests.app_helpers import SLOT_FLOWS_MSG as MSG
from tests.app_helpers import login_as

from bindaems.app.ledger.service import parse_slot_flows


def test_days_endpoint(client, auth, ledger, clock) -> None:
    login_as(client, auth, "viewer")
    ledger.ingest(parse_slot_flows(MSG), "stream")
    clock.advance(timedelta(minutes=15))  # 10:15 Ortszeit
    [day] = client.get("/api/ledger/days", params={"from": "2026-10-09", "to": "2026-10-09"}).json()
    assert day["pv_kwh"] == 1.0 and day["expected_slots"] == 41 and day["date"] == "2026-10-09"
    assert (
        day["complete"] is False
        and day["coverage"] == 0.024
        and day["wallbox_kwh"] == {"evcs": 0.2, "twc": 0.25}
    )


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


def test_checks_route_returns_days_and_totals(client, auth, checks, engine, clock) -> None:
    from datetime import date

    from tests.unit.app.ledger.test_checks import make

    from bindaems.app.db.schema import ledger_check

    login_as(client, auth, "viewer")
    rows = [
        make(date(2026, 10, 8), "import", 10.2, 10.0, flagged=True),
        make(date(2026, 10, 8), "wb:evcs", 3.0, None, note="Zählerstand fehlt"),
        make(date(2026, 10, 9), "import", 5.0, 5.0),
    ]
    with engine.begin() as conn:
        conn.execute(
            ledger_check.insert(),
            [
                {
                    "day": row.day.isoformat(),
                    "signal": row.signal,
                    "ledger_kwh": row.ledger_kwh,
                    "counter_kwh": row.counter_kwh,
                    "deviation_pct": row.deviation_pct,
                    "flagged": row.flagged,
                    "note": row.note,
                    "checked_at": clock.now(),
                }
                for row in rows
            ],
        )
    body = client.get(
        "/api/ledger/checks", params={"from": "2026-10-08", "to": "2026-10-09"}
    ).json()
    assert body["days"][0] == {
        "day": "2026-10-08",
        "signal": "import",
        "ledger_kwh": 10.2,
        "counter_kwh": 10.0,
        "deviation_pct": 2.0,
        "flagged": True,
        "note": None,
    }
    assert [(d["day"], d["signal"]) for d in body["days"]] == [
        ("2026-10-08", "import"),
        ("2026-10-08", "wb:evcs"),
        ("2026-10-09", "import"),
    ]
    assert body["days"][1]["note"] == "Zählerstand fehlt" and body["days"][1]["counter_kwh"] is None
    assert body["totals"] == [
        {"signal": "import", "ledger_kwh": 15.2, "counter_kwh": 15.0, "deviation_pct": 1.333},
        {"signal": "wb:evcs", "ledger_kwh": 0.0, "counter_kwh": None, "deviation_pct": None},
    ]


def test_checks_route_limits_span_and_needs_login(client, auth) -> None:
    params = {"from": "2026-10-08", "to": "2026-10-09"}
    assert client.get("/api/ledger/checks", params=params).status_code == 401
    login_as(client, auth, "viewer")
    long = {"from": "2025-01-01", "to": "2026-10-09"}
    assert client.get("/api/ledger/checks", params=long).status_code == 422
    backwards = {"from": "2026-10-09", "to": "2026-10-08"}
    assert client.get("/api/ledger/checks", params=backwards).status_code == 422
    assert client.get("/api/ledger/checks", params=params).json() == {"days": [], "totals": []}
