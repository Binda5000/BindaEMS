import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs
from tests.app_helpers import T_APP, sample_inputs
from websockets.asyncio.server import ServerConnection, serve

from bindaems.app.core_link import LiveState
from bindaems.app.ha.commands import Rejections
from bindaems.app.ha.entities import HaInputs, planned_view
from bindaems.app.ha.notify import (
    MAX_PER_MINUTE,
    MAX_REJECTIONS_PER_MINUTE,
    NOTIFY_AFTER_S,
    RETRY_AFTER_S,
    AlarmNotifications,
    HaNotifier,
    WarningNotifications,
    websocket_service_call,
)
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.plan import PlanVehicle
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


def _creates(env: Env) -> list[dict[str, Any]]:
    return [data for domain, service, data in env.calls if (domain, service) == _CREATE]


_CREATE = ("persistent_notification", "create")


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


async def test_service_call_failure_is_logged_and_does_not_count_as_notified(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    env.failing = True
    with capture_logs() as logs:
        await env.advance(NOTIFY_AFTER_S)  # wirft nicht
    failed = [e for e in logs if e["event"] == "Dienstaufruf an Home Assistant fehlgeschlagen"]
    # ohne persistente Meldung auch kein ``notify``: sonst käme es bei jedem neuen Versuch
    assert [e["service"] for e in failed] == ["persistent_notification.create"]
    assert env.calls == []
    env.failing = False
    env.live.alarms = [_alarm(), _alarm("competitor.other", message="Zweiter Alarm.")]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    messages = [
        data["message"] for domain, _, data in env.calls if domain == "persistent_notification"
    ]
    assert messages == ["Zweiter Alarm."]  # der erste wartet noch auf seinen nächsten Versuch


async def test_failed_create_is_retried_while_the_alarm_persists(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    env.failing = True
    await env.advance(NOTIFY_AFTER_S)  # der erste Versuch scheitert (HA startet neu)
    env.failing = False
    await env.advance(RETRY_AFTER_S - 1)
    assert env.calls == []  # Abstand zwischen den Versuchen
    await env.advance(1)
    assert [(domain, service) for domain, service, _ in env.calls] == [
        ("persistent_notification", "create"),
        ("notify", "mobile_app_handy"),
    ]
    await env.advance(3 * RETRY_AFTER_S)
    assert len(env.calls) == 2  # gemeldet: kein weiterer Versuch
    env.live.alarms = []
    await env.advance(1)
    assert env.calls[-1][:2] == ("persistent_notification", "dismiss")


async def test_alarm_over_the_budget_is_sent_when_it_frees_up(env: Env) -> None:
    env.live.alarms = [_alarm(f"competitor.a{n}") for n in range(MAX_PER_MINUTE + 2)]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    assert len(_creates(env)) == MAX_PER_MINUTE
    await env.advance(RETRY_AFTER_S)  # das Budget ist frei, die beiden Übrigen folgen
    ids = [data["notification_id"] for data in _creates(env)]
    assert len(ids) == MAX_PER_MINUTE + 2 == len(set(ids))
    await env.advance(3 * RETRY_AFTER_S)
    assert len(_creates(env)) == MAX_PER_MINUTE + 2


async def test_rejections_cannot_take_the_share_of_the_alarms(env: Env) -> None:
    assert (MAX_PER_MINUTE, MAX_REJECTIONS_PER_MINUTE) == (10, 4)
    with capture_logs() as logs:
        for n in range(20):  # eine Automation sendet eine Flut ungültiger Befehle
            await env.notifier.reject(f"Befehl aus Home Assistant abgelehnt: {n}.")
    assert len(_creates(env)) == MAX_REJECTIONS_PER_MINUTE
    assert [e["event"] for e in logs].count(
        "Benachrichtigung nicht gesendet: zu viele je Minute"
    ) == 20 - MAX_REJECTIONS_PER_MINUTE
    env.calls.clear()
    alarms = MAX_PER_MINUTE - MAX_REJECTIONS_PER_MINUTE
    env.live.alarms = [_alarm(f"competitor.a{n}") for n in range(alarms + 1)]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)  # noch in derselben Minute
    assert len(_creates(env)) == alarms  # insgesamt höchstens 10 je Minute


async def test_failed_dismiss_is_retried(env: Env) -> None:
    env.live.alarms = [_alarm()]
    await env.alarms.check()
    await env.advance(NOTIFY_AFTER_S)
    env.calls.clear()
    env.failing = True
    env.live.alarms = []
    await env.advance(1)  # das Entfernen scheitert
    env.failing = False
    await env.advance(RETRY_AFTER_S - 1)
    assert env.calls == []
    await env.advance(1)
    assert env.calls == [
        ("persistent_notification", "dismiss", {"notification_id": NOTIFICATION_ID})
    ]
    await env.advance(3 * RETRY_AFTER_S)
    assert len(env.calls) == 1


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


# Warnungen (Plan 4b, Entscheidung 14): die binary_sensor-Warnungen aus Home Assistant melden
# sich nach 30 s ohne Unterbrechung einmal und verschwinden mit ihnen.
PLANNED = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})
DEPARTURE = T_APP + timedelta(hours=10)  # 20:00 Ortszeit


