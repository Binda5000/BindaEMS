import json
from pathlib import Path

import pytest
from tests.helpers import run_until

from bindaems.core.adapters.twc import parse_lifetime, parse_vitals
from bindaems.shared.domain import Quality

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
VITALS = json.loads((FIXTURES / "twc_vitals.json").read_text())
LIFETIME = json.loads((FIXTURES / "twc_lifetime.json").read_text())


def test_parse_vitals_power() -> None:
    assert parse_vitals(VITALS, ("L1", "L2", "L3"))["power_w"][0] == pytest.approx(11040.2)


def test_parse_vitals_maps_terminals_to_grid_phases() -> None:
    d = parse_vitals(VITALS, ("L2", "L3", "L1"))
    assert d["l2.current_a"][0] == 16.0
    assert d["l3.current_a"][0] == 16.1
    assert d["l1.current_a"][0] == 15.9


def test_parse_vitals_missing_field_invalidates_power() -> None:
    data = {k: v for k, v in VITALS.items() if k != "currentB_a"}
    d = parse_vitals(data, ("L1", "L2", "L3"))
    assert d["power_w"][0] is None
    assert d["l2.current_a"][0] is None


def test_parse_lifetime() -> None:
    assert parse_lifetime(LIFETIME)["total_kwh"][0] == pytest.approx(1234.567)


async def test_adapter_polls_and_updates_store(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1000})
    respx_mock.get("http://twc.lan/api/1/version").respond(json={"firmware_version": "25.42.1"})
    adapter, store = twc_env()
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.twc.power_w") is not None)


@pytest.mark.respx(assert_all_called=False)
async def test_http_error_marks_disconnected(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    # Fehlerantwort mit JSON-Body: darf nicht als Messdaten durchgehen
    respx_mock.get("http://twc.lan/api/1/vitals").respond(500, json={"error": "intern"})
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json=LIFETIME)
    adapter, _ = twc_env()
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert adapter.sleeps[0] == 1.0


async def test_only_get_requests(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1})
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    adapter, _ = twc_env()
    await run_until(adapter, lambda: len(respx_mock.calls) >= 3)
    assert all(call.request.method == "GET" for call in respx_mock.calls)


async def test_lifetime_polled_less_often_and_version_once(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json=LIFETIME)
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    adapter, _ = twc_env(vitals_s=2.0, lifetime_s=4.0)
    await run_until(adapter, lambda: len(respx_mock.calls) >= 6)
    paths = [call.request.url.path.rsplit("/", 1)[-1] for call in respx_mock.calls]
    assert paths[:6] == ["version", "vitals", "lifetime", "vitals", "vitals", "lifetime"]
    assert paths.count("version") == 1


async def test_version_read_once_across_reconnects(respx_mock, twc_env) -> None:
    version = respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    respx_mock.get("http://twc.lan/api/1/vitals").respond(500)
    adapter, _ = twc_env()
    await run_until(adapter, lambda: adapter.health().error_count >= 3)
    assert version.call_count == 1


async def test_cancel_marks_source_disconnected(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json=LIFETIME)
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    adapter, store = twc_env()
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.twc.contactor_closed") is True)
    assert store.snapshot().get("wallbox.twc.contactor_closed").quality is Quality.STALE


async def test_missing_version_endpoint_does_not_block_vitals(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/version").respond(404)
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json=LIFETIME)
    adapter, store = twc_env()
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.twc.power_w") is not None)
