"""Lebenszeichen an den externen Dienst (Plan „Lebenszeichen und USV-Reichweite“, Aufgabe 4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import structlog
from pydantic import SecretStr

from bindaems.app.monitoring.heartbeat import Heartbeat, PingInfo, Reading, Verdict, verdict
from bindaems.app.monitoring.outages import Outage
from bindaems.shared.timeutil import ManualClock

NBSP = chr(0xA0)
# 14:05 lokal (MEZ = UTC+1)
NOW = datetime(2026, 11, 4, 13, 5, tzinfo=UTC)
SINCE = datetime(2026, 11, 4, 13, 3, tzinfo=UTC)  # 14:03 lokal


def _verdict(**over: object) -> Verdict:
    args: dict[str, object] = {
        "connected": True,
        "down_since": None,
        "now": NOW,
        "mode": "CONTROL",
        "reasons": [],
        "outage": None,
        "soc_pct": 55.0,
        "range_now": 31.2,
    }
    args.update(over)
    return verdict(**args)  # type: ignore[arg-type]


def _outage() -> Outage:
    return Outage(
        id=1,
        start=SINCE,
        end=None,
        end_estimated=False,
        soc_start=70.0,
        soc_end=None,
        soc_min=None,
        load_wh=0.0,
        covered_s=0.0,
    )


def test_ok_text() -> None:
    assert _verdict() == Verdict(True, f"in Ordnung · CONTROL · SOC 55 % · USV ≈ 31{NBSP}h")


def test_core_down_grace() -> None:
    assert _verdict(connected=False, down_since=NOW - timedelta(seconds=119)).ok is True
    v = _verdict(connected=False, down_since=NOW - timedelta(seconds=120))
    assert v.ok is False
    assert v.text.startswith("core nicht erreichbar seit 14:03")


def test_core_down_text_uses_down_since() -> None:
    v = _verdict(connected=False, down_since=SINCE)
    assert v.text.startswith("core nicht erreichbar seit 14:03")


def test_outage_fails() -> None:
    expected = Verdict(False, f"Netzausfall seit 14:03 · SOC 62 % · reicht ≈ 18{NBSP}h ohne PV")
    assert _verdict(outage=_outage(), soc_pct=62.0, range_now=18.0) == expected
    # im echten Ausfall steht der core im sicheren Zustand: der Netzausfall-Text gewinnt
    safe = _verdict(
        outage=_outage(),
        soc_pct=62.0,
        range_now=18.0,
        mode="SAFE",
        reasons=["Netzausfall (Inselbetrieb)."],
    )
    assert safe == expected


def test_safe_fails() -> None:
    v = _verdict(mode="SAFE", reasons=["Cerbo-Daten veraltet."])
    assert v == Verdict(False, "Sicherer Zustand: Cerbo-Daten veraltet.")


def test_observe_is_ok() -> None:
    assert _verdict(mode="OBSERVE").ok is True


def _reading(**over: object) -> Reading:
    args: dict[str, object] = {
        "connected": True,
        "mode": "CONTROL",
        "reasons": [],
        "outage": None,
        "soc_pct": 55.0,
        "range_now": 31.2,
    }
    args.update(over)
    return Reading(**args)  # type: ignore[arg-type]


def _hb(
    handler: httpx.MockTransport | None,
    reading: Reading | None = None,
    url: str | None = "https://hc-ping.com/abc",
    clock: ManualClock | None = None,
    sleep: object = None,
) -> tuple[Heartbeat, httpx.AsyncClient, ManualClock]:
    clock = clock or ManualClock(NOW)
    http = httpx.AsyncClient(transport=handler)
    kwargs = {} if sleep is None else {"sleep": sleep}
    hb = Heartbeat(
        SecretStr(url) if url else None,
        http,
        clock,
        interval_s=300,
        inputs=lambda: reading or _reading(),
        **kwargs,  # type: ignore[arg-type]
    )
    return hb, http, clock


async def test_posts_to_url_and_fail() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    hb, http, _ = _hb(transport)
    assert await hb.send_once() is True
    hb2, _, _ = _hb(transport, _reading(mode="SAFE", reasons=["Cerbo-Daten veraltet."]))
    assert await hb2.send_once() is True
    await http.aclose()
    assert [(r.method, str(r.url)) for r in seen] == [
        ("POST", "https://hc-ping.com/abc"),
        ("POST", "https://hc-ping.com/abc/fail"),
    ]
    assert seen[0].content.decode() == f"in Ordnung · CONTROL · SOC 55 % · USV ≈ 31{NBSP}h"
    assert seen[1].content.decode() == "Sicherer Zustand: Cerbo-Daten veraltet."
    assert seen[0].extensions["timeout"]["read"] == 10


async def test_send_failure_is_contained() -> None:
    fail = True

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("weg", request=request)
        return httpx.Response(200)

    hb, http, _ = _hb(httpx.MockTransport(handler))
    with structlog.testing.capture_logs() as logs:
        assert await hb.send_once() is False
        assert await hb.send_once() is False
        assert await hb.send_once() is False
        assert len(logs) == 1
    status = hb.component_status()
    assert status.ok is False
    assert status.message == "nicht gesendet seit 14:05"
    fail = False
    with structlog.testing.capture_logs() as logs:
        assert await hb.send_once() is True
        assert len(logs) == 1
    assert hb.component_status().ok is True
    await http.aclose()


async def test_non_2xx_counts_as_failure() -> None:
    hb, http, _ = _hb(httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await hb.send_once() is False
    await http.aclose()


async def test_url_never_logged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"kein Weg zu {request.url}", request=request)

    hb, http, _ = _hb(httpx.MockTransport(handler))
    with structlog.testing.capture_logs() as logs:
        assert await hb.send_once() is False
    status = hb.component_status()
    assert "abc" not in repr(logs)
    assert "abc" not in status.message
    assert "abc" not in repr(dict(status.details))
    await http.aclose()


async def test_down_since_is_tracked_by_heartbeat() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200)

    reading = _reading(connected=False)
    hb, http, clock = _hb(httpx.MockTransport(handler), reading)
    await hb.send_once()  # erster Poll ohne Verbindung: Karenzzeit
    clock.advance(119)
    await hb.send_once()
    clock.advance(1)
    await hb.send_once()
    assert [u.endswith("/fail") for u in sent] == [False, False, True]
    await http.aclose()


async def test_without_url_is_off() -> None:
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("darf nichts senden")

    hb, http, _ = _hb(httpx.MockTransport(handler), url=None, sleep=sleep)
    task = asyncio.ensure_future(hb.run())
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    assert await hb.send_once() is False
    status = hb.component_status()
    assert status.ok is True
    assert status.message == "aus (keine Ping-URL)"
    await http.aclose()


async def test_run_first_ping_after_30s_then_interval() -> None:
    sleeps: list[float] = []
    sent: list[str] = []

    class Stop(Exception):
        pass

    async def sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise Stop

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200)

    hb, http, _ = _hb(httpx.MockTransport(handler), sleep=sleep)
    with pytest.raises(Stop):
        await hb.run()
    assert sleeps == [30, 300, 300]
    assert len(sent) == 2
    await http.aclose()


def test_ping_info() -> None:
    clock = ManualClock(NOW)
    reading = Reading(True, "CONTROL", [], None, 55.0, None)
    off = Heartbeat(None, httpx.AsyncClient(), clock, interval_s=300, inputs=lambda: reading)
    assert off.ping_info() == PingInfo(False, True, None, None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    beat = Heartbeat(
        SecretStr("https://hc.example/abc"), http, clock, interval_s=300, inputs=lambda: reading
    )
    assert beat.ping_info() == PingInfo(True, True, None, None)
    assert asyncio.run(beat.send_once()) is True
    info = beat.ping_info()
    assert info.ok and info.last_sent == NOW and info.text == "in Ordnung · CONTROL · SOC 55 %"
