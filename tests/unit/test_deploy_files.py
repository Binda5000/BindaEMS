import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import yaml

SCRIPT = Path("deploy/influxdb-setup.sh")


def test_compose_references_existing_files() -> None:
    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text())
    svc = compose["services"]["ems-core"]
    assert Path(svc["build"]["dockerfile"]).exists()
    assert svc["env_file"] == [".env"] or svc["env_file"] == ".env"
    assert "ports" not in svc
    assert svc["networks"] == ["internal"]
    # „internal“ ist nur der Name: der core braucht LAN (Cerbo, Wallboxen) und Internet (Tessie)
    assert compose["networks"]["internal"].get("internal") is not True


class _FakeInflux(ThreadingHTTPServer):
    statements: list[str]
    auth: list[str | None]
    policies: set[str]


class _Handler(BaseHTTPRequestHandler):
    server: _FakeInflux

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        q = parse_qs(self.rfile.read(length).decode())["q"][0]
        self.server.statements.append(q)
        self.server.auth.append(self.headers.get("Authorization"))
        result: dict[str, object] = {"statement_id": 0}
        if q.startswith("CREATE RETENTION POLICY"):
            name = q.split('"')[1]
            if name in self.server.policies:
                result["error"] = "retention policy already exists"
            self.server.policies.add(name)
        if q.startswith("DROP CONTINUOUS QUERY"):
            result["error"] = "continuous query not found"
        body = json.dumps({"results": [result]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def fake_influx() -> Iterator[_FakeInflux]:
    server = _FakeInflux(("127.0.0.1", 0), _Handler)
    server.statements, server.auth, server.policies = [], [], set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _run_setup(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    proxy = {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}
    env = {k: v for k, v in os.environ.items() if k not in proxy and not k.startswith("INFLUX_")}
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 - festes Skript aus dem Repository
        [bash, str(SCRIPT)], env=env | env_extra, capture_output=True, text=True, timeout=30
    )


def test_influx_setup_creates_database_policies_and_queries(fake_influx) -> None:
    env = {
        "INFLUX_URL": f"http://127.0.0.1:{fake_influx.server_address[1]}",
        "INFLUX_ADMIN_USER": "admin",
        "INFLUX_ADMIN_PASSWORD": "geheim",
    }
    first = _run_setup(env)
    assert first.returncode == 0, first.stderr
    statements = list(fake_influx.statements)
    assert statements[0] == 'CREATE DATABASE "bindaems"'
    assert (
        'CREATE RETENTION POLICY "raw" ON "bindaems" DURATION 90d REPLICATION 1 DEFAULT'
        in statements
    )
    assert any(
        s.startswith('CREATE RETENTION POLICY "long" ON "bindaems" DURATION INF')
        for s in statements
    )
    created = [s for s in statements if s.startswith("CREATE CONTINUOUS QUERY")]
    assert [s.split('"')[1] for s in created] == [
        "cq_power_1m",
        "cq_soc_1m",
        "cq_energy_1m",
        "cq_flows_15m",
    ]
    assert (
        'INTO "bindaems"."long"."power" FROM "bindaems"."raw"."power" GROUP BY time(1m), *'
        in created[0]
    )
    assert "mean(p_w)" in created[0] and "mean(i_a)" in created[0] and "mean(u_v)" in created[0]
    assert "mean(pct)" in created[1] and "last(kwh)" in created[2] and "sum(wh)" in created[3]
    assert "GROUP BY time(15m), *" in created[3]
    assert set(fake_influx.auth) == {"Basic YWRtaW46Z2VoZWlt"}  # admin:geheim

    second = _run_setup(env)  # zweiter Lauf: Policies existieren schon → ALTER
    assert second.returncode == 0, second.stderr
    altered = [s for s in fake_influx.statements[len(statements) :] if s.startswith("ALTER")]
    assert [s.split('"')[1] for s in altered] == ["raw", "long"]


def test_influx_setup_requires_environment() -> None:
    result = _run_setup({})
    assert result.returncode != 0
    assert "INFLUX_URL" in result.stderr


def test_healthcheck_returns_1_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_CORE_PORT", "1")  # nichts lauscht auf Port 1
    from bindaems.core.healthcheck import main

    assert main() == 1


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ok = self.path == "/v1/health" and self.headers.get("Authorization") == "Bearer " + "x" * 32
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        return None


@pytest.mark.parametrize(("token", "expected"), [("x" * 32, 0), ("y" * 32, 1)])
def test_healthcheck_checks_api_with_token(monkeypatch, token: str, expected: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", token)
        monkeypatch.setenv("BINDAEMS_CORE_PORT", str(server.server_address[1]))
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")  # ein Proxy darf nicht greifen
        from bindaems.core.healthcheck import main

        assert main() == expected
    finally:
        server.shutdown()


def test_app_healthcheck_returns_1_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("BINDAEMS_APP_PORT", "1")  # nichts lauscht auf Port 1
    from bindaems.app.healthcheck import main as health

    assert health() == 1


class _AppHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        return None


def test_app_healthcheck_returns_0_when_healthy(monkeypatch) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AppHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("BINDAEMS_APP_PORT", str(server.server_address[1]))
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, "http://127.0.0.1:9")  # ein Proxy darf nicht greifen
        from bindaems.app.healthcheck import main as health

        assert health() == 0
    finally:
        server.shutdown()
