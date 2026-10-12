"""Tests der Teil-2-Sitzung: prüfen, senden, bestätigen, wiederherstellen und der Aufbau."""

import asyncio
import time
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr
from tests.unit.tools.part2.conftest import FakeEvcs

from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.actuators.victron import VictronActuator
from bindaems.core.adapters.base import AdapterHealth
from bindaems.core.adapters.evcs import EvcsAdapter
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import VictronMqttAdapter
from bindaems.core.control.actuator import Actuator, Capabilities, Confirmation
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import Alarm, SignalKind, Snapshot
from bindaems.shared.timeutil import SystemClock
from bindaems.tools.part2.session import Part2Session, build_session, minutes, seconds
from bindaems.tools.verify_checks import Finding

CURRENT, CHARGING = CommandKind.CURRENT, CommandKind.CHARGING


def currents(evcs: FakeEvcs) -> list[float | int | None]:
    return [v for p, v in evcs.writes if p.endswith("SetCurrent")]


# Zeiten in Texten


@pytest.mark.parametrize(
    ("value", "text"), [(0.04, "0"), (1.5, "1,5"), (12.34, "12,3"), (75.0, "75"), (0.25, "0,2")]
)
def test_seconds_have_one_decimal_and_a_comma(value: float, text: str) -> None:
    assert seconds(value) == text


@pytest.mark.parametrize(
    ("value", "text"), [(600.0, "10"), (150.0, "2,5"), (60.0, "1"), (90.0, "1,5"), (0.3, "0")]
)
def test_minutes_have_one_decimal_and_a_comma(value: float, text: str) -> None:
    assert minutes(value) == text


# Senden und Bestätigen


async def test_confirmed_write_returns_the_seconds_since_sending(evcs_session) -> None:
    session, evcs = evcs_session()
    waited = await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    assert waited is not None and 0 <= waited < 0.5
    assert session.failure is None
    assert currents(evcs) == [10]
    assert session.snapshot().ok("wallbox.evcs.gx_set_current_a") == 10


async def test_not_controllable_sends_nothing(evcs_session) -> None:
    # Die Sitzung wartet bis zur Frist, ob der Aktor steuerbar wird, und sendet bis dahin nichts.
    session, evcs = evcs_session(state={"wallbox.evcs.gx_mode": 1})
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.1) is None
    assert session.failure == "nicht steuerbar: EVCS nicht im manuellen Modus."
    assert evcs.writes == []
    assert await session.restore() == []  # nichts geschrieben, nichts herzustellen


async def test_refused_send_is_not_confirmed_and_not_remembered(evcs_session) -> None:
    session, evcs = evcs_session(accept=False)
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.1) is None
    assert session.failure == "nicht gesendet: Cerbo-Verbindung: Auftrag nicht angenommen."
    evcs.accept = True
    assert await session.restore() == []


async def test_unconfirmed_write_names_the_time(evcs_session) -> None:
    session, _ = evcs_session(follow=False)
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.05) is None
    assert session.failure == "nicht bestätigt nach 0,05 s"


async def test_failed_confirmation_ends_the_wait(cfg: Config) -> None:
    # Ein Tesla, dessen Befehl Tessie ablehnt: sofort ein Ergebnis, nicht erst nach der Frist.
    car = StubTesla(Confirmation.FAILED)
    session = Part2Session(
        cfg,
        store=StateStore(SystemClock()),
        adapters=[],
        actuators={car.id: car},
        ask=lambda _question: False,
        poll_s=0.01,
    )
    started = time.monotonic()
    assert await session.set_and_confirm("vehicle.tesla", CURRENT, 10.0, 5.0) is None
    assert time.monotonic() - started < 1.0
    assert session.failure == "Fehler: Tessie meldet result=false."
    restored = await session.restore(timeout_s=5.0)
    assert restored == [
        Finding(
            "17.2-7", "Wiederherstellen: Strom 16 A", "fail", "Fehler: Tessie meldet result=false."
        )
    ]
    assert car.sent == [(CURRENT, 10.0), (CURRENT, 16.0)]


async def test_unknown_original_is_not_written(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_set_current_a": None})
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.1) is None
    assert session.failure == (
        "nicht gesendet: Ausgangswert unbekannt; er ließe sich nicht wiederherstellen."
    )
    assert evcs.writes == []


