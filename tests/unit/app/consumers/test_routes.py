import asyncio

from tests.app_helpers import core_input, login_as

from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.routes import consumers_router
from bindaems.app.consumers.service import ConsumerInput


def test_tree_endpoint_with_live_values(client, auth, service, live) -> None:
    login_as(client, auth, "viewer")
    service.create(core_input("OG", ref="load.obergeschoss.power_w"), actor="chris", source="ui")
    body = client.get("/api/consumers").json()
    tree = body["tree"]
    assert tree["power_w"] == live.derived()["house_load_w"] and tree["children"][0]["name"] == "OG"
    assert tree["children"][0]["power_w"] == 640.0 and tree["other_w"] == 1460.0
    assert tree["children"][0]["note"] is None and tree["note"] is None
    assert body["consumers"][0]["power_ref"] == "load.obergeschoss.power_w"


def test_tree_says_why_an_ha_value_is_missing(client, auth, service) -> None:
    login_as(client, auth, "viewer")
    server = ConsumerInput(
        name="Serverschrank",
        color="#000000",
        source_kind="ha",
        power_ref="sensor.serverschrank_power",
        power_unit="W",
    )
    service.create(server, actor="chris", source="ui")
    node = client.get("/api/consumers").json()["tree"]["children"][0]
    assert (node["power_w"], node["note"]) == (
        None,
        "HA-Datenbank nicht eingerichtet (influxdb.ha_database)",
    )


def test_changes_are_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    body = {"name": "X", "color": "#000000", "source_kind": "core", "power_ref": "load.x.power_w"}
    assert client.post("/api/consumers", json=body, headers=csrf).status_code == 403


def test_admin_changes_map_errors_to_statuses(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    body = {"name": "OG", "color": "#000000", "source_kind": "core", "power_ref": "load.og.power_w"}
    created = client.post("/api/consumers", json=body, headers=csrf)
    assert created.status_code == 201
    og = created.json()["id"]
    child = client.post(
        "/api/consumers", json={**body, "name": "Küche", "parent_id": og}, headers=csrf
    ).json()
    cycle = client.patch(
        f"/api/consumers/{og}", json={**body, "parent_id": child["id"]}, headers=csrf
    )
    assert (cycle.status_code, cycle.json()) == (
        422,
        {"detail": "Ein Verbraucher kann nicht unter sich selbst hängen"},
    )
    assert client.delete(f"/api/consumers/{og}", headers=csrf).status_code == 409
    assert client.patch("/api/consumers/999", json=body, headers=csrf).status_code == 404
    assert client.delete(f"/api/consumers/{2**70}", headers=csrf).status_code == 422
    assert client.delete(f"/api/consumers/{child['id']}", headers=csrf).status_code == 204


def test_tree_endpoint_with_energy_since_midnight(
    make_client, service, live, reader, cfg, guard, auth, clock, respx_mock
) -> None:
    respx_mock.get("http://influx.lan:8086/query").respond(
        json={
            "results": [
                {
                    "series": [
                        {
                            "name": "power",
                            "tags": {"source": "load", "id": "obergeschoss"},
                            "columns": ["time", "w"],
                            "values": [[1791504000000, 600.0]],
                        }
                    ]
                }
            ]
        }
    )
    energy = EnergyCache(reader, clock, None)
    client = make_client(consumers_router(service, live, None, reader, cfg, guard, energy))
    login_as(client, auth, "viewer")
    service.create(core_input("OG", ref="load.obergeschoss.power_w"), actor="chris", source="ui")
    assert client.get("/api/consumers").json()["energy_since"] is None
    asyncio.run(energy.refresh(service.list()))
    body = client.get("/api/consumers").json()
    assert body["energy_since"] == "2026-10-08T22:00:00+00:00"
    og = body["tree"]["children"][0]
    assert (og["energy_kwh"], og["energy_note"]) == (0.01, None)  # 1 min × 600 W
    assert (body["tree"]["energy_kwh"], body["tree"]["other_kwh"]) == (None, None)
