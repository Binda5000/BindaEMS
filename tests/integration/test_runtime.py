import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.helpers import INTENT_CHARGING_DEFAULTS, FakeSink
from tests.integration.conftest import TOKEN

from bindaems.core.__main__ import _run, main
from bindaems.core.api.app import create_api
from bindaems.core.control.operating import OperatingStateMachine
from bindaems.core.persistence.files import read_json, write_json_atomic
from bindaems.core.runtime import CoreRuntime
from bindaems.shared.config import Secrets
from bindaems.shared.control import Intent


async def test_cycle_produces_state_and_points(runtime_env) -> None:
    rt, sink, clock = runtime_env()
    for _ in range(3):
        rt.cycle_once()
        clock.advance(1)
    assert rt.state()["signals"]["grid.l1.power_w"]["v"] == 1000.0
    assert any(p.measurement == "power" for p in sink.points)
    assert rt.health().mode == "OBSERVE"


async def test_slot_close_writes_flows(runtime_env) -> None:
    rt, sink, clock = runtime_env(start="2026-10-08T09:14:58+00:00")
    for _ in range(4):
        rt.cycle_once()
        clock.advance(1)
    flows = {p.tags["flow"]: p.fields for p in sink.points if p.measurement == "flows"}
    assert flows["_coverage"] == {"covered_s": 2.0}
    assert "grid>house" in flows


async def test_selfcheck_runs_every_10s(runtime_env) -> None:
    rt, _, clock = runtime_env()
    rt.cycle_once()
    assert rt.health().selfcheck
    assert rt.selfcheck_runs == 1
    for _ in range(10):
        clock.advance(1)
        rt.cycle_once()
    assert rt.selfcheck_runs == 2


async def _drain(it) -> list[dict]:
    messages = []
    while True:
        try:
            messages.append(await asyncio.wait_for(anext(it), 0.05))
        except TimeoutError:
            return messages


async def test_alarms_broadcast_on_change_only(runtime_env) -> None:
    rt, _, clock = runtime_env(state={"dess.mode": 1})
    async with rt.subscribe() as it:
        for _ in range(3):
            rt.cycle_once()
            clock.advance(1)
        messages = await _drain(it)
    alarms = [m for m in messages if m["type"] == "alarm"]
    assert len(alarms) == 1
    assert [a["id"] for a in alarms[0]["data"]] == ["competitor.dess"]
    assert sum(1 for m in messages if m["type"] == "state") == 3
    assert rt.health().alarms[0]["id"] == "competitor.dess"


class SlowTimer:
    """Jeder Zyklus dauert 250 ms."""

    def __init__(self) -> None:
        self.t = 0.0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.calls % 2 == 0:
            self.t += 0.25
        return self.t


async def test_three_slow_cycles_raise_overrun_alarm(runtime_env) -> None:
    rt, _, clock = runtime_env(timer=SlowTimer())
    for i in range(3):
        rt.cycle_once()
        clock.advance(1)
        ids = [a["id"] for a in rt.health().alarms]
        assert ("core.cycle_overrun" in ids) == (i == 2)
    overrun = next(a for a in rt.health().alarms if a["id"] == "core.cycle_overrun")
    assert overrun["message"] == "Regelzyklus überschreitet 200 ms."
    assert overrun["severity"] == "warning"
    assert rt.health().cycle_ms_p95 == 250.0


async def test_shutdown_writes_running_slot(runtime_env) -> None:
    rt, sink, clock = runtime_env()
    for _ in range(3):
        rt.cycle_once()
        clock.advance(1)
    await rt.shutdown()
    coverage = [p for p in sink.points if p.tags.get("flow") == "_coverage"]
    assert [p.fields["covered_s"] for p in coverage] == [2.0]


async def test_run_cycles_until_shutdown(runtime_env) -> None:
    rt, sink, _ = runtime_env(cycle_s=0.01)
    task = asyncio.create_task(rt.run())
    async with asyncio.timeout(2):
        while not any(p.measurement == "power" for p in sink.points):  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    await rt.shutdown()
    async with asyncio.timeout(2):
        await asyncio.gather(task, return_exceptions=True)
    assert task.done()


def test_build_adapters_from_config(cfg) -> None:
    secrets = Secrets(
        internal_token=SecretStr(TOKEN),
        tessie_token=SecretStr("t"),
        ha_token=SecretStr("h"),
    )
    rt = CoreRuntime(cfg, secrets, sink=FakeSink(), serve_api=False)
    assert [a.name for a in rt.adapters] == ["victron", "evcs", "twc", "tesla", "ha"]


