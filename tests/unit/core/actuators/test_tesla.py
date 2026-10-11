"""Tests des Tesla-Aktors: Befehle über Tessie im Hintergrund, das Fahrzeug wird nie geweckt."""

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from tests.helpers import T0, run_until, snap
from tests.unit.core.safety.conftest import make_ctx

from bindaems.core.actuators.tesla import (
    COMMAND_TIMEOUT_S,
    FAILURE_WINDOW,
    FAILURES_ALLOWED,
    TeslaActuator,
)
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.control.actuator import Capabilities, Confirmation
from bindaems.core.control.executor import CommandExecutor
from bindaems.core.safety.budget import PersistentWriteBudget
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.state.store import StateStore
from bindaems.shared.control import Command, CommandKind
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.domain import Alarm, Quality, Reading, Severity, SignalKind, Snapshot, Value
from bindaems.shared.timeutil import ManualClock

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
STATE = json.loads((FIXTURES / "tessie_state.json").read_text())
VIN = "5YJ3E7EB0MF000000"
TOKEN = "geheim"
URL = f"https://api.tessie.com/{VIN}/command/set_charging_amps"
STATE_URL = f"https://api.tessie.com/{VIN}/state"
HOME = {"vehicle.tesla.online": True, "vehicle.tesla.at_home": True, "vehicle.tesla.plugged": True}

NOT_REACHABLE = "Tessie nicht erreichbar."
ASLEEP = "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
AWAY = "Fahrzeug nicht zu Hause."
UNPLUGGED = "Fahrzeug nicht angesteckt."
AFTER_FAILURES = "Nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar."
STILL_RUNNING = "Befehl an Tessie läuft noch."
CANCELLED = "Befehl an Tessie abgebrochen."
CONFIRMED = (Confirmation.CONFIRMED, "")
PENDING = (Confirmation.PENDING, "")


def command_url(name: str) -> str:
    return f"https://api.tessie.com/{VIN}/command/{name}"


def tesla(
    cfg,
    client,
    now: list[datetime],
    *,
    connected: Callable[[], bool] = lambda: True,
    token: str = TOKEN,
) -> TeslaActuator:
    return TeslaActuator(
        "tesla", cfg.vehicles["tesla"], SecretStr(token), client, connected, clock=lambda: now[0]
    )


@pytest.fixture(scope="module")
def idle_client() -> httpx.AsyncClient:
    """Ein Client für Tests, die nichts über das Netz schicken: Er bleibt ungeöffnet.

    Ein Client entsteht nicht billig (er lädt die Zertifikate); Tests, die senden, brauchen einen
    eigenen unter respx, die anderen teilen diesen.
    """
    return httpx.AsyncClient()


@pytest.fixture
def car(cfg, idle_client) -> TeslaActuator:
    """Ein Tesla mit bestehender Verbindung zu Tessie; wer ihn benutzt, sendet nichts."""
    return tesla(cfg, idle_client, [T0])


def readings(signals: Mapping[str, tuple[Value, Quality]]) -> Snapshot:
    """Snapshot mit Qualität je Signal."""
    return Snapshot(
        T0,
        {
            name: Reading(value, T0, quality, "tessie:tesla", SignalKind.MEASUREMENT)
            for name, (value, quality) in signals.items()
        },
    )


async def fail_at(
    car: TeslaActuator, now: list[datetime], *minutes: float, start: datetime = T0
) -> None:
    """Sendet zu den Minuten nach ``start`` je einen Strombefehl und wartet auf sein Ende."""
    for minute in minutes:
        now[0] = start + timedelta(minutes=minute)
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)


# Vorgabe des Plans


async def test_asleep_is_not_controllable(cfg) -> None:
    async with httpx.AsyncClient() as client:
        caps = tesla(cfg, client, [T0]).capabilities(
            snap(HOME | {"vehicle.tesla.online": False}), T0
        )
    assert caps == Capabilities(
        False, ("Fahrzeug schläft oder ist offline; es wird nicht geweckt.",)
    )


async def test_command_runs_in_background(cfg, respx_mock) -> None:
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    route = respx_mock.post(URL).mock(side_effect=answer)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(CommandKind.CURRENT, 10.0) is None  # kehrt sofort zurück
        await asyncio.sleep(0)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({}))[0] == Confirmation.PENDING
        release.set()
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({}))[0] == Confirmation.CONFIRMED
    request = route.calls[0].request
    assert (
        request.url.params["amps"] == "10" and request.url.params["wait_for_completion"] == "true"
    )
    assert request.headers["Authorization"] == "Bearer geheim"


async def test_result_false_fails(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(json={"result": False})
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED,
            "Tessie meldet result=false.",
        )


