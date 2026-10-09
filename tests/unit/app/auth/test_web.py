import pyotp
from fastapi.testclient import TestClient
from tests.app_helpers import PASSWORD, login_as

from bindaems.app.api.server import create_app
from bindaems.app.auth.routes import auth_router
from bindaems.app.auth.web import Guard


def test_login_sets_strict_secure_cookies(make_client, auth) -> None:
    client = make_client()
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"]["role"] == "operator"
    cookies = {c.split("=")[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    assert set(cookies) == {"bindaems_session", "bindaems_csrf"}
    for cookie in cookies.values():
        assert "secure" in cookie and "samesite=strict" in cookie and "max-age" not in cookie
    assert "httponly" in cookies["bindaems_session"] and "httponly" not in cookies["bindaems_csrf"]


def test_remember_sets_max_age_30_days(make_client, auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    response = make_client().post(
        "/api/auth/login", json={"username": "chris", "password": PASSWORD, "remember": True}
    )
    assert "Max-Age=2592000" in response.headers["set-cookie"]


def test_cookies_without_secure_when_configured(auth) -> None:
    guard = Guard(auth, cookie_secure=False)
    client = TestClient(create_app([auth_router(auth, guard)]))
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 200
    assert "secure" not in response.headers["set-cookie"].lower()


def test_api_requires_login(make_client) -> None:
    response = make_client().get("/api/auth/me")
    assert response.status_code == 401 and response.json() == {"detail": "Nicht angemeldet"}


def test_unsafe_requests_need_csrf_header(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "viewer")
    rejected = client.post("/api/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "CSRF-Token fehlt oder ist ungültig"}
    assert client.post("/api/auth/logout", headers=csrf).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_viewer_cannot_manage_users(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "viewer")
    response = client.post(
        "/api/users",
        json={"username": "x1x", "password": PASSWORD, "role": "admin"},
        headers=csrf,
    )
    assert response.status_code == 403 and response.json() == {"detail": "Keine Berechtigung"}


def test_admin_manages_users_and_changes_are_audited(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    created = client.post(
        "/api/users",
        json={"username": "gast", "password": PASSWORD, "role": "viewer"},
        headers=csrf,
    )
    assert created.status_code == 201
    uid = created.json()["id"]
    changed = client.patch(f"/api/users/{uid}", json={"role": "operator"}, headers=csrf)
    assert changed.json()["role"] == "operator"
    assert client.delete(f"/api/users/{uid}", headers=csrf).status_code == 204
    actions = [entry["action"] for entry in client.get("/api/audit").json()]
    assert actions[:3] == ["user.delete", "user.role", "user.create"]


def test_last_admin_cannot_delete_itself(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin", username="chris")
    me = client.get("/api/auth/me").json()["user"]["id"]
    response = client.delete(f"/api/users/{me}", headers=csrf)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Der letzte Admin kann nicht entfernt oder herabgestuft werden"
    }


def test_rejected_role_change_leaves_the_password_unchanged(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin", username="chris")
    me = client.get("/api/auth/me").json()["user"]["id"]
    response = client.patch(
        f"/api/users/{me}", json={"password": "neues-passwort-1", "role": "viewer"}, headers=csrf
    )
    assert response.status_code == 409
    auth.check_password(me, PASSWORD)  # wirft bei geändertem Passwort
    assert client.get("/api/audit").json()[0]["action"] == "user.create"


def test_lockout_answers_429_with_retry_after(make_client, auth) -> None:
    client = make_client()
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):
        failed = client.post(
            "/api/auth/login", json={"username": "chris", "password": "falsch-falsch"}
        )
        assert failed.status_code == 401
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    assert response.json() == {"detail": "Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr"}


def test_login_reports_totp_requirement(make_client, auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id, PASSWORD)
    auth.totp_enable(user.id, pyotp.TOTP(secret).at(clock.now()), actor="chris", source="ui")
    response = make_client().post(
        "/api/auth/login", json={"username": "chris", "password": PASSWORD}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Bestätigungscode erforderlich", "totp_required": True}


def test_password_change_requires_old_password(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "operator")
    wrong = client.post(
        "/api/auth/password",
        json={"old_password": "falsch-falsch", "new_password": "neues-passwort-1"},
        headers=csrf,
    )
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Altes Passwort falsch"}
    changed = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": "neues-passwort-1"},
        headers=csrf,
    )
    assert changed.status_code == 204
    assert client.get("/api/auth/me").status_code == 200  # die eigene Sitzung bleibt


def test_totp_setup_and_enable_via_api(make_client, auth, clock) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    secret = client.post("/api/auth/totp/setup", json={"password": PASSWORD}, headers=csrf).json()[
        "secret"
    ]
    code = pyotp.TOTP(secret).at(clock.now())
    enabled = client.post("/api/auth/totp/enable", json={"code": code}, headers=csrf)
    assert enabled.status_code == 204
    assert client.get("/api/auth/me").json()["user"]["totp_enabled"] is True


def test_wrong_old_passwords_lock_the_account(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "operator")
    body = {"old_password": "falsch-falsch", "new_password": "neues-passwort-1"}
    for _ in range(5):
        assert client.post("/api/auth/password", json=body, headers=csrf).status_code == 400
    body["old_password"] = PASSWORD
    locked = client.post("/api/auth/password", json=body, headers=csrf)
    assert locked.status_code == 429 and locked.headers["retry-after"] == "900"


def test_totp_setup_requires_the_password(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    assert client.post("/api/auth/totp/setup", headers=csrf).status_code == 422
    wrong = client.post("/api/auth/totp/setup", json={"password": "falsch-falsch"}, headers=csrf)
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Passwort falsch"}


def test_wrong_passwords_for_totp_disable_lock_the_account(make_client, auth, clock) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    secret = client.post("/api/auth/totp/setup", json={"password": PASSWORD}, headers=csrf).json()[
        "secret"
    ]
    code = pyotp.TOTP(secret).at(clock.now())
    client.post("/api/auth/totp/enable", json={"code": code}, headers=csrf)
    for _ in range(5):
        wrong = client.post(
            "/api/auth/totp/disable", json={"password": "falsch-falsch"}, headers=csrf
        )
        assert wrong.status_code == 400
    locked = client.post("/api/auth/totp/disable", json={"password": PASSWORD}, headers=csrf)
    assert locked.status_code == 429


BACKGROUND = {"X-Bindaems-Background": "1"}


def test_background_requests_do_not_keep_the_session_alive(make_client, auth, clock) -> None:
    # Übersicht offen, niemand bedient: die Admin-Sitzung endet nach 30 min (Spec 14)
    client = make_client()
    login_as(client, auth, "admin")
    clock.advance(20 * 60)
    assert client.get("/api/auth/me", headers=BACKGROUND).status_code == 200
    clock.advance(11 * 60)
    assert client.get("/api/auth/me", headers=BACKGROUND).status_code == 401


def test_user_requests_keep_the_session_alive(make_client, auth, clock) -> None:
    client = make_client()
    login_as(client, auth, "admin")
    clock.advance(20 * 60)
    assert client.get("/api/auth/me").status_code == 200
    clock.advance(11 * 60)
    assert client.get("/api/auth/me").status_code == 200
