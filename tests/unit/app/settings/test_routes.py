import json

import pytest
from tests.app_helpers import login_as

from bindaems.app.settings.routes import settings_router


@pytest.fixture
def client(make_client, settings_service, cfg, guard):
    return make_client(settings_router(settings_service, cfg, guard))


def test_viewer_reads_settings_with_warnings(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/settings").json()
    assert body["version"] == 1 and body["settings"]["tariff"]["vat_pct"] == 20.0
    assert "Noch kein OeMAG-Monatswert eingetragen." in body["warnings"]


def test_only_admin_updates_and_conflict_is_409(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    settings = client.get("/api/settings").json()["settings"]
    settings["feed_in"]["monthly_ct"] = {"2026-09": 7.3}
    first = client.put(
        "/api/settings", json={"base_version": 1, "settings": settings}, headers=csrf
    )
    assert first.json()["version"] == 2
    conflict = client.put(
        "/api/settings", json={"base_version": 1, "settings": settings}, headers=csrf
    )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": "Die Einstellungen wurden inzwischen geändert (aktuell Version 2)."
    }


def test_operator_cannot_update(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    response = client.put("/api/settings", json={"base_version": 1, "settings": {}}, headers=csrf)
    assert response.status_code == 403


def test_invalid_settings_are_422(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    response = client.put(
        "/api/settings",
        json={"base_version": 1, "settings": {"tariff": {"vat_pct": -1}}},
        headers=csrf,
    )
    assert response.status_code == 422


def test_export_and_import_via_api(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    exported = client.get("/api/settings/export")
    assert exported.headers["content-type"].startswith("text/yaml")
    assert 'filename="bindaems-einstellungen-v1.yaml"' in exported.headers["content-disposition"]
    text = exported.text.replace("fixed_price_gross_ct: 30.0", "fixed_price_gross_ct: 29.0")
    imported = client.post("/api/settings/import", json={"yaml": text}, headers=csrf)
    assert imported.json()["version"] == 2
    assert imported.json()["settings"]["tariff"]["fixed_price_gross_ct"] == 29.0


def test_limits_show_hard_limits_without_hosts_or_vin(client, auth) -> None:
    login_as(client, auth, "viewer")
    limits = client.get("/api/limits").json()
    assert limits["grid"]["fuse_a"] == 35 and limits["battery"]["reserve_soc_pct"] == 20
    assert limits["wallboxes"]["evcs"]["max_a"] == 16
    assert limits["vehicles"]["egolf"]["phases"] == 2
    text = json.dumps(limits)
    assert "host" not in text and "5YJ3E7EB0MF000000" not in text


def test_wallbox_names_from_settings_appear_in_limits(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    assert client.get("/api/limits").json()["wallboxes"]["evcs"]["name"] is None
    settings = client.get("/api/settings").json()["settings"]
    settings["wallbox_names"] = {"evcs": "  Garage  "}
    saved = client.put(
        "/api/settings", json={"base_version": 1, "settings": settings}, headers=csrf
    )
    assert saved.json()["settings"]["wallbox_names"] == {"evcs": "Garage"}
    wallboxes = client.get("/api/limits").json()["wallboxes"]
    assert (wallboxes["evcs"]["name"], wallboxes["twc"]["name"]) == ("Garage", None)


@pytest.mark.parametrize("name", ["", "   ", "x" * 41])
def test_wallbox_names_must_not_be_empty_or_too_long(client, auth, name) -> None:
    csrf = login_as(client, auth, "admin")
    settings = client.get("/api/settings").json()["settings"]
    settings["wallbox_names"] = {"evcs": name}
    response = client.put(
        "/api/settings", json={"base_version": 1, "settings": settings}, headers=csrf
    )
    assert response.status_code == 422