async def test_three_failures_make_it_unavailable(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        for minute in range(3):
            now[0] = T0 + timedelta(minutes=minute)
            car.send(CommandKind.CURRENT, 10.0)
            await asyncio.sleep(0.01)
        later = T0 + timedelta(minutes=3)
        caps = car.capabilities(snap(HOME), later)
        assert caps.reasons == ("Nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar.",)
        assert [a.message for a in car.alarms(later)] == [
            "Tesla Model 3 ist nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar."
        ]
        assert car.capabilities(snap(HOME), T0 + timedelta(minutes=18)).controllable


async def test_token_never_in_failure_text(cfg, respx_mock) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("Bearer geheim abgelehnt"))
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED,
            "ConnectError: Bearer *** abgelehnt",
        )


# Kennung, Frist und Aufbau


def test_identity_and_deadline(car) -> None:
    assert (car.id, car.confirm_timeout_s) == ("vehicle.tesla", 75.0)
    assert (COMMAND_TIMEOUT_S, FAILURES_ALLOWED, FAILURE_WINDOW) == (60.0, 3, timedelta(minutes=15))
    assert car.confirm_timeout_s > COMMAND_TIMEOUT_S  # das Ende des Befehls kommt vor der Frist


def test_the_key_names_the_vehicle(cfg, idle_client) -> None:
    zoe = TeslaActuator(
        "zoe", cfg.vehicles["tesla"], SecretStr(TOKEN), idle_client, lambda: True, clock=lambda: T0
    )
    zoe_home = {name.replace("tesla", "zoe"): value for name, value in HOME.items()}
    assert zoe.id == "vehicle.zoe"
    assert zoe.capabilities(snap(zoe_home), T0) == Capabilities(True)
    assert zoe.capabilities(snap(HOME), T0).reasons == (ASLEEP, AWAY, UNPLUGGED)


def test_a_vehicle_without_tessie_is_refused(cfg, idle_client) -> None:
    with pytest.raises(ValueError, match="egolf"):
        TeslaActuator(
            "egolf",
            cfg.vehicles["egolf"],
            SecretStr(TOKEN),
            idle_client,
            lambda: True,
            clock=lambda: T0,
        )


# Fähigkeiten


def test_controllable_when_online_at_home_and_plugged(car) -> None:
    caps = car.capabilities(snap(HOME), T0)
    assert caps == Capabilities(True) and caps.reasons == ()


@pytest.mark.parametrize(
    ("connected", "state", "reasons"),
    [
        (False, HOME, (NOT_REACHABLE,)),
        (True, HOME | {"vehicle.tesla.online": False}, (ASLEEP,)),
        (True, HOME | {"vehicle.tesla.at_home": False}, (AWAY,)),
        (True, HOME | {"vehicle.tesla.plugged": False}, (UNPLUGGED,)),
        (
            True,
            HOME | {"vehicle.tesla.at_home": False, "vehicle.tesla.plugged": False},
            (AWAY, UNPLUGGED),
        ),
        (False, dict.fromkeys(HOME, False), (NOT_REACHABLE, ASLEEP, AWAY, UNPLUGGED)),
    ],
)
def test_reasons_come_in_this_order(cfg, idle_client, connected, state, reasons) -> None:
    car = tesla(cfg, idle_client, [T0], connected=lambda: connected)
    assert car.capabilities(snap(state), T0) == Capabilities(False, reasons)


async def test_unavailability_comes_last(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now, reachable = [T0], [True]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now, connected=lambda: reachable[0])
        await fail_at(car, now, 0, 1, 2)
        reachable[0] = False
        caps = car.capabilities(snap(dict.fromkeys(HOME, False)), T0 + timedelta(minutes=3))
    assert caps.reasons == (NOT_REACHABLE, ASLEEP, AWAY, UNPLUGGED, AFTER_FAILURES)


def test_stale_invalid_or_missing_signals_are_reasons(car) -> None:
    expected = Capabilities(False, (ASLEEP, AWAY, UNPLUGGED))
    assert car.capabilities(snap(HOME, quality=Quality.STALE), T0) == expected
    assert car.capabilities(snap(HOME, quality=Quality.INVALID), T0) == expected
    assert car.capabilities(snap({}), T0) == expected


@pytest.mark.parametrize("value", [None, 1, 1.0, "True", "online"])
def test_only_true_counts(car, value) -> None:
    # Nur der Wahrheitswert True zählt; jeder andere Wert ist ein Grund, auch ein „wahrer“.
    caps = car.capabilities(snap(dict.fromkeys(HOME, value)), T0)
    assert caps == Capabilities(False, (ASLEEP, AWAY, UNPLUGGED))


def test_the_connection_is_asked_on_every_call(cfg, idle_client) -> None:
    reachable = [True]
    car = tesla(cfg, idle_client, [T0], connected=lambda: reachable[0])
    assert car.capabilities(snap(HOME), T0).controllable
    reachable[0] = False
    assert car.capabilities(snap(HOME), T0) == Capabilities(False, (NOT_REACHABLE,))
    reachable[0] = True
    assert car.capabilities(snap(HOME), T0).controllable


