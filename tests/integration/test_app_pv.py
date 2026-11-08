import asyncio
import contextlib
from datetime import date

import pytest
from structlog.testing import capture_logs
from tests.integration.test_app_runtime import PASSWORD, make_runtime
from tests.plan_helpers import until

from bindaems.app.consumers.service import ConsumerInput
from bindaems.app.pv.periods import pv_fill_days
from bindaems.app.pv.service import PvSourceInput, PvSourceService


def pv_input(ref: str = "pv.huawei.power_w", **kw) -> PvSourceInput:
    return PvSourceInput(
        **{"name": "Huawei", "color": "#f59e0b", "source_kind": "core", "power_ref": ref, **kw}
    )


def series(source: str, id_: str, watts: float) -> dict:
    return {
        "name": "power",
        "tags": {"source": source, "id": id_},
        "columns": ["time", "w"],
        "values": [[1791504000000, watts]],
    }


@pytest.mark.respx(assert_all_called=False)
async def test_pv_and_consumer_ids_do_not_mix(cfg, tmp_path, respx_mock) -> None:
    respx_mock.post("http://influx.lan:8086/query").respond(
        json={
            "results": [
                {"series": [series("load", "obergeschoss", 600.0), series("pv", "huawei", 3000.0)]}
            ]
        }
    )
    runtime = make_runtime(cfg, tmp_path)
    try:
        runtime.consumers.create(
            ConsumerInput(
                name="OG",
                color="#000000",
                source_kind="core",
                power_ref="load.obergeschoss.power_w",
            ),
            actor="t",
            source="ui",
        )
        runtime.pv_sources.create(pv_input(), actor="t", source="ui")
        await runtime.energy.refresh(runtime.consumers.list())
        await runtime.pv_energy.refresh(runtime.pv_sources.list())
        assert runtime.energy.energy_kwh(runtime.consumers.list()[0]) == 0.01
        assert runtime.pv_energy.energy_kwh(runtime.pv_sources.list()[0]) == 0.05
        from fastapi.testclient import TestClient

        client = TestClient(runtime.app, base_url="https://testserver")
        runtime.auth.create_user("viewer1", PASSWORD, "viewer", actor="cli", source="cli")
        login = client.post("/api/auth/login", json={"username": "viewer1", "password": PASSWORD})
        assert login.status_code == 200
        pv = client.get("/api/pv").json()["tree"][0]
        og = client.get("/api/consumers").json()["tree"]["children"][0]
        assert (pv["kwh_today"], og["energy_kwh"]) == (0.05, 0.01)
    finally:
        runtime.engine.dispose()


def test_fill_days_reach_january_of_last_year() -> None:
    assert pv_fill_days(date(2026, 11, 8)) == 676
    assert pv_fill_days(date(2028, 12, 31)) == 730  # nie über 800


@pytest.mark.respx(assert_all_called=False)
async def test_fill_reaches_january_of_last_year(cfg, tmp_path, respx_mock) -> None:
    respx_mock.post("http://influx.lan:8086/query").respond(json={"results": [{}]})
    runtime = make_runtime(cfg, tmp_path)
    try:
        runtime.pv_sources.create(pv_input(), actor="t", source="ui")
        today = date(2026, 11, 8)
        first = await runtime.pv_days.fill_missing(today, pv_fill_days(today))
        assert first > 600  # jeder Tag bekommt eine Zeile mit Grund
        rows = runtime.pv_days.daily(date(2025, 1, 1), date(2025, 1, 1))
        assert rows[1][date(2025, 1, 1)] == (None, "kein Verlauf seit Mitternacht")
        assert await runtime.pv_days.fill_missing(today, pv_fill_days(today)) == 0
    finally:
        runtime.engine.dispose()


@pytest.mark.respx(assert_all_called=False)
async def test_ha_without_database_shows_reason(cfg, tmp_path, respx_mock) -> None:
    respx_mock.post("http://influx.lan:8086/query").respond(
        json={"results": [{"series": [series("pv", "huawei", 3000.0)]}]}
    )
    influx = cfg.influxdb.model_copy(update={"ha_database": None})
    runtime = make_runtime(cfg.model_copy(update={"influxdb": influx}), tmp_path)
    try:
        helper = PvSourceService(runtime.engine, runtime._clock, runtime.audit, ha_enabled=True)
        helper.create(pv_input(), actor="t", source="ui")
        helper.create(
            pv_input(source_kind="ha", power_ref="sensor.pv2", power_unit="W", name="HA"),
            actor="t",
            source="ui",
        )
        await runtime.pv_energy.refresh(runtime.pv_sources.list())
        from fastapi.testclient import TestClient

        client = TestClient(runtime.app, base_url="https://testserver")
        runtime.auth.create_user("viewer1", PASSWORD, "viewer", actor="cli", source="cli")
        client.post("/api/auth/login", json={"username": "viewer1", "password": PASSWORD})
        nodes = {n["name"]: n for n in client.get("/api/pv").json()["tree"]}
        assert nodes["Huawei"]["note"] is None
        assert nodes["Huawei"]["kwh_today"] == 0.05
        assert "HA-Datenbank nicht eingerichtet" in nodes["HA"]["note"]
        assert nodes["HA"]["power_w"] is None
    finally:
        runtime.engine.dispose()


