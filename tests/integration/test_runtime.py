import asyncio
from datetime import timedelta

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.helpers import FakeSink
from tests.integration.conftest import TOKEN

from bindaems.core.__main__ import main
from bindaems.core.runtime import CoreRuntime
from bindaems.shared.config import Secrets


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


def test_configure_logging_writes_json_with_utc_timestamp(capsys) -> None:
    import json
    import logging

    import structlog

    from bindaems.core.logging import configure_logging

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

    from bindaems.core.logging import configure_logging

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