async def test_not_controllable_until_the_deadline(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1, 2)
        until = T0 + timedelta(minutes=17)  # der dritte Fehler um 2 min, dazu 15 min
        assert not car.capabilities(snap(HOME), until - timedelta(microseconds=1)).controllable
        assert car.capabilities(snap(HOME), until) == Capabilities(True)


async def test_the_deadline_is_shown_in_vienna_time(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    winter = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)  # 11:00 Uhr in Wien (MEZ), nicht 12:00
    now = [winter]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1, 2, start=winter)
        caps = car.capabilities(snap(HOME), winter + timedelta(minutes=3))
        assert caps.reasons == ("Nach 3 Fehlern in 15 min bis 11:17 nicht steuerbar.",)
        assert car.alarms(winter + timedelta(minutes=3))[0].message == (
            "Tesla Model 3 ist nach 3 Fehlern in 15 min bis 11:17 nicht steuerbar."
        )


# Zurückgelesene Werte

UNUSABLE_AMPS = [
    pytest.param((12, Quality.STALE), id="stale"),
    pytest.param((12, Quality.INVALID), id="invalid"),
    pytest.param((None, Quality.INVALID), id="no-value"),  # so speichert der Speicher ein None
    pytest.param((float("nan"), Quality.OK), id="nan"),
    pytest.param((float("inf"), Quality.OK), id="inf"),
    pytest.param((True, Quality.OK), id="bool"),
    pytest.param(("12", Quality.OK), id="text"),
]


def test_present_reads_current_and_charging(car) -> None:
    state = readings(
        {
            "vehicle.tesla.charge_amps": (12, Quality.OK),
            "vehicle.tesla.charging_state": ("Charging", Quality.OK),
        }
    )
    present = car.present(state)
    assert present == {CommandKind.CURRENT: 12, CommandKind.CHARGING: True}
    assert present[CommandKind.CHARGING] is True


def test_nothing_is_known_without_readings(car) -> None:
    assert car.present(readings({})) == {}


@pytest.mark.parametrize("reading", UNUSABLE_AMPS)
def test_unusable_current_is_unknown(car, reading) -> None:
    assert car.present(readings({"vehicle.tesla.charge_amps": reading})) == {}


def test_a_current_of_zero_is_a_value(car) -> None:
    state = readings({"vehicle.tesla.charge_amps": (0, Quality.OK)})
    assert car.present(state) == {CommandKind.CURRENT: 0}


def test_only_the_set_current_counts(car) -> None:
    # Angeforderter und tatsächlicher Strom sind nicht der eingestellte Ladestrom.
    state = readings(
        {
            "vehicle.tesla.current_request_a": (16, Quality.OK),
            "vehicle.tesla.actual_current_a": (16, Quality.OK),
        }
    )
    assert car.present(state) == {}


@pytest.mark.parametrize(
    ("text", "charging"),
    [
        ("Charging", True),
        ("Stopped", False),
        ("Complete", False),
        ("Disconnected", False),
        ("NoPower", False),
        ("Starting", False),
    ],
)
def test_charging_is_true_only_for_the_text_charging(car, text, charging) -> None:
    present = car.present(readings({"vehicle.tesla.charging_state": (text, Quality.OK)}))
    assert present == {CommandKind.CHARGING: charging}
    assert present[CommandKind.CHARGING] is charging


@pytest.mark.parametrize(
    "reading",
    [
        pytest.param(("Charging", Quality.STALE), id="stale"),
        pytest.param(("Charging", Quality.INVALID), id="invalid"),
        pytest.param((None, Quality.INVALID), id="no-value"),
        pytest.param((1, Quality.OK), id="number"),
        pytest.param((True, Quality.OK), id="bool"),
    ],
)
def test_unusable_charging_state_is_unknown(car, reading) -> None:
    assert car.present(readings({"vehicle.tesla.charging_state": reading})) == {}


def test_present_reports_each_kind_on_its_own(car) -> None:
    amps = readings({"vehicle.tesla.charge_amps": (10, Quality.OK)})
    state = readings({"vehicle.tesla.charging_state": ("Stopped", Quality.OK)})
    assert car.present(amps) == {CommandKind.CURRENT: 10}
    assert car.present(state) == {CommandKind.CHARGING: False}


def test_a_sleeping_car_is_still_read(car) -> None:
    # Gelesen wird aus dem Cache, ob das Auto schläft oder nicht.
    state = readings(
        {
            "vehicle.tesla.online": (False, Quality.OK),
            "vehicle.tesla.charge_amps": (8, Quality.OK),
        }
    )
    assert car.present(state) == {CommandKind.CURRENT: 8}