@pytest.mark.parametrize(
    ("actuator", "kind", "value"),
    [
        ("wallbox.evcs", CURRENT, 32.0),  # über dem Höchststrom: würde geklemmt
        ("wallbox.evcs", CURRENT, 10.5),  # würde auf ganze Ampere abgerundet
        ("wallbox.evcs", CURRENT, 5.0),  # unter dem Mindeststrom: würde abgelehnt
        ("wallbox.evcs", CHARGING, 1.0),  # kein Wahrheitswert
        ("wallbox.evcs", CommandKind.MIN_SOC, 30.0),  # keine Art der EVCS
        ("wallbox.nirgends", CURRENT, 10.0),  # kein Aktor der Sitzung
    ],
)
async def test_tool_errors_raise_before_anything_is_sent(
    evcs_session, actuator: str, kind: CommandKind, value: CommandValue
) -> None:
    session, evcs = evcs_session()
    with pytest.raises(ValueError):
        await session.set_and_confirm(actuator, kind, value, 0.5)
    assert evcs.writes == []


# Wiederherstellen


async def test_restore_writes_the_original_found_before_the_first_write(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 6.0, 0.5)
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    restored = await session.restore()
    assert [(f.item, f.title, f.status) for f in restored] == [
        ("17.2-6", "Wiederherstellen: Strom 16 A", "ok")
    ]
    assert restored[0].detail.startswith("Wiederhergestellt nach ")
    assert currents(evcs) == [6, 10, 16]


