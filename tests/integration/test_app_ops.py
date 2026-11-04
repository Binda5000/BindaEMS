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
    assert body["report"]["day"] == "2026-10-08"
    assert body["report"]["text"].startswith("Donnerstag 8.10.: Netzbezug 4,4 kWh (geplant 0,0)")
    assert "Betriebscheck jetzt:" in body["report"]["text"]


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


def spy_findings_start(runtime, monkeypatch) -> list[str]:
    """Ersetzt Nachholen und Nachtlauf der Auffälligkeiten; zeichnet die Aufrufe auf."""
    calls: list[str] = []

    async def catch_up() -> None:
        calls.append("catch_up")

    async def nightly() -> None:
        calls.append("nightly")

    monkeypatch.setattr(runtime.findings, "catch_up", catch_up)
    monkeypatch.setattr(runtime.findings, "nightly", nightly)
    return calls


async def test_ops_nightly_waits_for_the_first_backfill(runtime, monkeypatch) -> None:
    calls = spy_findings_start(runtime, monkeypatch)
    runtime._ledger_checked.set()
    task = asyncio.create_task(runtime._ops_nightly_loop())
    await asyncio.sleep(0.05)
    assert calls == []  # der Nachtrag der Abrechnung fehlt noch
    runtime._ledger_backfilled.set()
    await asyncio.wait_for(task, timeout=1)
    assert calls == ["catch_up", "nightly"]


async def test_findings_catch_up_waits_for_the_first_ledger_nightly_pass(
    runtime, monkeypatch
) -> None:
    # sonst rechnete das Nachholen vor Nachrechnung der Lücken und Zählerabgleich
    calls = spy_findings_start(runtime, monkeypatch)
    runtime._ledger_backfilled.set()
    task = asyncio.create_task(runtime._ops_nightly_loop())
    await asyncio.sleep(0.05)
    assert calls == []
    assert not runtime._findings_caught_up.is_set()
    runtime._ledger_checked.set()
    await asyncio.wait_for(task, timeout=1)
    assert calls == ["catch_up", "nightly"]
    assert runtime._findings_caught_up.is_set()


async def test_first_ledger_nightly_pass_releases_the_findings(runtime, monkeypatch) -> None:
    passes: list[bool] = []

    async def nightly(days: int, *, start: bool = False) -> None:
        assert not runtime._ledger_checked.is_set()
        passes.append(start)

    monkeypatch.setattr(runtime, "_ledger_nightly", nightly)
    runtime._ledger_backfilled.set()
    monkeypatch.setattr(asyncio, "sleep", record_then_stop([], after=1))
    with pytest.raises(StopScheduler):
        await runtime._ledger_nightly_loop()
    assert passes == [True]
    assert runtime._ledger_checked.is_set()


async def test_failed_findings_catch_up_still_releases_the_report(runtime, monkeypatch) -> None:
    async def catch_up() -> None:
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(runtime.findings, "catch_up", catch_up)
    runtime._ledger_backfilled.set()
    runtime._ledger_checked.set()
    with pytest.raises(RuntimeError):
        await runtime._ops_nightly_loop()
    assert runtime._findings_caught_up.is_set()


async def test_ops_report_waits_for_the_start_order(runtime, monkeypatch) -> None:
    # Nachtrag → Nachrechnung und Zählerabgleich → Nachholen der Auffälligkeiten → Tagesbericht:
    # sonst rechnete der Bericht gestern aus einer unvollständigen Abrechnung (und legte es ab)
    started = asyncio.Event()

    async def run() -> None:
        started.set()

    monkeypatch.setattr(runtime.report_sender, "run", run)
    task = asyncio.create_task(runtime._ops_report_loop())
    for event in (runtime._ledger_backfilled, runtime._ledger_checked):
        await asyncio.sleep(0.05)
        assert not started.is_set()
        event.set()
    await asyncio.sleep(0.05)
    assert not started.is_set()  # die Auffälligkeiten sind noch nicht nachgeholt
    runtime._findings_caught_up.set()
    await asyncio.wait_for(task, timeout=1)
    assert started.is_set()


async def test_ops_report_does_not_wait_forever(runtime, monkeypatch) -> None:
    started = asyncio.Event()

    async def run() -> None:
        started.set()

    monkeypatch.setattr(runtime.report_sender, "run", run)
    monkeypatch.setattr("bindaems.app.runtime.LEDGER_BACKFILL_WAIT_S", 0.02)
    with capture_logs() as logs:
        await asyncio.wait_for(runtime._ops_report_loop(), timeout=1)
    assert started.is_set()
    assert "Start des Tagesberichts wartet zu lange, er beginnt ohne Vorlauf" in [
        e["event"] for e in logs
    ]