# Senden


@pytest.mark.parametrize(
    ("kind", "value", "name", "params"),
    [
        (CommandKind.CURRENT, 10.0, "set_charging_amps", [("amps", "10")]),
        (CommandKind.CURRENT, 5, "set_charging_amps", [("amps", "5")]),
        (CommandKind.CHARGING, True, "start_charging", []),
        (CommandKind.CHARGING, False, "stop_charging", []),
    ],
)
async def test_each_kind_goes_to_its_command(cfg, respx_mock, kind, value, name, params) -> None:
    route = respx_mock.post(command_url(name)).respond(json={"result": True})
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(kind, value) is None
        await asyncio.sleep(0.01)
        assert car.confirm(kind, value, snap({})) == CONFIRMED
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert sorted(request.url.params.multi_items()) == sorted(
        [("wait_for_completion", "true"), *params]
    )
    assert request.headers["Authorization"] == "Bearer geheim"
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 60.0
    )


@pytest.mark.parametrize(("amps", "sent"), [(6.0, "6"), (6.4, "6"), (15.6, "16"), (16, "16")])
async def test_current_is_sent_in_whole_amps(cfg, respx_mock, amps, sent) -> None:
    route = respx_mock.post(URL).respond(json={"result": True})
    async with httpx.AsyncClient() as client:
        assert tesla(cfg, client, [T0]).send(CommandKind.CURRENT, amps) is None
        await asyncio.sleep(0.01)
    assert route.calls.last.request.url.params["amps"] == sent


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (CommandKind.GRID_SETPOINT, "grid_setpoint"),
        (CommandKind.MAX_DISCHARGE, "max_discharge"),
        (CommandKind.MIN_SOC, "min_soc"),
    ],
)
async def test_other_kinds_are_not_sent(car, respx_mock, kind, name) -> None:
    assert car.send(kind, 100.0) == f"Tesla kennt die Befehlsart „{name}“ nicht."
    await asyncio.sleep(0.01)
    assert respx_mock.calls.call_count == 0


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "10"])
async def test_unusable_current_is_not_sent(car, respx_mock, value) -> None:
    assert car.send(CommandKind.CURRENT, value) == "Ungültiger Wert für „current“."
    await asyncio.sleep(0.01)
    assert respx_mock.calls.call_count == 0


@pytest.mark.parametrize("value", [None, 1, 0, 1.0, "an", float("nan")])
async def test_charging_must_be_a_boolean(car, respx_mock, value) -> None:
    assert car.send(CommandKind.CHARGING, value) == "Ungültiger Wert für „charging“."
    await asyncio.sleep(0.01)
    assert respx_mock.calls.call_count == 0


async def test_reading_never_sends(car, respx_mock) -> None:
    car.capabilities(snap(HOME), T0)
    car.present(snap({}))
    car.confirm(CommandKind.CURRENT, 10.0, snap({}))
    car.alarms(T0)
    await asyncio.sleep(0.01)
    assert respx_mock.calls.call_count == 0


# Hintergrund


async def test_a_kind_is_not_sent_twice_while_it_runs(cfg, respx_mock) -> None:
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    route = respx_mock.post(URL).mock(side_effect=answer)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(CommandKind.CURRENT, 10.0) is None
        await asyncio.sleep(0)
        assert car.send(CommandKind.CURRENT, 12.0) == STILL_RUNNING
        release.set()
        await asyncio.sleep(0.01)
        assert car.send(CommandKind.CURRENT, 12.0) is None  # der erste ist fertig
        await asyncio.sleep(0.01)
    assert [call.request.url.params["amps"] for call in route.calls] == ["10", "12"]


async def test_the_kinds_run_independently(cfg, respx_mock) -> None:
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    respx_mock.post(URL).mock(side_effect=answer)
    respx_mock.post(command_url("start_charging")).respond(json={"result": True})
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(CommandKind.CURRENT, 10.0) is None
        await asyncio.sleep(0)
        assert car.send(CommandKind.CHARGING, True) is None  # trotz des laufenden Strombefehls
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CHARGING, True, snap({})) == CONFIRMED
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == PENDING
        release.set()
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == CONFIRMED


async def test_a_result_stays_until_the_next_send(cfg, respx_mock) -> None:
    release = asyncio.Event()
    release.set()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    respx_mock.post(URL).mock(side_effect=answer)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        for _ in range(3):  # das Ergebnis geht beim Abfragen nicht verloren
            assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == CONFIRMED
        release.clear()
        assert car.send(CommandKind.CURRENT, 12.0) is None
        await asyncio.sleep(0)
        assert car.confirm(CommandKind.CURRENT, 12.0, snap({})) == PENDING  # das alte ist ersetzt
        release.set()
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 12.0, snap({})) == CONFIRMED