def _plan_car(**changes: Any) -> PlanVehicle:
    fields: dict[str, Any] = {
        "key": "tesla",
        "name": "Tesla Model 3",
        "wallbox": "evcs",
        "target_soc_pct": 80.0,
        "departure_at": DEPARTURE,
        "soc_start_pct": 40.0,
        "soc_assumed": False,
        "limit_pct": None,
        "need_kwh": 33.3,
        "planned_kwh": 20.0,
        "shortfall_kwh": 13.3,
        "expected_soc_pct": 63.4,
        "finish_at": DEPARTURE,
        "reachable": False,
    }
    return PlanVehicle(**(fields | changes))


class WarnEnv(Env):
    def __init__(self, cfg: Config, clock: ManualClock, notify_service: str | None) -> None:
        super().__init__(clock, notify_service)
        self.inputs = sample_inputs()
        self.warnings = WarningNotifications(
            cfg, lambda: self.inputs, self.notifier, clock, live=self.live
        )

    def set(self, **changes: Any) -> None:
        self.inputs = sample_inputs(**changes)

    async def advance(self, seconds: float) -> None:
        """Wie ``Env.advance``; nach den Alarmen prüft sie jede Sekunde die Warnungen."""
        for _ in range(int(seconds)):
            self.clock.advance(1)
            await self.alarms.check()
            await self.warnings.check()


@pytest.fixture
def warn(cfg: Config) -> WarnEnv:
    return WarnEnv(cfg, ManualClock(T_APP), "notify.mobile_app_handy")


def _dismissed(env: Env) -> list[str]:
    return [
        data["notification_id"]
        for domain, service, data in env.calls
        if (domain, service) == ("persistent_notification", "dismiss")
    ]


async def test_warning_after_30_seconds_once_and_dismissed(warn: WarnEnv) -> None:
    warn.set(prices_missing=True)
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S - 1)
    assert warn.calls == []
    await warn.advance(1)
    assert warn.calls == [
        (
            "persistent_notification",
            "create",
            {
                "notification_id": "bindaems_warning_problem_prices",
                "title": "BindaEMS",
                "message": "Preise fehlen.",
            },
        ),
        ("notify", "mobile_app_handy", {"title": "BindaEMS", "message": "Preise fehlen."}),
    ]
    await warn.advance(3 * RETRY_AFTER_S)  # besteht weiter: keine zweite Meldung
    assert len(warn.calls) == 2
    warn.set(prices_missing=False)
    await warn.advance(1)
    assert _dismissed(warn) == ["bindaems_warning_problem_prices"]
    await warn.advance(3 * RETRY_AFTER_S)
    assert len(warn.calls) == 3


async def test_interrupted_warning_starts_counting_again(warn: WarnEnv) -> None:
    warn.set(prices_missing=True)
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S - 5)
    warn.set(prices_missing=False)
    await warn.advance(1)
    warn.set(prices_missing=True)
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S - 1)
    assert warn.calls == []
    await warn.advance(1)
    assert len(_creates(warn)) == 1


@pytest.mark.parametrize(
    ("changes", "notification_id", "text"),
    [
        (
            {"alarm_ids": ["competitor.dess"]},
            "bindaems_warning_problem_competitor",
            "Mitregelndes System erkannt.",
        ),
        (
            {"adapters_offline": ["twc", "tessie"]},
            "bindaems_warning_problem_device_offline",
            "Gerät offline: twc, tessie.",
        ),
        (
            {"vehicles": [{"id": "tesla", "assignment": "unclear"}]},
            "bindaems_warning_problem_assignment_unclear",
            "Zuordnung Auto und Wallbox unklar.",
        ),
    ],
)
async def test_each_warning_has_its_own_text(
    warn: WarnEnv, changes: dict[str, Any], notification_id: str, text: str
) -> None:
    warn.set(**changes)
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S)
    assert _creates(warn) == [
        {"notification_id": notification_id, "title": "BindaEMS", "message": text}
    ]


