from tests.app_helpers import core_input, login_as


def test_tree_endpoint_with_live_values(client, auth, service, live) -> None:
    login_as(client, auth, "viewer")
    service.create(core_input("OG", ref="load.obergeschoss.power_w"), actor="chris", source="ui")
    body = client.get("/api/consumers").json()
    tree = body["tree"]
    assert tree["power_w"] == live.derived()["house_load_w"] and tree["children"][0]["name"] == "OG"
    assert tree["children"][0]["power_w"] == 640.0 and tree["other_w"] == 1460.0
    assert body["consumers"][0]["power_ref"] == "load.obergeschoss.power_w"


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