def test_nothing_is_confirmed_for_a_kind_that_was_never_sent(car) -> None:
    assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == PENDING
    assert car.confirm(CommandKind.CHARGING, True, snap({})) == PENDING


# Antworten von Tessie


@pytest.mark.parametrize("code", [200, 201, 202])
async def test_any_2xx_with_result_true_confirms(cfg, respx_mock, code) -> None:
    respx_mock.post(URL).respond(code, json={"result": True})
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == CONFIRMED


@pytest.mark.parametrize("code", [302, 401, 404, 429, 500, 503])
async def test_a_status_outside_2xx_names_the_code(cfg, respx_mock, code) -> None:
    respx_mock.post(URL).respond(code, json={"result": True})  # auch mit result=true
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED,
            f"Tessie antwortet mit HTTP {code}.",
        )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"result": False}, id="false"),
        pytest.param({}, id="no-result"),
        pytest.param({"result": None}, id="null"),
        pytest.param({"result": "true"}, id="text"),
        pytest.param({"result": 1}, id="number"),
        pytest.param({"success": True}, id="other-key"),
        pytest.param([True], id="list"),
        pytest.param("ok", id="string"),
        pytest.param(None, id="json-null"),
    ],
)
async def test_only_result_true_confirms(cfg, respx_mock, body) -> None:
    respx_mock.post(URL).respond(200, content=json.dumps(body))
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED,
            "Tessie meldet result=false.",
        )


@pytest.mark.parametrize("content", ["<html>Bad Gateway</html>", ""], ids=["html", "empty"])
async def test_a_body_without_json_fails_like_an_exception(cfg, respx_mock, content) -> None:
    respx_mock.post(URL).respond(200, content=content)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        status, text = car.confirm(CommandKind.CURRENT, 10.0, snap({}))
    assert status == Confirmation.FAILED and text.startswith("JSONDecodeError: ")


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (httpx.ConnectError("Verbindung abgelehnt"), "ConnectError: Verbindung abgelehnt"),
        (httpx.ReadTimeout("Zeitüberschreitung"), "ReadTimeout: Zeitüberschreitung"),
        (httpx.ReadTimeout(""), "ReadTimeout"),  # httpx meldet Zeitüberschreitungen ohne Text
        (RuntimeError("kaputt"), "RuntimeError: kaputt"),
    ],
)
async def test_an_exception_gives_its_type_and_text(cfg, respx_mock, error, text) -> None:
    respx_mock.post(URL).mock(side_effect=error)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (Confirmation.FAILED, text)


@pytest.mark.parametrize(
    ("message", "text"),
    [
        ("Bearer geheim abgelehnt", "ConnectError: Bearer *** abgelehnt"),
        ("geheim und nochmal geheim", "ConnectError: *** und nochmal ***"),
        ("xgeheimx", "ConnectError: x***x"),
        ("geheim", "ConnectError: ***"),
    ],
)
async def test_every_occurrence_of_the_token_is_masked(cfg, respx_mock, message, text) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError(message))
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (Confirmation.FAILED, text)


async def test_an_empty_token_does_not_garble_the_text(cfg, respx_mock) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("abc"))
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0], token="")
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED,
            "ConnectError: abc",
        )


# Zählung der Fehler


async def test_two_failures_are_not_enough(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1)
        later = T0 + timedelta(minutes=2)
        assert car.capabilities(snap(HOME), later) == Capabilities(True)
        assert car.alarms(later) == []


async def test_old_failures_leave_the_window(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 10, 16)  # der erste liegt beim dritten 16 min zurück
        assert car.capabilities(snap(HOME), T0 + timedelta(minutes=16)).controllable
        await fail_at(car, now, 17)  # 10, 16 und 17 liegen binnen 15 min
        caps = car.capabilities(snap(HOME), T0 + timedelta(minutes=18))
        assert caps.reasons == ("Nach 3 Fehlern in 15 min bis 12:32 nicht steuerbar.",)


@pytest.mark.parametrize(
    ("third", "unavailable"),
    [
        (timedelta(minutes=15) - timedelta(seconds=1), True),
        (timedelta(minutes=15), False),  # ein Fehler, der 15 min zurückliegt, zählt nicht mehr
    ],
)
async def test_the_window_is_fifteen_minutes(cfg, respx_mock, third, unavailable) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 5)
        now[0] = T0 + third
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.capabilities(snap(HOME), now[0]).controllable == (not unavailable)


async def test_a_success_does_not_reset_the_count(cfg, respx_mock) -> None:
    respx_mock.post(URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"result": True}),
            httpx.Response(500),
            httpx.Response(500),
        ]
    )
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1, 2, 3)
        assert not car.capabilities(snap(HOME), T0 + timedelta(minutes=4)).controllable