async def test_watchdog_warning_is_left_to_the_alarm(warn: WarnEnv) -> None:
    warn.set(watchdog_state="tripped")
    await warn.warnings.check()
    await warn.advance(2 * NOTIFY_AFTER_S)
    assert warn.calls == []


async def test_unknown_state_neither_counts_nor_dismisses(warn: WarnEnv) -> None:
    warn.set(alarm_ids=["competitor.dess"])
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S)
    assert len(_creates(warn)) == 1
    warn.set(core_connected=False, alarm_ids=["competitor.dess"])  # Warnung „unbekannt“
    await warn.advance(2 * NOTIFY_AFTER_S)
    warn.set(alarm_ids=["competitor.dess"])
    await warn.advance(2 * NOTIFY_AFTER_S)
    assert (len(_creates(warn)), _dismissed(warn)) == (1, [])
    # noch nicht gemeldet: „unbekannt“ unterbricht das Zählen
    warn.set(prices_missing=True, alarm_ids=["competitor.dess"])
    await warn.advance(NOTIFY_AFTER_S - 5)
    warn.set(core_connected=False, prices_missing=None, alarm_ids=["competitor.dess"])
    await warn.advance(10)
    warn.set(prices_missing=True, alarm_ids=["competitor.dess"])
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S - 1)
    assert len(_creates(warn)) == 1
    await warn.advance(1)
    assert len(_creates(warn)) == 2


async def test_target_unreachable_notifies_once(warn: WarnEnv) -> None:
    def unreachable(expected: float) -> dict[str, Any]:
        car = _plan_car(expected_soc_pct=expected)
        return {"intent": PLANNED, "now": warn.clock.now(), "plan_vehicles": [car]}

    warn.set(**unreachable(63.0))
    await warn.warnings.check()
    for n in range(10):  # bleibt unerreichbar über zehn Pläne
        warn.set(**unreachable(63.0 + n / 10))
        await warn.advance(NOTIFY_AFTER_S / 3)
    assert _creates(warn) == [
        {
            "notification_id": "bindaems_warning_tesla_target",
            "title": "BindaEMS",
            "message": (
                "Ziel-SOC unerreichbar: Tesla Model 3 erreicht bis 20:00 voraussichtlich nur 63 %."
            ),
        }
    ]
    assert warn.calls[1] == (
        "notify",
        "mobile_app_handy",
        {"title": "BindaEMS", "message": _creates(warn)[0]["message"]},
    )
    # ein Plan, der das Ziel erreicht, entfernt die Meldung
    reachable = _plan_car(reachable=True, shortfall_kwh=0.0, expected_soc_pct=80.0)
    warn.set(intent=PLANNED, now=warn.clock.now(), plan_vehicles=[reachable])
    await warn.advance(1)
    assert _dismissed(warn) == ["bindaems_warning_tesla_target"]
    # wieder unerreichbar: nach 30 s eine neue Meldung
    warn.set(**unreachable(55.0))
    await warn.warnings.check()
    await warn.advance(NOTIFY_AFTER_S)
    assert [c["message"] for c in _creates(warn)][1:] == [
        "Ziel-SOC unerreichbar: Tesla Model 3 erreicht bis 20:00 voraussichtlich nur 55 %."
    ]
    # nicht mehr in „Geplant“: auch dann verschwindet die Meldung
    warn.set(intent=Intent(version=4), now=warn.clock.now(), plan_vehicles=[_plan_car()])
    await warn.advance(1)
    assert _dismissed(warn) == ["bindaems_warning_tesla_target"] * 2


