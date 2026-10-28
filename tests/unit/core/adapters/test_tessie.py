import asyncio
import contextlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from tests.helpers import T0, run_until

from bindaems.core.adapters.tessie import (
    MIN_GAP_S,
    TessieAdapter,
    haversine_m,
    parse_state,
    poll_interval_s,
)
from bindaems.core.state.store import StateStore
from bindaems.shared.config import LatLon
from bindaems.shared.domain import Quality
from bindaems.shared.timeutil import ManualClock

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


def test_parse_state_climate_on_from_climate_or_preconditioning() -> None:
    for climate, pre, expected in [(True, False, True), (False, True, True), (False, False, False)]:
        data = {**STATE, "climate_state": {"is_climate_on": climate, "is_preconditioning": pre}}
        assert parse_state(data, HOME, 150.0)["climate_on"] is expected


def test_parse_state_climate_unknown_without_climate_state() -> None:
    data = {k: v for k, v in STATE.items() if k != "climate_state"}
    assert parse_state(data, HOME, 150.0)["climate_on"] is None
    data = {**STATE, "climate_state": {"is_climate_on": "yes"}}
    assert parse_state(data, HOME, 150.0)["climate_on"] is None


@pytest.mark.parametrize(
    ("state", "plugged", "home", "expected"),
    [
        ("Charging", True, True, 30.0),
        ("Stopped", True, False, 60.0),
        ("Disconnected", False, True, 60.0),
        ("Disconnected", False, False, 600.0),
        ("Disconnected", False, None, 600.0),
        (None, None, None, 600.0),
    ],
)
def test_poll_interval(state, plugged, home, expected) -> None:
    assert poll_interval_s(state, plugged, home) == expected


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


class _Clock:
    """Die Uhr des Adapters (``monotonic``), die nur der Test vorstellt."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _until(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("Bedingung nicht erfüllt")


@contextlib.asynccontextmanager
async def _running(cfg, sleep, clock):
    """Ein laufender Adapter mit der Uhr ``clock`` und dem Ersatz ``sleep``."""
    async with httpx.AsyncClient() as client:
        adapter = TessieAdapter(
            "tesla",
            cfg.vehicles["tesla"],
            cfg.site.location,
            SecretStr("tok"),
            StateStore(ManualClock(T0)),
            client,
            sleep=sleep,
            monotonic=clock,
        )
        task = asyncio.create_task(adapter.run())
        try:
            yield adapter
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_poll_soon_ends_the_wait_after_the_minimum_gap(respx_mock, cfg) -> None:
    # Auto weg (600 s Wartezeit): poll_soon beendet sie, die nächste Abfrage folgt erst MIN_GAP_S
    # nach der letzten, nicht nach 600 s
    away = json.loads(json.dumps(STATE))
    away["charge_state"]["charging_state"] = "Disconnected"
    away["drive_state"]["latitude"] = 48.3
    route = respx_mock.get(URL).respond(json=away)
    clock = _Clock()
    sleeps: list[float] = []
    release = asyncio.Event()

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        # die reguläre Wartezeit endet nur durch poll_soon, die Lücke durch den Test
        await (release.wait() if delay <= MIN_GAP_S else asyncio.Event().wait())

    async with _running(cfg, sleep, clock) as adapter:
        await _until(lambda: sleeps == [600.0])
        assert route.call_count == 1
        clock.t += 4.0  # poll_soon 4 s nach der letzten Abfrage
        adapter.poll_soon()
        await _until(lambda: len(sleeps) == 2)
        assert sleeps[1] == pytest.approx(MIN_GAP_S - 4.0)
        assert route.call_count == 1  # die Lücke läuft noch
        release.set()
        await _until(lambda: route.call_count == 2)
        await _until(lambda: sleeps[2:] == [600.0])  # danach wieder die reguläre Wartezeit


async def test_poll_soon_after_the_gap_polls_without_further_wait(respx_mock, cfg) -> None:
    route = respx_mock.get(URL).respond(json=STATE)
    clock = _Clock()
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        await asyncio.Event().wait()

    async with _running(cfg, sleep, clock) as adapter:
        await _until(lambda: sleeps == [30.0])
        clock.t += 12.0  # die Mindestzeit ist schon um
        adapter.poll_soon()
        await _until(lambda: route.call_count == 2)
        assert sleeps[:2] == [30.0, 30.0]  # keine zusätzliche Lücke