async def test_failures_of_all_kinds_count_together(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    respx_mock.post(command_url("start_charging")).respond(json={"result": False})
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        car.send(CommandKind.CURRENT, 10.0)  # HTTP 500
        await asyncio.sleep(0.01)
        now[0] = T0 + timedelta(minutes=1)
        car.send(CommandKind.CHARGING, True)  # result=false
        await asyncio.sleep(0.01)
        assert car.capabilities(snap(HOME), now[0]).controllable
        await fail_at(car, now, 2)
        assert not car.capabilities(snap(HOME), now[0]).controllable


async def test_the_clock_is_read_when_the_failure_happens(cfg, respx_mock) -> None:
    release = asyncio.Event()
    release.set()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(500)

    respx_mock.post(URL).mock(side_effect=answer)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1)
        release.clear()
        await fail_at(car, now, 2)  # gesendet bei 2 min, Tessie hängt
        now[0] = T0 + timedelta(minutes=16)
        release.set()
        await asyncio.sleep(0.01)  # der Fehler geschieht bei 16 min: der erste (0 min) ist raus
        assert car.capabilities(snap(HOME), now[0]).controllable
        assert car.alarms(now[0]) == []


async def test_the_count_starts_afresh_after_the_pause(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1, 2)  # gesperrt bis 17 min
        await fail_at(car, now, 18, 19)  # nach der Pause zwei neue Fehler
        assert car.capabilities(snap(HOME), T0 + timedelta(minutes=19)).controllable
        await fail_at(car, now, 20)
        caps = car.capabilities(snap(HOME), T0 + timedelta(minutes=21))
        assert caps.reasons == ("Nach 3 Fehlern in 15 min bis 12:35 nicht steuerbar.",)


# Alarme


def test_no_alarm_without_failures(car) -> None:
    assert car.alarms(T0) == []


async def test_the_alarm_lasts_until_the_deadline(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        await fail_at(car, now, 0, 1, 2)
        alarm = Alarm(
            "actuator.unavailable.vehicle.tesla",
            Severity.WARNING,
            "Tesla Model 3 ist nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar.",
            T0 + timedelta(minutes=2),  # seit dem dritten Fehler
        )
        assert car.alarms(T0 + timedelta(minutes=3)) == [alarm]
        assert car.alarms(T0 + timedelta(minutes=17) - timedelta(microseconds=1)) == [alarm]
        assert car.alarms(T0 + timedelta(minutes=17)) == []


async def test_the_alarm_names_the_vehicle_by_its_display_name(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    vehicle = cfg.vehicles["tesla"].model_copy(update={"name": "Mein Tesla"})
    async with httpx.AsyncClient() as client:
        car = TeslaActuator(
            "tesla", vehicle, SecretStr(TOKEN), client, lambda: True, clock=lambda: now[0]
        )
        await fail_at(car, now, 0, 1, 2)
        assert [a.message for a in car.alarms(T0 + timedelta(minutes=3))] == [
            "Mein Tesla ist nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar."
        ]


# Abbruch


async def test_a_cancelled_command_fails_but_is_not_counted(cfg, respx_mock) -> None:
    async def shutdown(request: httpx.Request) -> httpx.Response:
        task = asyncio.current_task()
        assert task is not None
        task.cancel()  # wie beim Herunterfahren: die Aufgabe des Befehls wird abgebrochen
        await asyncio.sleep(0)
        raise AssertionError("der abgebrochene Befehl läuft nicht weiter")

    respx_mock.post(URL).mock(side_effect=shutdown)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        for minute in range(FAILURES_ALLOWED):
            await fail_at(car, now, minute)
            assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
                Confirmation.FAILED,
                CANCELLED,
            )
        later = T0 + timedelta(minutes=3)
        assert car.capabilities(snap(HOME), later) == Capabilities(True)
        assert car.alarms(later) == []


async def test_a_command_cancelled_before_it_starts_sends_nothing(cfg, respx_mock) -> None:
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(CommandKind.CURRENT, 10.0) is None
        (task,) = asyncio.all_tasks() - {asyncio.current_task()}  # die Aufgabe des Befehls
        task.cancel()
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (Confirmation.FAILED, CANCELLED)
        assert car.capabilities(snap(HOME), T0).controllable
    assert respx_mock.calls.call_count == 0


# Frist je Befehl


async def test_a_command_that_never_answers_fails_at_the_total_timeout(
    cfg, respx_mock, monkeypatch
) -> None:
    monkeypatch.setattr("bindaems.core.actuators.tesla.COMMAND_TIMEOUT_S", 0.02)
    never = asyncio.Event()
    requests: list[httpx.Request] = []

    async def hang(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await never.wait()  # Tessie antwortet nie
        return httpx.Response(200, json={"result": True})

    respx_mock.post(URL).mock(side_effect=hang)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        for minute in range(FAILURES_ALLOWED):
            now[0] = T0 + timedelta(minutes=minute)
            assert car.send(CommandKind.CURRENT, 10.0) is None  # der letzte Befehl ist zu Ende
            await asyncio.sleep(0.1)  # die Frist ist 0,02 s
            assert asyncio.all_tasks() == {asyncio.current_task()}  # die Aufgabe ist fertig
            assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
                Confirmation.FAILED,
                "TimeoutError",
            )
            # Jede Zeitüberschreitung zählt als ein Fehler: nicht steuerbar erst nach dem dritten.
            assert car.capabilities(snap(HOME), now[0]).controllable == (
                minute < FAILURES_ALLOWED - 1
            )
        caps = car.capabilities(snap(HOME), T0 + timedelta(minutes=3))
    assert caps.reasons == (AFTER_FAILURES,)
    assert len(requests) == FAILURES_ALLOWED


async def test_a_command_cancelled_while_waiting_is_no_timeout(cfg, respx_mock) -> None:
    never = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        await never.wait()
        return httpx.Response(200, json={"result": True})

    respx_mock.post(URL).mock(side_effect=hang)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        for minute in range(FAILURES_ALLOWED):
            now[0] = T0 + timedelta(minutes=minute)
            assert car.send(CommandKind.CURRENT, 10.0) is None
            await asyncio.sleep(0)  # Tessie hat noch nicht geantwortet
            (task,) = asyncio.all_tasks() - {asyncio.current_task()}
            task.cancel()  # von außen, wie beim Herunterfahren; die Frist läuft noch
            await asyncio.sleep(0.01)
            assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
                Confirmation.FAILED,
                CANCELLED,
            )
        later = T0 + timedelta(minutes=3)
        assert car.capabilities(snap(HOME), later) == Capabilities(True)
        assert car.alarms(later) == []