async def test_competitor_warning_silent_while_error_alarm_is_notified(warn: WarnEnv) -> None:
    warn.live.alarms = [_alarm()]  # Fehler competitor.foreign.wallbox.evcs
    warn.set(alarm_ids=[ALARM_ID])
    await warn.alarms.check()
    await warn.warnings.check()
    await warn.advance(3 * NOTIFY_AFTER_S)
    assert [c["notification_id"] for c in _creates(warn)] == [NOTIFICATION_ID]
    # der Fehler geht, eine Warnung competitor.* bleibt: nun meldet sich die Warnung
    warn.live.alarms = [_alarm("competitor.dess", severity="warning")]
    warn.set(alarm_ids=["competitor.dess"])
    await warn.advance(NOTIFY_AFTER_S + 1)  # sie zählt ab dem Entfernen des Fehlers
    assert [c["notification_id"] for c in _creates(warn)] == [
        NOTIFICATION_ID,
        "bindaems_warning_problem_competitor",
    ]
    # kommt ein Fehler competitor.* dazu und wird gemeldet, entfernt das die Warnung
    warn.live.alarms = [_alarm("competitor.dess", severity="warning"), _alarm()]
    warn.set(alarm_ids=["competitor.dess", ALARM_ID])
    await warn.advance(NOTIFY_AFTER_S + 1)
    assert [c["notification_id"] for c in _creates(warn)][2:] == [NOTIFICATION_ID]
    assert "bindaems_warning_problem_competitor" in _dismissed(warn)


async def test_competitor_warning_silent_when_the_error_alarm_comes_due_in_the_same_tick(
    warn: WarnEnv,
) -> None:
    # Fehler competitor.* und Warnung „Mitregelndes System“ bestehen gleich lange; die Warnungen
    # prüft dieser Takt zuerst. Es gibt nur die Meldung des Alarms, keine zweite auf dem Handy.
    warn.live.alarms = [_alarm()]
    warn.set(alarm_ids=[ALARM_ID])
    await warn.warnings.check()
    await warn.alarms.check()
    for _ in range(int(NOTIFY_AFTER_S) + 5):
        warn.clock.advance(1)
        await warn.warnings.check()
        await warn.alarms.check()
    assert [c["notification_id"] for c in _creates(warn)] == [NOTIFICATION_ID]
    assert [c for c in warn.calls if c[0] == "notify"] == [
        ("notify", "mobile_app_handy", {"title": "BindaEMS", "message": TEXT})
    ]


async def test_warnings_share_the_alarm_budget(warn: WarnEnv) -> None:
    warn.live.alarms = [_alarm(f"competitor.a{n}") for n in range(MAX_PER_MINUTE)]
    warn.set(prices_missing=True)
    await warn.alarms.check()
    await warn.warnings.check()
    with capture_logs() as logs:
        await warn.advance(NOTIFY_AFTER_S)
    assert len(_creates(warn)) == MAX_PER_MINUTE  # die zehn Alarme füllen das Budget
    assert "bindaems_warning_problem_prices" not in [c["notification_id"] for c in _creates(warn)]
    assert any(e["text"] == "Preise fehlen." for e in logs)
    await warn.advance(RETRY_AFTER_S)  # das Budget ist frei: die Warnung folgt
    assert _creates(warn)[-1]["notification_id"] == "bindaems_warning_problem_prices"
    assert len(_creates(warn)) == MAX_PER_MINUTE + 1


async def test_warning_check_survives_broken_inputs(cfg: Config, warn: WarnEnv) -> None:
    def broken() -> HaInputs:
        raise RuntimeError("Werte für Home Assistant noch nicht bestimmt")

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    warnings = WarningNotifications(
        cfg, broken, warn.notifier, warn.clock, live=warn.live, sleep=sleep
    )
    task = asyncio.create_task(warnings.run())
    try:
        for _ in range(5):
            await asyncio.sleep(0)
    finally:
        task.cancel()
    assert sleeps and set(sleeps) == {1.0}
    assert warn.calls == []


async def test_unreachable_target_without_expected_soc_waits(cfg: Config, warn: WarnEnv) -> None:
    # ohne Plan, aus Health: die Zeit reicht nicht, der Ladestand ist aber unbekannt
    health = {
        "id": "tesla",
        "mode": "planned",
        "plugged": True,
        "wallbox": "evcs",
        "soc_pct": None,
        "soc_source": None,
        "planned": {
            "state": "surplus",
            "target_soc_pct": 80.0,
            "departure_at": DEPARTURE.isoformat(),
            "need_kwh": 50.0,
            "latest_start": (DEPARTURE - timedelta(hours=1)).isoformat(),
            "plan_w": None,
        },
    }
    warn.set(intent=PLANNED, now=warn.clock.now(), vehicles=[health])
    view = planned_view(cfg, "tesla", warn.inputs)
    assert view is not None and (view.reachable, view.expected_soc_pct) == (False, None)
    await warn.warnings.check()
    await warn.advance(2 * NOTIFY_AFTER_S)
    assert warn.calls == []
