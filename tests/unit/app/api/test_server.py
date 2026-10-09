from pathlib import Path

from fastapi.testclient import TestClient

from bindaems.app.api.server import create_app


def test_health_needs_no_login() -> None:
    response = TestClient(create_app([])).get("/health")
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_security_headers_on_every_response() -> None:
    headers = TestClient(create_app([])).get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_unknown_api_path_is_german_404_and_not_cached() -> None:
    response = TestClient(create_app([])).get("/api/gibt-es-nicht")
    assert response.status_code == 404 and response.json() == {"detail": "Nicht gefunden"}
    assert response.headers["cache-control"] == "no-store"


def test_no_openapi_or_docs() -> None:
    client = TestClient(create_app([]))
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_ui_with_spa_fallback_and_inline_script_hash(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html><script>boot()</script></html>")
    (ui / "app.js").write_text("console.log(1)")
    (tmp_path / "geheim.txt").write_text("geheim")
    client = TestClient(create_app([], ui_dir=ui))
    index = client.get("/einstellungen/tarif")
    assert index.text.startswith("<html>") and index.headers["cache-control"] == "no-cache"
    csp = index.headers["content-security-policy"]
    assert "'sha256-MeZS89WlF0u+o0hCvHTBt4q1WHU+U+sJKgbdRUc36mY='" in csp
    assert client.get("/app.js").text == "console.log(1)"
    assert "geheim" not in client.get("/%2e%2e/geheim.txt").text