# Mit dem Ausführer

TESLA = "vehicle.tesla"
SET_CURRENT = Command(TESLA, CommandKind.CURRENT, 10.0)
SAFE_CURRENT = Command(TESLA, CommandKind.CURRENT, 5.0, urgent=True, reasons=("safe_state",))
LIVE = make_ctx(present={(TESLA, CommandKind.CURRENT): 16.0})


def executor_for(car: TeslaActuator, limits, tmp_path) -> tuple[CommandExecutor, list[Decision]]:
    """Ein Ausführer mit der echten Safety-Schicht und dem Tesla als einzigem Aktor."""
    decisions: list[Decision] = []
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "b.json", T0))
    executor = CommandExecutor(layer, {TESLA: car}, decisions.append, {TESLA: "Tesla Model 3"})
    return executor, decisions


async def test_the_executor_sends_at_once_and_confirms_later(
    cfg, respx_mock, limits_live, tmp_path
) -> None:
    respx_mock.post(URL).respond(json={"result": True})
    async with httpx.AsyncClient() as client:
        executor, decisions = executor_for(tesla(cfg, client, [T0]), limits_live, tmp_path)
        verdict = executor.execute(SET_CURRENT, LIVE, snap(HOME), T0, {})
        assert verdict is not None and verdict.send
        assert executor.pending() == {(TESLA, CommandKind.CURRENT)} and decisions == []
        await asyncio.sleep(0.01)
        results = executor.poll(snap(HOME), T0 + timedelta(seconds=2), T0)
    assert [r.result for r in results] == [DecisionResult.OK]
    assert [d.text for d in decisions] == ["Tesla Model 3: Strom 10 A – gesendet und bestätigt."]


async def test_the_executor_logs_a_failed_command_with_the_reason(
    cfg, respx_mock, limits_live, tmp_path
) -> None:
    respx_mock.post(URL).respond(500)
    async with httpx.AsyncClient() as client:
        executor, decisions = executor_for(tesla(cfg, client, [T0]), limits_live, tmp_path)
        executor.execute(SET_CURRENT, LIVE, snap(HOME), T0, {})
        await asyncio.sleep(0.01)
        results = executor.poll(snap(HOME), T0 + timedelta(seconds=2), T0)
    assert [r.result for r in results] == [DecisionResult.ERROR]
    assert [d.text for d in decisions] == [
        "Tesla Model 3: Strom 10 A – Fehler: Tessie antwortet mit HTTP 500."
    ]
    assert [f.result for f in executor.take_failures()] == [DecisionResult.ERROR]