async def test_restore_runs_in_reverse_order(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_start_stop": 1})
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    await session.set_and_confirm("wallbox.evcs", CHARGING, False, 0.5)
    restored = await session.restore()
    assert [f.title for f in restored] == [
        "Wiederherstellen: Laden an",
        "Wiederherstellen: Strom 16 A",
    ]
    assert evcs.writes[-2:] == [("evcharger/40/StartStop", 1), ("evcharger/40/SetCurrent", 16)]


async def test_restore_does_not_write_an_original_outside_the_limits(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_set_current_a": 32})
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    assert await session.restore() == [
        Finding(
            "17.2-6",
            "Wiederherstellen: Strom 32 A",
            "fail",
            "Ausgangswert außerhalb der Grenzen, nicht geschrieben: "
            "Auf den Höchstwert 16 A begrenzt.",
        )
    ]
    assert await session.restore() == []  # einmal gemeldet genügt
    assert currents(evcs) == [10]


async def test_unconfirmed_restore_is_an_error_not_an_exception(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    evcs.follow = False
    assert await session.restore(timeout_s=0.1) == [
        Finding("17.2-6", "Wiederherstellen: Strom 16 A", "fail", "nicht bestätigt nach 0,1 s")
    ]


async def test_each_restored_value_gets_its_own_deadline(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_start_stop": 1})
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    await session.set_and_confirm("wallbox.evcs", CHARGING, False, 0.5)
    evcs.follow = False
    restored = await session.restore(timeout_s=0.1)
    assert [(f.title, f.detail) for f in restored] == [
        ("Wiederherstellen: Laden an", "nicht bestätigt nach 0,1 s"),
        ("Wiederherstellen: Strom 16 A", "nicht bestätigt nach 0,1 s"),
    ]
    assert evcs.writes[-2:] == [("evcharger/40/StartStop", 1), ("evcharger/40/SetCurrent", 16)]


async def test_restore_sends_nothing_to_an_actuator_that_is_not_controllable(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    evcs.store.update("wallbox.evcs.gx_mode", 1, source="victron", kind=SignalKind.STATE)
    assert await session.restore(timeout_s=0.1) == [
        Finding(
            "17.2-6",
            "Wiederherstellen: Strom 16 A",
            "fail",
            "nicht steuerbar: EVCS nicht im manuellen Modus.",
        )
    ]
    assert currents(evcs) == [10]


async def test_restore_reports_an_exception_as_an_error(evcs_session) -> None:
    session, _ = evcs_session(state={"wallbox.evcs.gx_start_stop": 1})
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    await session.set_and_confirm("wallbox.evcs", CHARGING, False, 0.5)

    def broken(kind: CommandKind, value: CommandValue) -> str | None:
        if kind == CHARGING:
            raise ValueError("Schreibpfad nicht freigegeben: evcharger/40/StartStop")
        return None

    session.actuators["wallbox.evcs"].send = broken  # type: ignore[method-assign]
    restored = await session.restore(timeout_s=0.1)
    assert restored[0] == Finding(
        "17.2-6",
        "Wiederherstellen: Laden an",
        "fail",
        "ValueError: Schreibpfad nicht freigegeben: evcharger/40/StartStop",
    )
    assert (
        restored[1].title == "Wiederherstellen: Strom 16 A"
    )  # der nächste Wert kommt trotzdem dran


async def test_value_back_at_its_original_is_not_written_again(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    await session.set_and_confirm("wallbox.evcs", CURRENT, 16.0, 0.5)  # zurück auf 16 A
    assert await session.restore() == []
    assert currents(evcs) == [10, 16]


async def test_restore_trusts_the_last_write_more_than_a_lagging_readback(cfg: Config) -> None:
    # Der Tessie-Cache zeigt noch die vorgefundenen 16 A, gesendet sind 5 A: wiederherstellen.
    car = StubTesla(Confirmation.CONFIRMED)
    session = Part2Session(
        cfg,
        store=StateStore(SystemClock()),
        adapters=[],
        actuators={car.id: car},
        ask=lambda _question: False,
        poll_s=0.01,
    )
    assert await session.set_and_confirm("vehicle.tesla", CURRENT, 5.0, 1.0) is not None
    restored = await session.restore(timeout_s=1.0)
    assert [(f.item, f.title, f.status) for f in restored] == [
        ("17.2-7", "Wiederherstellen: Strom 16 A", "ok")
    ]
    assert car.sent == [(CURRENT, 5.0), (CURRENT, 16.0)]


def car_session(cfg: Config, car: Actuator) -> Part2Session:
    """Eine Sitzung mit nur diesem Tesla."""
    return Part2Session(
        cfg,
        store=StateStore(SystemClock()),
        adapters=[],
        actuators={car.id: car},
        ask=lambda _question: False,
        poll_s=0.01,
    )


@pytest.mark.parametrize(("on_fails", "status"), [(1, "ok"), (2, "fail")])
async def test_restore_sends_a_value_again_whose_last_write_failed(
    cfg: Config, on_fails: int, status: str
) -> None:
    # Tessie bestätigt „aus“ und lehnt „an“ ab. Der Cache zeigt noch „lädt“, und zuletzt gesendet
    # wurde „an“, also der vorgefundene Wert. Bestätigt aber ist nur „aus“: Das Laden steht nicht
    # wieder, und das Wiederherstellen sendet „an“ genau einmal noch.
    car = LaggingCar(on_fails)
    session = car_session(cfg, car)
    assert await session.set_and_confirm("vehicle.tesla", CHARGING, False, 1.0) is not None
    assert await session.set_and_confirm("vehicle.tesla", CHARGING, True, 1.0) is None
    rows = await session.restore_after_step(1.0)
    assert [(f.title, f.status) for f in rows] == [("Wiederherstellen: Laden an", status)]
    assert car.sent == [(CHARGING, False), (CHARGING, True), (CHARGING, True)]


async def test_restore_does_not_repeat_a_failed_write_that_changed_nothing(cfg: Config) -> None:
    # Gescheitert ist der einzige Schreibvorgang, und er galt dem vorgefundenen Wert: Nichts wurde
    # bestätigt, also ist auch nichts zu ändern.
    car = LaggingCar(on_fails=1)
    session = car_session(cfg, car)
    assert await session.set_and_confirm("vehicle.tesla", CHARGING, True, 1.0) is None
    assert await session.restore_after_step(1.0) == []
    assert car.sent == [(CHARGING, True)]


async def test_restore_sends_a_value_again_when_the_last_write_of_it_was_not_confirmed(
    evcs_session,
) -> None:
    # „aus“ ist bestätigt, „an“ kam nie zurück. Zeigt das Rücklesen später wieder „an“ (die EVCS
    # übernahm es spät, oder der Wert ist veraltet), zählt der letzte bestätigte Wert: Das
    # Wiederherstellen sendet „an“ noch einmal.
    session, evcs = evcs_session(state={"wallbox.evcs.gx_start_stop": 1})
    assert await session.set_and_confirm("wallbox.evcs", CHARGING, False, 0.5) is not None
    evcs.follow = False
    assert await session.set_and_confirm("wallbox.evcs", CHARGING, True, 0.05) is None
    evcs.store.update("wallbox.evcs.gx_start_stop", 1, source="victron", kind=SignalKind.STATE)
    evcs.follow = True
    rows = await session.restore(timeout_s=0.5)
    assert [(f.title, f.status) for f in rows] == [("Wiederherstellen: Laden an", "ok")]
    assert [v for p, v in evcs.writes if p.endswith("StartStop")] == [0, 1, 1]


async def test_restore_after_step_keeps_the_rows_in_the_findings(evcs_session) -> None:
    session, _ = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    rows = await session.restore_after_step(1.0)
    assert [(f.title, f.status) for f in rows] == [("Wiederherstellen: Strom 16 A", "ok")]
    assert session.findings == rows
    assert await session.restore_after_step(1.0) == []  # steht wieder: nichts zu tun
    assert session.findings == rows


# Hartnäckig innerhalb der Frist: warten, bis steuerbar, senden, ohne Bestätigung erneut senden


async def test_write_waits_until_the_actuator_is_controllable(evcs_session) -> None:
    session, evcs = evcs_session()
    evcs.connected = False  # die Sitzung zum Cerbo ist gerade weg
    asyncio.get_running_loop().call_later(0.1, setattr, evcs, "connected", True)
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 1.0) is not None
    assert currents(evcs) == [10]  # nichts, solange nicht steuerbar


async def test_refused_write_is_sent_again_until_accepted(evcs_session) -> None:
    session, evcs = evcs_session(accept=False)  # zwischen zwei Sitzungen zum Cerbo
    asyncio.get_running_loop().call_later(0.1, setattr, evcs, "accept", True)
    assert await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 1.0) is not None
    assert currents(evcs) == [10]


async def test_lost_write_is_sent_again(evcs_session) -> None:
    session, evcs = evcs_session(resend_s=0.05)
    evcs.lose = 1  # in der Warteschlange einer Sitzung, die gerade endet
    waited = await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 1.0)
    assert waited is not None and waited >= 0.05  # gezählt ab dem ersten Senden
    assert currents(evcs) == [10, 10]


async def test_restore_waits_until_the_actuator_is_controllable(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    evcs.connected = False
    asyncio.get_running_loop().call_later(0.1, setattr, evcs, "connected", True)
    restored = await session.restore(timeout_s=1.0)
    assert [(f.title, f.status) for f in restored] == [("Wiederherstellen: Strom 16 A", "ok")]
    assert currents(evcs) == [10, 16]


async def test_restore_sends_again_after_a_refusal(evcs_session) -> None:
    session, evcs = evcs_session()
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    evcs.accept = False
    asyncio.get_running_loop().call_later(0.1, setattr, evcs, "accept", True)
    restored = await session.restore(timeout_s=1.0)
    assert [(f.title, f.status) for f in restored] == [("Wiederherstellen: Strom 16 A", "ok")]
    assert currents(evcs) == [10, 16]


async def test_restore_sends_again_when_a_write_is_lost(evcs_session) -> None:
    session, evcs = evcs_session(resend_s=0.05)
    await session.set_and_confirm("wallbox.evcs", CURRENT, 10.0, 0.5)
    evcs.lose = 1
    restored = await session.restore(timeout_s=1.0)
    assert [(f.title, f.status) for f in restored] == [("Wiederherstellen: Strom 16 A", "ok")]
    assert currents(evcs) == [10, 16, 16]


TESSIE_LATENCY_S = 0.12  # Tessie antwortet erst nach dem Befehl, hier länger als resend_s
AMPS_PATH = "/5YJ3E7EB0MF000000/command/set_charging_amps"


def tesla_session(cfg: Config, result: bool, requests: list[httpx.Request]) -> Part2Session:
    """Eine Sitzung mit dem echten Tesla-Aktor: zu Hause, angesteckt, online, 16 A.

    Tessie antwortet jedem Befehl nach ``TESSIE_LATENCY_S`` mit ``result``; ``requests`` sammelt
    die Befehle. Erneut senden würde die Sitzung schon nach 0,05 s.
    """

    async def tessie(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(TESSIE_LATENCY_S)
        return httpx.Response(200, json={"result": result})

    store = StateStore(SystemClock())
    store.register_source("tessie:tesla", 900.0)
    store.set_connected("tessie:tesla", True)
    state = {"online": True, "at_home": True, "plugged": True, "charge_amps": 16}
    for name, value in (state | {"charging_state": "Charging"}).items():
        store.update(
            f"vehicle.tesla.{name}", value, source="tessie:tesla", kind=SignalKind.MEASUREMENT
        )
    client = httpx.AsyncClient(transport=httpx.MockTransport(tessie))
    car = TeslaActuator(
        "tesla",
        cfg.vehicles["tesla"],
        SecretStr("tessie-token"),
        client,
        lambda: True,
        clock=SystemClock().now,
    )
    return Part2Session(
        cfg,
        store=store,
        adapters=[],
        actuators={car.id: car},
        ask=lambda _question: False,
        poll_s=0.01,
        resend_s=0.05,
        cleanup=[client.aclose],
    )


async def test_a_tesla_command_is_sent_once_and_its_result_counts(cfg: Config) -> None:
    # Läuft der Befehl länger als resend_s, darf weder ein zweiter starten noch das Ergebnis
    # des ersten verloren gehen.
    requests: list[httpx.Request] = []
    session = tesla_session(cfg, True, requests)
    try:
        waited = await session.set_and_confirm("vehicle.tesla", CURRENT, 10.0, 1.0)
    finally:
        await session.stop()
    assert waited is not None and waited >= TESSIE_LATENCY_S - 0.01
    assert [request.url.path for request in requests] == [AMPS_PATH]


async def test_a_failed_tesla_command_ends_the_wait_at_once(cfg: Config) -> None:
    requests: list[httpx.Request] = []
    session = tesla_session(cfg, False, requests)
    started = time.monotonic()
    try:
        assert await session.set_and_confirm("vehicle.tesla", CURRENT, 10.0, 1.0) is None
    finally:
        await session.stop()
    assert time.monotonic() - started < 0.5
    assert session.failure == "Fehler: Tessie meldet result=false."
    assert [request.url.path for request in requests] == [AMPS_PATH]


async def test_persistent_settings_are_not_sent_again(cfg: Config) -> None:
    # Entladegrenze und Min-SOC sind gespeicherte Einstellungen: je Aufruf höchstens ein
    # Schreiben, auch ohne Bestätigung. Den flüchtigen Override sendet die Sitzung erneut.
    store = StateStore(SystemClock())
    store.register_source("victron", 5.0)
    store.set_connected("victron", True)
    store.update("ess.max_discharge_w", 5000.0, source="victron", kind=SignalKind.STATE)
    store.update("ess.override.setpoint", None, source="victron", kind=SignalKind.STATE)
    writes: list[tuple[str, float | int | None]] = []

    def write(path: str, value: float | int | None) -> bool:  # angenommen, nie übernommen
        writes.append((path, value))
        return True

    victron = VictronActuator(write, lambda: True)
    session = Part2Session(
        cfg,
        store=store,
        adapters=[],
        actuators={victron.id: victron},
        ask=lambda _question: False,
        poll_s=0.01,
        resend_s=0.05,
    )
    assert await session.set_and_confirm("victron", CommandKind.MAX_DISCHARGE, 0.0, 0.3) is None
    assert writes == [("settings/0/Settings/CGwacs/MaxDischargePower", 0.0)]
    assert await session.set_and_confirm("victron", CommandKind.GRID_SETPOINT, 1000.0, 0.3) is None
    assert len([path for path, _ in writes if path == "hub4/0/Overrides/Setpoint"]) > 1


# Warten, Festhalten, Start und Stopp


async def test_wait_for_returns_the_seconds_or_none(evcs_session) -> None:
    session, evcs = evcs_session()
    asyncio.get_running_loop().call_later(0.05, evcs.change_by_hand, 10)

    def changed(snap: Snapshot) -> bool:
        return snap.ok("wallbox.evcs.gx_set_current_a") == 10

    waited = await session.wait_for(changed, 1.0)
    assert waited is not None and 0.04 <= waited < 1.0
    assert await session.wait_for(lambda _snap: False, 0.05) is None


def test_record_keeps_and_shows_the_finding(
    evcs_session, capsys: pytest.CaptureFixture[str]
) -> None:
    session, _ = evcs_session()
    finding = Finding("17.2-6", "Strom 6 A", "ok", "bestätigt nach 1,5 s")
    session.record(finding)
    assert session.findings == [finding]
    assert "17.2-6 Strom 6 A: OK – bestätigt nach 1,5 s" in capsys.readouterr().out


async def test_start_runs_the_adapters_until_stop(cfg: Config) -> None:
    adapter = WaitingAdapter()
    closed: list[str] = []

    async def close() -> None:
        closed.append("http")

    session = Part2Session(
        cfg,
        store=StateStore(SystemClock()),
        adapters=[adapter],
        actuators={},
        ask=lambda _question: False,
        cleanup=[close],
    )
    await session.start()
    await asyncio.wait_for(adapter.running.wait(), 1.0)
    await session.stop()
    assert adapter.cancelled and closed == ["http"]
    await session.stop()  # ein zweiter Aufruf schadet nicht
    assert closed == ["http"]


# Aufbau mit den echten Adaptern und Aktoren


async def test_build_session_uses_the_adapters_and_actuators_of_the_core(cfg: Config) -> None:
    secrets = Secrets(internal_token=SecretStr("x" * 32), tessie_token=SecretStr("tessie-token"))
    session = build_session(cfg, secrets, ask=lambda _question: False)
    try:
        assert session.cfg is cfg
        kinds = [type(adapter) for adapter in session.adapters]
        assert kinds == [VictronMqttAdapter, EvcsAdapter, TwcAdapter, TessieAdapter]
        assert [adapter.name for adapter in session.adapters] == ["victron", "evcs", "twc", "tesla"]
        assert list(session.actuators) == ["victron", "wallbox.evcs", "vehicle.tesla"]
        assert isinstance(session.actuators["vehicle.tesla"], TeslaActuator)
        # Die EVCS schreibt über den Victron-Adapter an Instanz 40; ohne MQTT-Sitzung nimmt er
        # keinen Auftrag an.
        evcs = session.actuators["wallbox.evcs"]
        assert evcs.send(CURRENT, 10.0) == "Cerbo-Verbindung: Auftrag nicht angenommen."
    finally:
        await session.stop()


async def test_build_session_without_tessie_token_reads_no_vehicle(cfg: Config) -> None:
    session = build_session(cfg, Secrets(internal_token=SecretStr("x" * 32)), ask=lambda _q: False)
    try:
        assert not any(isinstance(adapter, TessieAdapter) for adapter in session.adapters)
        car = session.actuators["vehicle.tesla"]
        snap = session.snapshot()
        assert car.capabilities(snap, snap.ts).reasons[0] == "Tessie nicht erreichbar."
    finally:
        await session.stop()


class StubTesla:
    """Ein steuerbarer Tesla; der Cache zeigt immer 16 A, jeder Befehl endet mit ``answer``.

    ``FAILED`` steht für ``result: false`` von Tessie, ``CONFIRMED`` für ``result: true``.
    """

    id = "vehicle.tesla"
    confirm_timeout_s = 75.0

    def __init__(self, answer: Confirmation) -> None:
        self.answer = answer
        self.sent: list[tuple[CommandKind, CommandValue]] = []

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        return Capabilities(True)

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        return {CURRENT: 16.0}

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        self.sent.append((kind, value))
        return None

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        if self.answer == Confirmation.FAILED:
            return Confirmation.FAILED, "Tessie meldet result=false."
        return self.answer, ""

    def alarms(self, now: datetime) -> list[Alarm]:
        return []


class LaggingCar(StubTesla):
    """Ein Tesla, dessen Cache immer „lädt“ zeigt, wie ihn Tessie kurz nach dem Befehl noch zeigt.

    Tessie bestätigt jeden Befehl, nur „an“ (``True``) lehnt es ab, und zwar so oft, wie
    ``on_fails`` sagt.
    """

    def __init__(self, on_fails: int) -> None:
        super().__init__(Confirmation.CONFIRMED)
        self.on_fails = on_fails
        self._refused = False

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        return {CHARGING: True}

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        self._refused = value is True and self.on_fails > 0
        self.on_fails -= 1 if self._refused else 0
        return super().send(kind, value)

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        if self._refused:
            return Confirmation.FAILED, "Tessie meldet result=false."
        return Confirmation.CONFIRMED, ""


class WaitingAdapter:
    """Ein Adapter, der läuft, bis er abgebrochen wird."""

    name = "wartend"

    def __init__(self) -> None:
        self.running = asyncio.Event()
        self.cancelled = False

    async def run(self) -> None:
        self.running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def health(self) -> AdapterHealth:
        return AdapterHealth(name=self.name)
