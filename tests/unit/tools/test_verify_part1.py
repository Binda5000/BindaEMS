import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from bindaems.tools.verify_part1 import clock_offset_s, main, redact

VIN = "5YJ3E7EB0MF000000"
TESSIE_STATE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "tessie_state.json").read_text()
)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_redact_hides_secrets_location_and_vin() -> None:
    data = {
        "access_token": "abc",
        "vin": VIN,
        "drive_state": {"latitude": 48.2, "longitude": 16.37, "speed": 0},
        "items": [{"password": "x", "state": "on"}],
        "driving": "bleibt",
    }
    assert redact(data) == {
        "access_token": "***",
        "vin": "***",
        "drive_state": {"latitude": "***", "longitude": "***", "speed": 0},
        "items": [{"password": "***", "state": "on"}],
        "driving": "bleibt",
    }


def test_clock_offset_from_date_header() -> None:
    now = datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC)
    assert clock_offset_s("Thu, 08 Oct 2026 10:00:05 GMT", now) == 5.0
    assert clock_offset_s("kein Datum", now) is None
    assert clock_offset_s(None, now) is None


def _config(tmp_path: Path, **overrides) -> Path:
    raw = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    raw["victron"]["mqtt"] |= {
        "host": "127.0.0.1",
        "port": _closed_port(),
        "portal_id": "c0619ab1234",
    }
    raw["wallboxes"]["evcs"] |= {"host": "127.0.0.1", "port": _closed_port()}
    for path, value in overrides.items():
        section, key = path.split(".")
        raw[section][key] = value
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config


def _env(monkeypatch, **values: str) -> None:
    for name in ("TESSIE_TOKEN", "HA_TOKEN", "MQTT_PASSWORD", "INFLUX_PASSWORD"):
        monkeypatch.delenv(f"BINDAEMS_{name}", raising=False)
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    for name, value in values.items():
        monkeypatch.setenv(f"BINDAEMS_{name}", value)


def _outputs(out: Path) -> tuple[str, str]:
    (report,) = out.glob("*-pruefprotokoll-teil1.md")
    (raw,) = out.glob("*-pruefprotokoll-teil1-rohdaten.json")
    return report.read_text(encoding="utf-8"), raw.read_text(encoding="utf-8")


def test_unreachable_sources_are_reported_and_secrets_never_written(tmp_path, monkeypatch) -> None:
    closed = _closed_port()
    config = _config(
        tmp_path,
        **{
            "homeassistant.url": f"http://127.0.0.1:{closed}",
            "influxdb.url": f"http://127.0.0.1:{closed}",
        },
    )
    raw_cfg = yaml.safe_load(config.read_text())
    raw_cfg["wallboxes"]["twc"]["host"] = f"127.0.0.1:{closed}"
    config.write_text(yaml.safe_dump(raw_cfg))
    _env(
        monkeypatch,
        MQTT_PASSWORD="mqtt-geheim-1",
        HA_TOKEN="ha-geheim-2",
        INFLUX_PASSWORD="influx-geheim-3",
    )
    out = tmp_path / "out"
    assert main(["--config", str(config), "--duration", "0.2", "--out", str(out)]) == 0
    report, raw = _outputs(out)
    assert report.startswith("# Prüfprotokoll Teil 1 – ")
    for title in (
        "Cerbo GX (MQTT)",
        "EVCS evcs",
        "Wall Connector twc",
        "Home Assistant",
        "InfluxDB",
    ):
        assert any(title in line and "| FEHLER |" in line for line in report.splitlines()), title
    assert "| 17.1-10 | Tessie tesla | INFO | Kein Token, nicht geprüft. |" in report
    json.loads(raw)
    for secret in ("mqtt-geheim-1", "ha-geheim-2", "influx-geheim-3"):
        assert secret not in report and secret not in raw


def test_http_sources_are_checked(tmp_path, monkeypatch, respx_mock) -> None:
    now = datetime.now(UTC)
    date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    respx_mock.get("http://twc.lan/api/1/vitals").respond(
        json=json.loads(
            (Path(__file__).resolve().parents[2] / "fixtures" / "twc_vitals.json").read_text()
        )
    )
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1})
    respx_mock.get("http://twc.lan/api/1/version").respond(json={"firmware_version": "25.42.1"})
    tessie = respx_mock.get(f"https://api.tessie.com/{VIN}/state").respond(json=TESSIE_STATE)
    respx_mock.get("http://homeassistant.lan:8123/api/states/sensor.egolf_soc").respond(
        json={"entity_id": "sensor.egolf_soc", "state": "55"}, headers={"Date": date}
    )

    def influx(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        if q == "SHOW DATABASES":
            series = {"columns": ["name"], "values": [["bindaems"], ["homeassistant"]]}
        elif q.startswith("SHOW RETENTION POLICIES"):
            series = {
                "columns": ["name", "duration", "shardGroupDuration", "replicaN", "default"],
                "values": [
                    ["raw", "2160h0m0s", "24h0m0s", 1, True],
                    ["long", "0s", "168h0m0s", 1, False],
                ],
            }
        else:
            series = {"columns": ["name"], "values": [["%"], ["W"]]}
        body = {"results": [{"statement_id": 0, "series": [series]}]}
        return httpx.Response(200, json=body, headers={"Date": date})

    respx_mock.get("http://influx.lan:8086/query").mock(side_effect=influx)
    _env(monkeypatch, TESSIE_TOKEN="tessie-geheim", HA_TOKEN="ha-geheim")
    out = tmp_path / "out"
    assert main(["--config", str(_config(tmp_path)), "--duration", "0.2", "--out", str(out)]) == 0
    report, raw = _outputs(out)
    for row in (
        "| 17.1-9 | Wall-Connector-Vitals | OK |",
        "| 17.1-10 | Tessie-Felder | OK |",
        "| 17.1-11 | HA sensor.egolf_soc | OK | sensor.egolf_soc = 55 |",
        "| 17.1-11 | InfluxDB-Datenbank | OK |",
        "| 17.1-11 | InfluxDB-Retention-Policies | OK |",
        "| 17.1-12 | Uhrzeit gegenüber der VM | OK |",
    ):
        assert row in report, row
    assert "| Wall-Connector-Firmware twc | 25.42.1 |" in report
    clock = next(line for line in report.splitlines() if "Uhrzeit gegenüber der VM" in line)
    assert "homeassistant:" in clock and "influxdb:" in clock
    assert tessie.calls[0].request.url.params["use_cache"] == "true"
    data = json.loads(raw)
    assert data["tessie"]["tesla"]["drive_state"]["latitude"] == "***"
    assert "tessie-geheim" not in raw and "ha-geheim" not in raw
    # nur lesende Anfragen
    assert {call.request.method for call in respx_mock.calls} == {"GET"}
    lines = report.splitlines()
    items = [line.split("|")[1].strip() for line in lines if line.startswith("| 17.1-")]
    assert items == sorted(
        items, key=lambda i: [int(p) for p in i.removeprefix("17.1-").split("-")]
    )


@pytest.mark.parametrize("argv", [["--config", "/gibt/es/nicht.yaml"]])
def test_invalid_config_returns_2(argv, capsys) -> None:
    assert main(argv) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
