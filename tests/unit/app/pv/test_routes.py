import pytest
from sqlalchemy import insert
from tests.app_helpers import login_as

from bindaems.app.consumers.days import SourceDays
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import pv_source_day
from bindaems.app.pv.routes import pv_router
from bindaems.app.pv.service import PvSourceInput

BODY = {
    "name": "Huawei",
    "color": "#f59e0b",
    "source_kind": "core",
    "power_ref": "pv.huawei.power_w",
}


@pytest.fixture
def live() -> LiveState:
    state = LiveState()
    state.connected = True
    state.state = {
        "signals": {
            "pv.huawei.power_w": {"v": 4200.0, "ts": "…", "q": "ok"},
            "pv.huawei.dc": {"v": 1.0, "ts": "…", "q": "ok"},
            "load.x.power_w": {"v": 1.0, "ts": "…", "q": "ok"},
        },
        "derived": {"pv_total_w": 5000.0},
    }
    return state


@pytest.fixture
def client(make_client, service, live, reader, cfg, guard):
    return make_client(pv_router(service, live, None, reader, cfg, guard))


def test_viewer_reads_tree_with_live_values(client, auth, service) -> None:
    login_as(client, auth, "viewer")
    service.create(PvSourceInput(**BODY), actor="chris", source="ui")
    body = client.get("/api/pv").json()
    assert body["total_w"] == 5000.0 and body["total_kwh"] is None
    system, rest = body["tree"]
    assert (system["name"], system["power_w"], system["kind"]) == ("Huawei", 4200.0, "system")
    assert (rest["kind"], rest["power_w"]) == ("unassigned", 800.0)
    assert body["sources"][0]["power_ref"] == "pv.huawei.power_w"
    assert body["energy_since"] is None


def test_viewer_cannot_create(client, auth) -> None:
    csrf = login_as(client, auth, "viewer")
    assert client.post("/api/pv", json=BODY, headers=csrf).status_code == 403


def test_admin_creates_changes_and_deletes(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    created = client.post("/api/pv", json=BODY, headers=csrf)
    assert created.status_code == 201
    sid = created.json()["id"]
    string = client.post("/api/pv", json={**BODY, "name": "S1", "parent_id": sid}, headers=csrf)
    assert string.status_code == 201
    changed = client.patch(f"/api/pv/{sid}", json={**BODY, "name": "Dach"}, headers=csrf)
    assert changed.json()["name"] == "Dach"
    assert client.delete(f"/api/pv/{sid}", headers=csrf).status_code == 409
    assert client.delete(f"/api/pv/{string.json()['id']}", headers=csrf).status_code == 204
    assert client.delete(f"/api/pv/{sid}", headers=csrf).status_code == 204
    assert client.patch("/api/pv/999", json=BODY, headers=csrf).status_code == 404


def test_candidates_admin_only_and_pv_signals(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/pv/candidates").status_code == 403
    client.post("/api/auth/logout")
    login_as(client, auth, "admin")
    body = client.get("/api/pv/candidates").json()
    assert body["core"] == ["pv.huawei.power_w", "pv_dc.power_w"]
    assert "ha" in body


@pytest.fixture
def period_client(make_client, service, live, reader, cfg, guard, engine, clock):
    energy = EnergyCache(reader, clock, None, retention="long")
    days = SourceDays(engine, energy, service.list, pv_source_day, "source_id", clock, total_id=0)
    return make_client(pv_router(service, live, None, reader, cfg, guard, energy, days, clock))


def test_day_in_future_rejected(period_client, auth) -> None:
    login_as(period_client, auth, "viewer")
    assert period_client.get("/api/pv/day?date=2026-10-10").status_code == 422
    assert period_client.get("/api/pv/period?period=day&day=2026-10-10").status_code == 422
    assert period_client.get("/api/pv/period?period=decade").status_code == 422


def test_period_month_with_unassigned(period_client, auth, service, engine) -> None:
    login_as(period_client, auth, "viewer")
    sid = service.create(PvSourceInput(**BODY, kwp=10.0), actor="x", source="ui").id
    with engine.begin() as conn:
        conn.execute(
            insert(pv_source_day),
            [
                {"day": "2026-10-07", "source_id": 0, "kwh": 30.0, "note": None},
                {"day": "2026-10-07", "source_id": sid, "kwh": 27.5, "note": None},
            ],
        )
    body = period_client.get("/api/pv/period?period=month&day=2026-10-09").json()
    assert (body["first"], body["last"]) == ("2026-10-01", "2026-10-31")
    assert len(body["buckets"]) == 31
    (row,) = body["sources"]
    assert row["kwh"][6] == 27.5 and row["kwh"][5] is None and row["kwh"][8] is None
    assert row["sum_kwh"] == 27.5 and row["kwh_per_kwp"] == 2.75
    assert row["first_known"] == "2026-10-07"
    assert body["unassigned"]["kwh"][6] == 2.5 and body["unassigned"]["sum_kwh"] == 2.5
    assert body["total"]["sum_kwh"] == 30.0


def test_day_curve_route(client, auth, service, respx_mock) -> None:
    login_as(client, auth, "viewer")
    service.create(PvSourceInput(**BODY), actor="x", source="ui")
    respx_mock.post("http://influx.lan:8086/query").respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "power",
                            "tags": {"source": "pv", "id": "huawei"},
                            "columns": ["time", "w"],
                            "values": [[1000, 5.0]],
                        }
                    ]
                }
            ]
        }
    )
    body = client.get("/api/pv/day?date=2026-10-08").json()
    assert body["date"] == "2026-10-08"
    assert body["series"] == [{"id": 1, "points": [[1000, 5.0]], "note": None}]
