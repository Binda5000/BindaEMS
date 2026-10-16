import asyncio
import json
from typing import Any

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.app_helpers import T_APP
from websockets.asyncio.server import ServerConnection, serve

from bindaems.app.core_link import LiveState
from bindaems.app.ha.commands import Rejections
from bindaems.app.ha.notify import (
    MAX_PER_MINUTE,
    NOTIFY_AFTER_S,
    AlarmNotifications,
    HaNotifier,
    websocket_service_call,
)
from bindaems.shared.timeutil import ManualClock

ALARM_ID = "competitor.foreign.wallbox.evcs"
NOTIFICATION_ID = "bindaems_competitor_foreign_wallbox_evcs"
TEXT = "Fremde Steuerung an der Wallbox EVCS erkannt."

Call = tuple[str, str, dict[str, Any]]


def _alarm(alarm_id: str = ALARM_ID, severity: str = "error", message: str = TEXT) -> dict:
    return {"id": alarm_id, "severity": severity, "message": message, "since": "…"}


class Env:
    def __init__(self, clock: ManualClock, notify_service: str | None) -> None:
        self.clock = clock
        self.live = LiveState()
        self.calls: list[Call] = []
        self.failing = False
        self.notifier = HaNotifier(self._call, notify_service, clock)
        self.alarms = AlarmNotifications(self.live, self.notifier, clock)

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if self.failing:
            raise ConnectionError("Home Assistant nicht erreichbar")
        self.calls.append((domain, service, data))

    async def advance(self, seconds: float) -> None:
        """Lässt die Uhr in Sekunden vorrücken und prüft nach jeder Sekunde die Alarme."""
        for _ in range(int(seconds)):
            self.clock.advance(1)
            await self.alarms.check()


@pytest.fixture
def env() -> Env:
    return Env(ManualClock(T_APP), "notify.mobile_app_handy")


async def test_error_alarm_is_sent_after_30_s(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S - 1)
    assert env.calls == []
    await env.advance(1)
    assert env.calls == [
        (
            "persistent_notification",
            "create",
            {"notification_id": NOTIFICATION_ID, "title": "BindaEMS", "message": TEXT},
        ),
        ("notify", "mobile_app_handy", {"title": "BindaEMS", "message": TEXT}),
    ]
    await env.advance(60)
    assert len(env.calls) == 2  # einmal je Auftreten


async def test_without_notify_service_only_the_persistent_notification(env: Env) -> None:
    env = Env(ManualClock(T_APP), None)
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    assert [(domain, service) for domain, service, _ in env.calls] == [
        ("persistent_notification", "create")
    ]