def spy_pv_fill(runtime, monkeypatch, *, fail: bool = False) -> list[tuple[date, int]]:
    calls: list[tuple[date, int]] = []

    async def fill_missing(today: date, days: int = 31) -> int:
        calls.append((today, days))
        if fail:
            raise RuntimeError("Datenbank gesperrt")
        return 4

    monkeypatch.setattr(runtime.pv_days, "fill_missing", fill_missing)
    return calls


async def test_nightly_fills_the_last_3_pv_days_but_not_at_start(cfg, tmp_path, monkeypatch):
    runtime = make_runtime(cfg, tmp_path)
    try:
        calls = spy_pv_fill(runtime, monkeypatch)

        async def nothing(*_args) -> int:
            return 0

        monkeypatch.setattr(runtime.gap_estimator, "run", nothing)
        monkeypatch.setattr(runtime.ledger_checks, "run", nothing)
        monkeypatch.setattr(runtime.consumer_days, "fill_missing", nothing)
        await runtime._ledger_nightly(7, start=True)
        assert calls == []
        await runtime._ledger_nightly(7)
        assert calls == [(date(2026, 10, 9), 3)]
    finally:
        runtime.engine.dispose()


async def test_pv_fill_task_reaches_back_and_survives_errors(cfg, tmp_path, monkeypatch):
    runtime = make_runtime(cfg, tmp_path)
    try:
        calls = spy_pv_fill(runtime, monkeypatch)
        await runtime._pv_days_fill()
        assert calls == [(date(2026, 10, 9), pv_fill_days(date(2026, 10, 9)))]
        spy_pv_fill(runtime, monkeypatch, fail=True)
        with capture_logs() as logs:
            await runtime._pv_days_fill()
        assert "Energie der PV je Tag nicht gespeichert" in [e["event"] for e in logs]
    finally:
        runtime.engine.dispose()


@pytest.mark.respx(assert_all_called=False)
async def test_pv_fill_job_fills_once_then_waits_for_a_new_entry(
    cfg, tmp_path, respx_mock, monkeypatch
) -> None:
    """Der registrierte Job endet nach dem ersten Lauf nicht (sonst startete ``_supervise`` ihn
    alle 10 s neu) und rechnet nach dem Anlegen eines Eintrags wieder, ohne Neustart."""
    respx_mock.route().respond(503)  # kein echter Netzverkehr
    monkeypatch.setattr("bindaems.app.runtime.PV_FILL_DEBOUNCE_S", 0.0, raising=False)
    runtime = make_runtime(cfg, tmp_path)
    calls = spy_pv_fill(runtime, monkeypatch)
    task = asyncio.create_task(runtime.run())
    try:
        with capture_logs() as logs:
            await until(lambda: len(calls) == 1)
            await asyncio.sleep(0.2)
        assert len(calls) == 1
        ended = [e for e in logs if e["event"] == "Hintergrundaufgabe beendet"]
        assert [e for e in ended if e.get("task") == "pv-days-fill"] == []
        await asyncio.to_thread(runtime.pv_sources.create, pv_input(), actor="t", source="ui")
        await until(lambda: len(calls) == 2)
        assert calls[1] == (date(2026, 10, 9), pv_fill_days(date(2026, 10, 9)))
    finally:
        await runtime.shutdown()
        await asyncio.wait_for(task, timeout=2)
        runtime.engine.dispose()


@pytest.mark.respx(assert_all_called=False)
async def test_entry_created_after_start_gets_its_older_days(
    cfg, tmp_path, respx_mock, monkeypatch
) -> None:
    respx_mock.post("http://influx.lan:8086/query").respond(json={"results": [{}]})
    monkeypatch.setattr("bindaems.app.runtime.PV_FILL_DEBOUNCE_S", 0.0, raising=False)
    runtime = make_runtime(cfg, tmp_path)
    task = asyncio.create_task(runtime._pv_days_fill_loop())
    try:
        await asyncio.sleep(0.05)  # erster Lauf ohne Einträge
        await asyncio.to_thread(runtime.pv_sources.create, pv_input(), actor="t", source="ui")
        first = date(2025, 1, 1)
        await until(lambda: runtime.pv_days.daily(first, first) != {}, max_wait_s=30)
        assert runtime.pv_days.daily(first, first)[1][first] == (
            None,
            "kein Verlauf seit Mitternacht",
        )
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        runtime.engine.dispose()


async def test_ha_loop_refreshes_consumers_and_pv_in_one_call(cfg, tmp_path, monkeypatch):
    runtime = make_runtime(cfg, tmp_path)
    try:
        runtime.consumers.create(
            ConsumerInput(
                name="Server",
                color="#000000",
                source_kind="ha",
                power_ref="sensor.server",
                power_unit="W",
            ),
            actor="t",
            source="ui",
        )
        runtime.pv_sources.create(
            pv_input(source_kind="ha", power_ref="sensor.pv1", power_unit="kW"),
            actor="t",
            source="ui",
        )
        seen: list[list] = []

        class Stop(Exception):
            pass

        class Cache:
            async def refresh(self, refs) -> None:
                seen.append(list(refs))
                raise Stop

        async def stop(_s: float) -> None:
            raise Stop

        monkeypatch.setattr("bindaems.app.runtime.asyncio.sleep", stop)
        with pytest.raises(Stop):
            await runtime._ha_values_loop(Cache())  # type: ignore[arg-type]
        # Fehler im Refresh werden protokolliert; danach schläft die Schleife (Stop)
        assert [(r.domain, r.object_id) for r in seen[0]] == [
            ("sensor", "server"),
            ("sensor", "pv1"),
        ]
    finally:
        runtime.engine.dispose()
