import json
from pathlib import Path

import httpx
import pytest
from tests.helpers import run_until

from bindaems.core.adapters.tessie import haversine_m, parse_state, poll_interval_s
from bindaems.shared.config import LatLon
from bindaems.shared.domain import Quality

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
HOME = LatLon(lat=48.2, lon=16.37)
STATE = json.loads((FIXTURES / "tessie_state.json").read_text())
VIN = "5YJ3E7EB0MF000000"
URL = f"https://api.tessie.com/{VIN}/state"


def test_haversine_one_tenth_degree_latitude() -> None:
    assert haversine_m(HOME, LatLon(lat=48.3, lon=16.37)) == pytest.approx(11_119.5, abs=1.0)


def test_parse_state_values() -> None:
    d = parse_state(STATE, HOME, 150)
    assert d["soc_pct"] == 63 and d["charge_limit_pct"] == 80 and d["phases"] == 3
    assert d["plugged"] is True and d["scheduled_charging"] is False
    assert d["at_home"] is True and d["online"] is True


def test_parse_state_disconnected_and_away() -> None:
    data = json.loads(json.dumps(STATE))
    data["charge_state"]["charging_state"] = "Disconnected"
    data["drive_state"]["latitude"] = 48.3  # ≈ 11 119 m entfernt
    d = parse_state(data, HOME, 150)
    assert d["plugged"] is False and d["at_home"] is False


def test_parse_state_missing_location() -> None:
    data = {k: v for k, v in STATE.items() if k != "drive_state"}
    assert parse_state(data, HOME, 150)["at_home"] is None


def test_poll_interval() -> None:
    assert poll_interval_s("Charging", True) == 30.0
    assert poll_interval_s("Stopped", True) == 60.0
    assert poll_interval_s("Disconnected", False) == 600.0
    assert poll_interval_s(None, None) == 600.0


async def test_adapter_only_requests_state_endpoint(respx_mock, tessie_env) -> None:
    route = respx_mock.get(URL).respond(json=STATE)
    adapter, store = tessie_env()
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.tesla.soc_pct") == 63)
    req = route.calls[0].request
    assert req.url.params["use_cache"] == "true"
    assert req.headers["Authorization"] == "Bearer tok"
    assert len(respx_mock.calls) == route.call_count  # keine anderen Endpunkte


async def test_poll_interval_follows_charging_state(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).respond(json=STATE)
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: bool(adapter.sleeps))
    assert adapter.sleeps[0] == 30.0  # lädt gerade


async def test_server_error_backs_off_at_least_60s(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).respond(503)
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: bool(adapter.sleeps))
    assert adapter.sleeps[0] >= 60


@pytest.mark.parametrize(
    "outcome", [httpx.Response(429), httpx.ConnectTimeout("Zeitüberschreitung")]
)
async def test_rate_limit_and_timeout_back_off_at_least_60s(
    respx_mock, tessie_env, outcome
) -> None:
    respx_mock.get(URL).mock(side_effect=[outcome])
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: bool(adapter.sleeps))
    assert adapter.sleeps[0] >= 60


async def test_invalid_token_waits_900s(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).respond(401)
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: bool(adapter.sleeps))
    assert adapter.sleeps[0] == 900.0
    assert adapter.health().last_error == "Tessie-Token ungültig"


async def test_transient_errors_keep_values_valid(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).mock(
        side_effect=[httpx.Response(200, json=STATE)] + [httpx.Response(503)] * 50
    )
    adapter, store = tessie_env()
    await run_until(
        adapter,
        lambda: (
            adapter.health().error_count >= 3 and store.snapshot().ok("vehicle.tesla.soc_pct") == 63
        ),
    )


async def test_values_go_stale_after_900s(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).mock(
        side_effect=[httpx.Response(200, json=STATE)] + [httpx.Response(503)] * 50
    )
    adapter, store, clock = tessie_env(with_clock=True)

    def expires_after_900s() -> bool:  # geprüft, solange der Adapter läuft
        if store.snapshot().ok("vehicle.tesla.soc_pct") != 63:
            return False
        clock.advance(899)
        still_valid = store.snapshot().ok("vehicle.tesla.soc_pct") == 63
        clock.advance(2)
        reading = store.snapshot().get("vehicle.tesla.soc_pct")
        return still_valid and reading.quality is Quality.STALE

    await run_until(adapter, expires_after_900s)


async def test_cancel_marks_source_disconnected(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).respond(json=STATE)
    adapter, store = tessie_env()
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.tesla.soc_pct") == 63)
    assert store.snapshot().get("vehicle.tesla.soc_pct").quality is Quality.STALE


async def test_backoff_resets_after_success(respx_mock, tessie_env) -> None:
    respx_mock.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=STATE), httpx.Response(503)]
    )
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: len(adapter.sleeps) >= 3)
    assert adapter.sleeps[:3] == [60.0, 30.0, 60.0]


async def test_invalid_token_is_logged(respx_mock, tessie_env) -> None:
    from structlog.testing import capture_logs

    respx_mock.get(URL).respond(401)
    adapter, _ = tessie_env()
    with capture_logs() as logs:
        await run_until(adapter, lambda: bool(adapter.sleeps))
    assert logs[0]["error"] == "Tessie-Token ungültig" and logs[0]["retry_in_s"] == 900.0