async def test_the_executor_sends_nothing_to_a_sleeping_car(
    cfg, respx_mock, limits_live, tmp_path
) -> None:
    async with httpx.AsyncClient() as client:
        executor, decisions = executor_for(tesla(cfg, client, [T0]), limits_live, tmp_path)
        asleep = snap(HOME | {"vehicle.tesla.online": False})
        executor.execute(SET_CURRENT, LIVE, asleep, T0, {})
        await asyncio.sleep(0.01)
    assert [d.result for d in decisions] == [DecisionResult.NOT_SENT]
    assert decisions[0].text == (
        "Tesla Model 3: Strom 10 A – nicht gesendet: nicht steuerbar: "
        "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
    )
    assert executor.pending() == frozenset() and respx_mock.calls.call_count == 0


async def test_a_hanging_tessie_never_blocks_and_times_out_at_the_deadline(
    cfg, respx_mock, limits_live, tmp_path
) -> None:
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    route = respx_mock.post(URL).mock(side_effect=answer)
    async with httpx.AsyncClient() as client:
        executor, decisions = executor_for(tesla(cfg, client, [T0]), limits_live, tmp_path)
        executor.execute(SET_CURRENT, LIVE, snap(HOME), T0, {})  # kehrt sofort zurück
        await asyncio.sleep(0)
        assert executor.poll(snap(HOME), T0 + timedelta(seconds=74), T0) == []
        results = executor.poll(snap(HOME), T0 + timedelta(seconds=75), T0)
        assert [r.result for r in results] == [DecisionResult.TIMEOUT]
        # Tessie antwortet noch nicht: Die Art ist belegt, ein weiterer Befehl wird nicht gesendet.
        later = replace(LIVE, now=T0 + timedelta(seconds=80))
        executor.execute(SAFE_CURRENT, later, snap(HOME), T0, {})
        assert decisions[-1].result == DecisionResult.NOT_SENT
        assert decisions[-1].text.startswith(
            "Tesla Model 3 (sicherer Zustand): Strom 5 A – nicht gesendet: "
            "Befehl an Tessie läuft noch."
        )
        release.set()
        await asyncio.sleep(0.01)
    assert route.call_count == 1


# Der Weg über den Tessie-Adapter


async def tessie_cache(cfg, respx_mock, state: Any) -> Snapshot:
    """Der Zustandsspeicher nach einer Abfrage des echten Adapters; Tessie liefert ``state``."""
    respx_mock.get(STATE_URL).respond(json=state)
    store = StateStore(ManualClock(T0))
    seen: list[Snapshot] = []

    def polled() -> bool:
        if store.connected("tessie:tesla") is not True:
            return False
        seen.append(store.snapshot())  # der Adapter trennt die Quelle beim Abbruch wieder
        return True

    async with httpx.AsyncClient() as client:
        adapter = TessieAdapter(
            "tesla", cfg.vehicles["tesla"], cfg.site.location, SecretStr(TOKEN), store, client
        )
        await run_until(adapter, polled)
    return seen[0]


def changed(**sections: dict[str, Any]) -> dict[str, Any]:
    """Die Antwort von Tessie mit geänderten Feldern je Abschnitt (``top`` = oberste Ebene)."""
    state = json.loads(json.dumps(STATE))
    for section, fields in sections.items():
        (state if section == "top" else state[section]).update(fields)
    return state


async def test_the_cache_of_the_tessie_adapter_makes_the_car_controllable(
    cfg, respx_mock, car
) -> None:
    cache = await tessie_cache(cfg, respx_mock, STATE)
    assert car.capabilities(cache, T0) == Capabilities(True)
    assert car.present(cache) == {CommandKind.CURRENT: 16, CommandKind.CHARGING: True}
    assert all(call.request.method == "GET" for call in respx_mock.calls)  # nur gelesen


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (changed(top={"state": "asleep"}), ASLEEP),
        (changed(top={"state": "offline"}), ASLEEP),
        (changed(drive_state={"latitude": 48.3}), AWAY),  # rund 11 km vom Haus
        (changed(charge_state={"charging_state": "Disconnected"}), UNPLUGGED),
    ],
    ids=["asleep", "offline", "away", "unplugged"],
)
async def test_each_reason_follows_the_tessie_cache(cfg, respx_mock, car, state, reason) -> None:
    cache = await tessie_cache(cfg, respx_mock, state)
    assert car.capabilities(cache, T0) == Capabilities(False, (reason,))


async def test_a_stopped_car_is_not_charging_in_the_tessie_cache(cfg, respx_mock, car) -> None:
    cache = await tessie_cache(cfg, respx_mock, changed(charge_state={"charging_state": "Stopped"}))
    assert car.present(cache)[CommandKind.CHARGING] is False
    assert car.capabilities(cache, T0) == Capabilities(True)  # angesteckt und online


async def test_a_missing_value_in_the_tessie_cache_is_unknown(cfg, respx_mock, car) -> None:
    state = changed()
    del state["charge_state"]["charge_amps"], state["charge_state"]["charging_state"]
    cache = await tessie_cache(cfg, respx_mock, state)
    assert car.present(cache) == {}