async def test_short_alarm_is_not_sent(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(20)
    env.live.alarms = []
    await env.advance(60)
    assert env.calls == []


async def test_interrupted_alarm_starts_counting_again(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(20)
    env.live.alarms = []
    await env.advance(1)
    env.live.alarms = [_alarm()]
    await env.advance(20)
    assert env.calls == []
    await env.advance(11)  # 30 s seit dem Wiederauftreten
    assert len(env.calls) == 2


async def test_cleared_alarm_dismisses_the_notification(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    env.calls.clear()
    env.live.alarms = []
    await env.advance(1)
    assert env.calls == [
        ("persistent_notification", "dismiss", {"notification_id": NOTIFICATION_ID})
    ]
    await env.advance(5)
    assert len(env.calls) == 1


async def test_alarm_returning_is_sent_again(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    env.live.alarms = []
    await env.advance(1)
    env.calls.clear()
    env.live.alarms = [_alarm()]
    await env.advance(NOTIFY_AFTER_S + 1)
    assert [(domain, service) for domain, service, _ in env.calls] == [
        ("persistent_notification", "create"),
        ("notify", "mobile_app_handy"),
    ]


async def test_warnings_are_not_sent(env: Env) -> None:
    env.live.alarms = [_alarm(severity="warning")]
    await env.alarms.check()
    await env.advance(60)
    env.live.alarms = []
    await env.advance(1)
    assert env.calls == []


async def test_rate_limit_per_minute(env: Env) -> None:
    env.live.alarms = [_alarm(f"competitor.a{n}") for n in range(12)]
    await env.alarms.check()
    with capture_logs() as logs:
        await env.advance(NOTIFY_AFTER_S)
    creates = [call for call in env.calls if call[:2] == ("persistent_notification", "create")]
    assert len(creates) == MAX_PER_MINUTE == 10
    limited = [
        entry
        for entry in logs
        if entry["event"] == "Benachrichtigung nicht gesendet: zu viele je Minute"
    ]
    assert len(limited) == 2
    assert {entry["text"] for entry in limited} == {TEXT}


async def test_rate_limit_frees_up_after_a_minute(env: Env) -> None:
    for n in range(MAX_PER_MINUTE):
        await env.notifier.alarm_raised(f"a.{n}", TEXT)
    env.calls.clear()
    await env.notifier.alarm_raised("a.late", TEXT)
    assert env.calls == []
    env.clock.advance(60)
    await env.notifier.alarm_raised("a.late", TEXT)
    assert len(env.calls) == 2


async def test_dropped_alarm_is_not_dismissed(env: Env) -> None:
    for n in range(MAX_PER_MINUTE):
        await env.notifier.alarm_raised(f"a.{n}", TEXT)
    await env.notifier.alarm_raised("a.dropped", TEXT)
    env.calls.clear()
    await env.notifier.alarm_cleared("a.dropped")
    assert env.calls == []
    await env.notifier.alarm_cleared("a.0")
    assert env.calls == [
        ("persistent_notification", "dismiss", {"notification_id": "bindaems_a_0"})
    ]


async def test_rejection_replaces_one_notification(env: Env) -> None:
    notifier: Rejections = env.notifier
    await notifier.reject("Befehl aus Home Assistant abgelehnt: erster Grund.")
    await notifier.reject("Befehl aus Home Assistant abgelehnt: zweiter Grund.")
    assert env.calls == [
        (
            "persistent_notification",
            "create",
            {
                "notification_id": "bindaems_ha_command",
                "title": "BindaEMS",
                "message": f"Befehl aus Home Assistant abgelehnt: {reason} Grund.",
            },
        )
        for reason in ("erster", "zweiter")
    ]


async def test_service_call_failure_is_logged_and_retried_with_the_next_alarm(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    env.failing = True
    with capture_logs() as logs:
        await env.advance(NOTIFY_AFTER_S)  # wirft nicht
    assert [entry["event"] for entry in logs].count(
        "Dienstaufruf an Home Assistant fehlgeschlagen"
    ) == 2
    assert env.calls == []
    env.failing = False
    env.live.alarms = [_alarm(), _alarm("competitor.other", message="Zweiter Alarm.")]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    messages = [
        data["message"] for domain, _, data in env.calls if domain == "persistent_notification"
    ]
    assert messages == ["Zweiter Alarm."]  # der erste Alarm gilt als gemeldet, nicht wiederholt


async def test_run_checks_every_second(env: Env) -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        env.clock.advance(seconds)
        await asyncio.sleep(0)

    alarms = AlarmNotifications(env.live, env.notifier, env.clock, sleep=sleep)
    env.live.alarms = [_alarm()]
    task = asyncio.create_task(alarms.run())
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            if env.calls:
                break
    finally:
        task.cancel()
    assert set(sleeps) == {1.0}
    assert len(sleeps) >= NOTIFY_AFTER_S
    assert len(env.calls) == 2


async def test_check_survives_a_broken_alarm_list(env: Env) -> None:
    env.live.alarms = [{"severity": "error"}, {"id": 5, "severity": "error"}, _alarm()]  # type: ignore[list-item]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    assert len(env.calls) == 2


# --- websocket_service_call gegen einen kleinen lokalen Server --------------------------------


class FakeHa:
    def __init__(self, replies: list[dict[str, Any]], greet: bool = True) -> None:
        self.received: list[dict[str, Any]] = []
        self.connections = 0
        self._replies = replies
        self._greet = greet

    async def handle(self, ws: ServerConnection) -> None:
        self.connections += 1
        if not self._greet:
            await ws.wait_closed()  # schweigt, bis der Client aufgibt
            return
        await ws.send(json.dumps({"type": "auth_required"}))
        self.received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps(self._replies[0]))
        if self._replies[0]["type"] != "auth_ok":
            return
        self.received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps(self._replies[1] | {"id": self.received[-1]["id"]}))
        await ws.wait_closed()


RESULT_OK = {"type": "result", "success": True, "result": {"context": {}}}


async def _call_against(ha: FakeHa, **kwargs: Any) -> None:
    async with serve(ha.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        call = websocket_service_call(f"http://127.0.0.1:{port}/", SecretStr("tok"), **kwargs)
        await call("persistent_notification", "create", {"notification_id": "x", "message": "m"})


async def test_websocket_service_call_authenticates_and_calls_the_service() -> None:
    ha = FakeHa([{"type": "auth_ok"}, RESULT_OK])
    await _call_against(ha)
    assert ha.received[0] == {"type": "auth", "access_token": "tok"}
    assert ha.received[1]["type"] == "call_service"
    assert ha.received[1]["domain"] == "persistent_notification"
    assert ha.received[1]["service"] == "create"
    assert ha.received[1]["service_data"] == {"notification_id": "x", "message": "m"}


async def test_websocket_service_call_uses_one_connection_per_call() -> None:
    ha = FakeHa([{"type": "auth_ok"}, RESULT_OK])
    async with serve(ha.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        call = websocket_service_call(f"http://127.0.0.1:{port}", SecretStr("tok"))
        await call("notify", "mobile_app_handy", {"message": "a"})
        await call("notify", "mobile_app_handy", {"message": "b"})
    assert ha.connections == 2


async def test_websocket_service_call_rejects_an_invalid_token() -> None:
    ha = FakeHa([{"type": "auth_invalid", "message": "falsch"}])
    with pytest.raises(ConnectionError, match="Token"):
        await _call_against(ha)


async def test_websocket_service_call_reports_a_failed_result() -> None:
    failed = {"type": "result", "success": False, "error": {"code": "not_found", "message": "x"}}
    ha = FakeHa([{"type": "auth_ok"}, failed])
    with pytest.raises(ConnectionError, match="not_found"):
        await _call_against(ha)


async def test_websocket_service_call_times_out() -> None:
    ha = FakeHa([], greet=False)
    with pytest.raises(TimeoutError):
        await _call_against(ha, timeout_s=0.2)