def test_missing_tessie_token_skips_vehicle_with_warning(cfg) -> None:
    with capture_logs() as logs:
        rt = CoreRuntime(
            cfg, Secrets(internal_token=SecretStr(TOKEN)), sink=FakeSink(), serve_api=False
        )
    assert [a.name for a in rt.adapters] == ["victron", "evcs", "twc"]
    assert any(
        e["event"] == "Tessie-Token fehlt – Fahrzeug Tesla Model 3 wird nicht gelesen" for e in logs
    )


def test_main_returns_2_on_invalid_config(tmp_path, capsys) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("grid: {fuse_a: -1}\n")
    assert main(["--config", str(p)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err


@pytest.mark.parametrize("token", ["zu-kurz", None])
def test_main_returns_2_on_invalid_secrets_without_leaking(capsys, monkeypatch, token) -> None:
    if token is None:
        monkeypatch.delenv("BINDAEMS_INTERNAL_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", token)
    monkeypatch.setenv("BINDAEMS_MQTT_PASSWORD", "streng-geheim-123")
    assert main(["--config", "deploy/config.example.yaml"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Secrets ungültig")
    assert "internal_token" in err
    assert "streng-geheim-123" not in err
    assert token is None or token not in err


async def test_start_message_reports_the_start_state(cfg, monkeypatch) -> None:
    # Der core beginnt in START und schaltet selbst weiter (Spec 7.2): Die Startmeldung nennt den
    # Zustand der Laufzeit, nicht fest OBSERVE.
    class EndingRuntime:
        def __init__(self, cfg, secrets) -> None:
            self.operating = OperatingStateMachine()

        async def run(self) -> None:
            raise RuntimeError("beendet sich von selbst")

        async def shutdown(self) -> None:
            pass

    monkeypatch.setattr("bindaems.core.__main__.CoreRuntime", EndingRuntime)
    with capture_logs() as logs:
        code = await _run(cfg, Secrets(internal_token=SecretStr(TOKEN)))
    assert code == 1  # die Laufzeit endete unerwartet
    started = [entry for entry in logs if entry["event"] == "core gestartet"]
    assert [entry["mode"] for entry in started] == ["START"]


def test_configure_logging_writes_json_with_utc_timestamp(capsys) -> None:
    import json
    import logging

    import structlog

    from bindaems.shared.logging import configure_logging

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        structlog.get_logger("test").info("hallo", wert=1)
        structlog.get_logger("test").debug("unterdrückt")
        logging.getLogger("fremd").warning("aus stdlib")
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finally:
        structlog.reset_defaults()
        root.handlers[:] = handlers
        root.setLevel(level)
    assert [line["event"] for line in lines] == ["hallo", "aus stdlib"]
    assert lines[0]["level"] == "info" and lines[0]["wert"] == 1
    assert lines[0]["timestamp"].endswith("Z")
    assert lines[1]["level"] == "warning"


def test_slot_counters_include_energy_meters(runtime_env) -> None:
    rt, _, _ = runtime_env()
    assert {"grid.energy_import_kwh", "grid.energy_export_kwh", "pv.huawei.energy_kwh"} <= set(
        rt.counter_signals
    )
    assert timedelta(seconds=1) == timedelta(seconds=rt.cycle_s)


async def test_unknown_balance_is_not_counted_as_covered(runtime_env) -> None:
    vebus = [f"vebus.l{n}.{d}_power_w" for n in (1, 2, 3) for d in ("ac_in", "ac_out")]
    rt, sink, clock = runtime_env(start="2026-10-08T09:14:58+00:00", omit=vebus)
    for _ in range(4):
        rt.cycle_once()
        clock.advance(1)
    coverage = [p.fields for p in sink.points if p.tags.get("flow") == "_coverage"]
    assert coverage == [{"covered_s": 0.0}]


async def test_cycle_errors_do_not_stop_the_loop(runtime_env) -> None:
    rt, sink, _ = runtime_env(cycle_s=0.01, fail_first=True)
    task = asyncio.create_task(rt.run())
    with capture_logs() as logs:
        async with asyncio.timeout(2):
            while not any(p.measurement == "power" for p in sink.points):  # noqa: ASYNC110
                await asyncio.sleep(0.01)
    await rt.shutdown()
    await asyncio.gather(task, return_exceptions=True)
    assert any(e["event"] == "Zyklus fehlgeschlagen" for e in logs)


def test_configure_logging_quiets_request_logs(capsys) -> None:
    import logging

    import structlog

    from bindaems.shared.logging import configure_logging

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        logging.getLogger("httpx").info("HTTP Request: POST http://influx.lan:8086/write")
        logging.getLogger("uvicorn.error").info("WebSocket /v1/stream [accepted]")
        logging.getLogger("httpx").warning("echte Warnung")
        out = capsys.readouterr().out
    finally:
        structlog.reset_defaults()
        root.handlers[:] = handlers
        root.setLevel(level)
    assert "HTTP Request" not in out and "accepted" not in out
    assert "echte Warnung" in out


async def test_frozen_grid_counter_raises_alarm_after_full_slot(runtime_env) -> None:
    # Netz 3000 W Bezug, der Zähler steht still: nach einer vollen Viertelstunde fällt es auf
    frozen = {"grid.energy_import_kwh": 1000.0, "grid.energy_export_kwh": 500.0}
    rt, _, clock = runtime_env(start="2026-10-08T09:59:55+00:00", extra=frozen)
    for _ in range(182):  # bis 10:15:05, Schritt 5 s (= größte integrierte Lücke)
        rt.cycle_once()
        clock.advance(5)
    alarms = {a["id"]: a for a in rt.health().alarms}
    assert "plaus.grid_counter" in alarms
    assert (
        "Bezug 750 Wh aus der Leistung, 0 Wh laut Zähler" in alarms["plaus.grid_counter"]["message"]
    )


# Absicht: Die Laufzeit hält sie im IntentStore und legt sie unter core.data_dir ab.


def test_intent_over_the_api_is_clamped_kept_and_saved_under_the_data_dir(
    runtime_env, tmp_path
) -> None:
    rt, _, clock = runtime_env()
    client = TestClient(create_api(rt, SecretStr(TOKEN)))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/v1/intent", headers=auth).json() == {"intent": None}
    # die Beispielkonfiguration gibt keinen Aktor für den Live-Betrieb frei
    body = {"version": 3, "control": True, "actuators": {"wallbox.evcs": "live"}}
    reply = client.put("/v1/intent", json=body, headers=auth)
    assert (reply.status_code, reply.json()) == (
        200,
        {
            "result": "clamped",
            "version": 3,
            "details": [
                "EVCS „evcs“: in config.yaml nicht für den Live-Betrieb freigegeben; "
                "bleibt im Dry-Run."
            ],
        },
    )
    clamped = {"version": 3, "control": True, "actuators": {"wallbox.evcs": "dry_run"}}
    assert rt.intent() == Intent.model_validate(clamped)
    full = clamped | INTENT_CHARGING_DEFAULTS  # die Antwort und die Datei nennen auch den Standard
    assert client.get("/v1/intent", headers=auth).json() == {"intent": full}
    assert read_json(tmp_path / "core" / "intent.json") == {
        "intent": full,
        "accepted_at": clock.now().isoformat(),
    }


def test_intent_store_knows_the_vehicles_and_wallboxes_of_the_config(runtime_env) -> None:
    rt, _, _ = runtime_env()
    client = TestClient(create_api(rt, SecretStr(TOKEN)))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    unknown = {"version": 1, "vehicles": {"golf": {}, "tesla": {"wallbox": "garage"}}}
    reply = client.put("/v1/intent", json=unknown, headers=auth)
    assert (reply.status_code, reply.json()) == (
        422,
        {
            "result": "rejected",
            "version": None,
            "details": ["Unbekanntes Fahrzeug „golf“.", "Unbekannte Wallbox „garage“ für „tesla“."],
        },
    )
    # Tesla und e-Golf sowie die Wallboxen evcs und twc stehen in der Beispielkonfiguration; die
    # Hinweise zur Klemmung folgen der Reihenfolge der Fahrzeuge dort, nicht der des Aufrufs
    far = "2026-10-09T10:00:00Z"  # 24 h nach dem Start der Uhr
    known = {
        "version": 1,
        "vehicles": {
            "egolf": {"boost_until": far, "wallbox": "twc"},
            "tesla": {"boost_until": far},
        },
        "priority": ["egolf", "tesla"],
    }
    reply = client.put("/v1/intent", json=known, headers=auth)
    assert (reply.status_code, reply.json()) == (
        200,
        {
            "result": "clamped",
            "version": 1,
            "details": [
                "Tesla Model 3: Boost auf 12 h begrenzt, bis 00:00.",
                "e-Golf: Boost auf 12 h begrenzt, bis 00:00.",
            ],
        },
    )


def test_saved_intent_is_loaded_when_the_runtime_starts(runtime_env, tmp_path) -> None:
    stored = {"version": 4, "control": True, "actuators": {"wallbox.evcs": "dry_run"}}
    write_json_atomic(
        tmp_path / "core" / "intent.json",
        {"intent": stored, "accepted_at": "2026-10-08T09:00:00+00:00"},
    )
    rt, _, _ = runtime_env()
    assert rt.intent() == Intent.model_validate(stored)
    assert rt.intents.load_problem is None


def test_unreadable_intent_file_starts_without_intent(runtime_env, tmp_path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "intent.json").write_text("{kaputt")
    rt, _, _ = runtime_env()
    assert rt.intent() is None
    assert rt.intents.load_problem is not None
