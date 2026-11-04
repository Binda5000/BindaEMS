"""Auffälligkeiten in der app-Laufzeit (Betrieb im Blick, Task 4)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs
from tests.app_helpers import PASSWORD, StopScheduler, ingest, record_then_stop
from tests.integration.test_app_runtime import make_runtime
from tests.plan_helpers import at, sample_inputs, sample_plan

from bindaems.shared.config import Config


@pytest.fixture
def runtime(cfg: Config, tmp_path):
    rt = make_runtime(cfg, tmp_path)
    yield rt
    rt.engine.dispose()


async def test_findings_after_unplanned_grid_charge(runtime, respx_mock) -> None:
    # core und InfluxDB antworten nicht: der SOC bleibt ungeprüft, das Netzladen fällt trotzdem auf
    respx_mock.route().respond(503)
    for clock in ("19:30", "19:45"):  # 21:30 und 21:45 Ortszeit am 8. 10.
        ingest(runtime.ledger, at(f"2026-10-08T{clock}:00+00:00"), {"grid>battery": 2200.0})
    plan = sample_plan("2026-10-08T05:00:00+00:00", ["19:30", "19:45"])
    runtime.plan_store.save(
        plan.model_copy(update={"valid_until": at("2026-10-09T05:00:00+00:00")}),
        sample_inputs(),
    )
    await runtime.findings.run_day(date(2026, 10, 8))

    client = TestClient(runtime.app, base_url="https://testserver")
    runtime.auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    body = client.get("/api/ops/findings?days=2").json()
    [yesterday, before] = body["days"]
    assert (yesterday["day"], yesterday["computed"]) == ("2026-10-08", True)
    assert (before["day"], before["computed"]) == ("2026-10-07", False)
    grid = next(f for f in yesterday["findings"] if f["rule"] == "grid_charge_unplanned")
    assert grid["start"] == "2026-10-08T19:30:00+00:00"
    assert grid["end"] == "2026-10-08T20:00:00+00:00"
    assert grid["text"].startswith("Akku lud 21:30–22:00 mit bis zu 8,8 kW aus dem Netz")
    assert "not_checked" in [f["rule"] for f in yesterday["findings"]]
    assert body["report"] is None


async def test_maintenance_cleans_up_old_findings(runtime, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(runtime.findings, "cleanup", lambda: calls.append(1) or 4)
    monkeypatch.setattr(runtime.plan_store, "cleanup", lambda now: (0, 0))
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=0))
    with capture_logs() as logs, pytest.raises(StopScheduler):
        await runtime._maintenance_loop()
    assert calls == [1]
    entry = next(e for e in logs if e["event"] == "Alte Auffälligkeiten gelöscht")
    assert entry["findings"] == 4


async def test_ops_nightly_waits_for_the_first_backfill(runtime, monkeypatch) -> None:
    started = asyncio.Event()

    async def run() -> None:
        started.set()

    monkeypatch.setattr(runtime.findings, "run", run)
    task = asyncio.create_task(runtime._ops_nightly_loop())
    await asyncio.sleep(0.05)
    assert not started.is_set()  # der Nachtrag der Abrechnung fehlt noch
    runtime._ledger_backfilled.set()
    await asyncio.wait_for(task, timeout=1)
    assert started.is_set()
