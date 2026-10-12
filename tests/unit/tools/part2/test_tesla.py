"""Tests des Teils Tesla im Prüfprotokoll Teil 2 (Spec 17.2-7)."""

import asyncio
import json
import re
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from tests.unit.tools.part2.conftest import (
    AMPS_URL,
    START_URL,
    STOP_URL,
    VIN,
    put_signal,
    stray_requests,
    tessie_reports,
)

from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config
from bindaems.shared.timeutil import SystemClock
from bindaems.tools.part2.session import Part2Session
from bindaems.tools.part2.tesla import DEFAULT_TIMINGS, TeslaTimings, run_tesla
from bindaems.tools.verify_checks import Finding

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

FAST = TeslaTimings(confirm_s=0.3, effect_s=0.3, sleep_total_s=0.3, sleep_check_s=0.1)
ROOMY = replace(FAST, confirm_s=1.0)  # Luft für Wartezeiten

# Die Rückfrage und die Texte, die das Werkzeug dem Betreiber zeigt (Texte aus dem Plan)
CHARGING_QUESTION = "Darf der Tesla kurz „Laden aus“ und wieder „an“ schalten?"
ASLEEP = "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
SLEEP_TITLE = "Schlafendes Auto wird nicht geweckt"
SECONDS = r"\d+(,\d)?"  # eine Zeit in Sekunden mit höchstens einer Nachkommastelle
NOT_AT_TWC = r"Wirkung nicht messbar \(nicht am Wall Connector\)"


def step(findings: list[Finding], title: str) -> Finding:
    return next(f for f in findings if f.title == title)


def commands(respx_mock) -> list[str]:
    """Die Befehle an Tessie in der Reihenfolge, in der sie hinausgingen."""
    return [call.request.url.path.rsplit("/", 1)[1] for call in respx_mock.calls]


def amps(respx_mock) -> list[str]:
    """Die Ampere der Strombefehle an Tessie in der Reihenfolge, in der sie hinausgingen."""
    return [
        call.request.url.params["amps"]
        for call in respx_mock.calls
        if call.request.url.path.endswith("set_charging_amps")
    ]


def with_tesla(cfg: Config, **changes: object) -> Config:
    """Die Beispielkonfiguration mit anderen Angaben zum Tesla."""
    vehicle = cfg.vehicles["tesla"].model_copy(update=changes)
    return cfg.model_copy(update={"vehicles": {**cfg.vehicles, "tesla": vehicle}})


# Vorgabe des Plans


async def test_current_change_reaches_the_wall_connector(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], twc_follows=True)  # TWC übernimmt den Strom
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    step = next(f for f in findings if f.title == "Ladestrom 10 A")
    assert (
        step.status == "ok"
        and step.detail.startswith("Tessie ")
        and "Wall Connector" in step.detail
    )


async def test_a_current_the_car_takes_only_the_second_time_is_sent_twice(
    tesla_session, respx_mock
) -> None:
    # An der Anlage (Prüfprotokoll Teil 2 vom 12.10.2026) bestätigte Tessie jeden Strombefehl, das
    # Auto übernahm einen auf oder von 5 A aber erst beim zweiten. Hier übernimmt es jeden Wert
    # erst beim zweiten Mal; der Cache meldet, was das Auto eingestellt hat.
    session, twc = tesla_session(answers=[False], twc_follows=True, check_s=0.5)
    sent: list[str] = []

    def command(request: httpx.Request) -> httpx.Response:
        amps = request.url.params["amps"]
        if sent and sent[-1] == amps:
            put_signal(twc.store, "vehicle.tesla.charge_amps", int(amps))
        sent.append(amps)
        return httpx.Response(200, json={"result": True})

    respx_mock.post(AMPS_URL).mock(side_effect=command)
    tessie_reports(twc.store, every_s=0.05, until_s=10.0)  # der Adapter fragt laufend ab
    findings = await run_tesla(session, session.cfg, "tesla", timings=ROOMY)
    status = {f.title: f.status for f in findings}
    assert status["Ladestrom 10 A"] == "ok" and status["Verhalten bei 5 A"] == "ok"
    assert sent == ["10", "10", "5", "5", "16", "16"]  # je Wert einmal wiederholt
    assert twc.store.snapshot().ok("vehicle.tesla.charge_amps") == 16  # wiederhergestellt


async def test_asleep_stops_before_any_command(tesla_session, respx_mock) -> None:
    stray = stray_requests(respx_mock)  # ohne Route zeichnet respx eine Anfrage nicht auf
    session, _ = tesla_session(state={"vehicle.tesla.online": False})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert (findings[0].status, findings[0].detail) == (
        "fail",
        "Fahrzeug schläft oder ist offline; es wird nicht geweckt.",
    )
    await asyncio.sleep(0.05)  # ein Befehl im Hintergrund hätte jetzt Zeit gehabt
    assert respx_mock.calls.call_count == 0 and not stray.called


async def test_sleep_check_never_sends(tesla_session, respx_mock) -> None:
    stray = stray_requests(respx_mock)
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    tessie_reports(twc.store, every_s=0.05, until_s=2.0)  # Tessie meldet sich während der Prüfung
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    await asyncio.sleep(0.05)
    assert [f.status for f in findings] == ["ok"] and respx_mock.calls.call_count == 0
    assert not stray.called


# Aufruf


def test_the_timings_default_to_the_values_of_the_plan() -> None:
    assert DEFAULT_TIMINGS == TeslaTimings(
        confirm_s=75.0, effect_s=90.0, sleep_total_s=600.0, sleep_check_s=60.0
    )
    # Tessie hat für einen Befehl bis zu 75 s: so lange wartet auch der Teil
    assert DEFAULT_TIMINGS.confirm_s == TeslaActuator.confirm_timeout_s


@pytest.mark.parametrize("key", ["egolf", "nirgends"])
async def test_a_vehicle_without_tessie_is_refused(tesla_session, key: str) -> None:
    session, _ = tesla_session()
    with pytest.raises(ValueError, match=f"Kein Fahrzeug „{key}“ mit Tessie in vehicles"):
        await run_tesla(session, session.cfg, key, timings=FAST)
    assert session.findings == []


# Tesla bereit


async def test_ready_names_soc_current_and_charging_state(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings[0] == Finding(
        "17.2-7", "Tesla bereit", "ok", "SOC 64 %, Ladestrom 16 A, Charging"
    )


async def test_ready_says_what_it_does_not_know(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[False],
        twc_follows=True,
        state={"vehicle.tesla.soc_pct": None, "vehicle.tesla.charging_state": None},
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings[0].detail == "SOC unbekannt, Ladestrom 16 A, Ladezustand unbekannt"


async def test_asleep_names_the_reason_and_tries_nothing(tesla_session) -> None:
    session, _ = tesla_session(state={"vehicle.tesla.online": False})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings == [Finding("17.2-7", "Tesla bereit", "fail", ASLEEP)]
    assert session.actuators["vehicle.tesla"].sent == []  # nicht einmal versucht


async def test_not_ready_names_every_reason_and_asks_nothing(tesla_session) -> None:
    asked: list[str] = []
    session, _ = tesla_session(
        state={"vehicle.tesla.at_home": False, "vehicle.tesla.plugged": False}, asked=asked
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            "Tesla bereit",
            "fail",
            "Fahrzeug nicht zu Hause. Fahrzeug nicht angesteckt.",
        )
    ]
    assert asked == [] and session.actuators["vehicle.tesla"].sent == []


async def test_not_ready_without_a_connection_to_tessie(tesla_session) -> None:
    # Ohne Verbindung gelten alle Werte von Tessie als veraltet: Der Speicher kennt dann nichts.
    session, _ = tesla_session(tessie_connected=False)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            "Tesla bereit",
            "fail",
            f"Tessie nicht erreichbar. {ASLEEP} Fahrzeug nicht zu Hause. "
            "Fahrzeug nicht angesteckt.",
        )
    ]
    assert session.actuators["vehicle.tesla"].sent == []


@pytest.mark.parametrize(
    ("amps", "problem"),
    [
        (16, "Auf den Höchstwert 13 A begrenzt."),
        (3, "Unter dem Mindeststrom 5 A; zum Abschalten das Laden beenden."),
    ],
)
async def test_a_found_current_outside_the_limits_is_not_ready(
    tesla_session, respx_mock, cfg: Config, amps: int, problem: str
) -> None:
    # Ein Fahrzeug, das in der App auf 16 A steht, während `max_a` 13 A ist: Das Werkzeug stellte
    # 10 A und 5 A ein und könnte die 16 A nicht zurückstellen. Es schreibt gar nicht erst.
    stray = stray_requests(respx_mock)
    asked: list[str] = []
    session, _ = tesla_session(
        config=with_tesla(cfg, max_a=13), state={"vehicle.tesla.charge_amps": amps}, asked=asked
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            "Tesla bereit",
            "fail",
            f"Ausgangswert Strom {amps} A außerhalb der Grenzen; er ließe sich nicht "
            f"wiederherstellen: {problem}",
        )
    ]
    assert asked == [] and session.actuators["vehicle.tesla"].sent == [] and not stray.called


async def test_unknown_current_is_not_ready(tesla_session) -> None:
    # Ohne vorgefundenen Ladestrom ließe er sich nicht wiederherstellen.
    session, _ = tesla_session(state={"vehicle.tesla.charge_amps": None})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert findings == [Finding("17.2-7", "Tesla bereit", "fail", "Ladestrom unbekannt.")]
    assert session.actuators["vehicle.tesla"].sent == []


async def test_ready_waits_for_the_values_after_the_start(tesla_session, respx_mock) -> None:
    # Nach dem Start melden sich Tessie und der Wall Connector erst nach und nach.
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, twc = tesla_session(
        answers=[False],
        twc_follows=True,
        state={"vehicle.tesla.online": None, "wallbox.twc.vehicle_connected": None},
    )
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, put_signal, twc.store, "vehicle.tesla.online", True)
    loop.call_later(0.1, put_signal, twc.store, "wallbox.twc.vehicle_connected", True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=ROOMY)
    assert findings[0].status == "ok"
    # der Strom wurde gemessen, der Wall Connector war nicht „ohne Messwerte“
    assert re.fullmatch(
        rf"Tessie {SECONDS} s, Wall Connector {SECONDS} s", step(findings, "Ladestrom 10 A").detail
    )


# Ströme


async def test_current_steps_send_their_amps_and_name_both_times(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    for title in ("Ladestrom 10 A", "Verhalten bei 5 A"):
        result = step(findings, title)
        assert (result.item, result.status) == ("17.2-7", "ok")
        assert re.fullmatch(rf"Tessie {SECONDS} s, Wall Connector {SECONDS} s", result.detail)
    assert amps(respx_mock) == ["10", "5", "16"]  # zuletzt das Wiederherstellen


@pytest.mark.parametrize(
    ("limits", "titles"),
    [
        ({"min_a": 5, "max_a": 16}, ["Ladestrom 10 A", "Verhalten bei 5 A"]),
        ({"min_a": 5, "max_a": 10}, ["Ladestrom 10 A", "Verhalten bei 5 A"]),
        ({"min_a": 10, "max_a": 16}, ["Verhalten bei 10 A"]),  # 10 A wäre doppelt
        ({"min_a": 12, "max_a": 16}, ["Verhalten bei 12 A"]),  # 10 A liegt darunter
        ({"min_a": 5, "max_a": 8}, ["Verhalten bei 5 A"]),  # 10 A liegt darüber
    ],
)
async def test_ten_amps_only_between_min_and_max(
    tesla_session, respx_mock, cfg: Config, limits: dict[str, int], titles: list[str]
) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[False],
        twc_follows=True,
        config=with_tesla(cfg, **limits),
        state={"vehicle.tesla.charge_amps": limits["max_a"]},
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert [f.title for f in findings if f.title.startswith(("Ladestrom", "Verhalten"))] == titles


async def test_no_vehicle_at_the_wall_connector_means_no_effect_to_measure(
    tesla_session, respx_mock
) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], state={"wallbox.twc.vehicle_connected": False})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    for title in ("Ladestrom 10 A", "Verhalten bei 5 A"):
        result = step(findings, title)
        assert result.status == "ok"
        assert re.fullmatch(rf"Tessie {SECONDS} s; {NOT_AT_TWC}", result.detail)


async def test_a_vehicle_that_charges_elsewhere_is_not_measured_at_the_wall_connector(
    tesla_session, respx_mock, cfg: Config
) -> None:
    # default_wallbox ist die EVCS: Der Wall Connector zeigt, wenn überhaupt, ein anderes Auto.
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], config=with_tesla(cfg, default_wallbox="evcs"))
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert re.fullmatch(
        rf"Tessie {SECONDS} s; {NOT_AT_TWC}", step(findings, "Ladestrom 10 A").detail
    )


async def test_a_silent_wall_connector_is_named(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], state={"wallbox.twc.vehicle_connected": None})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Ladestrom 10 A")
    assert result.status == "ok"
    assert re.fullmatch(
        rf"Tessie {SECONDS} s; Wirkung nicht messbar \(Wall Connector ohne Messwerte\)",
        result.detail,
    )


async def test_a_current_that_is_already_there_proves_nothing(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[False],
        twc_follows=True,
        state={f"wallbox.twc.l{phase}.current_a": 10.0 for phase in (1, 2, 3)},
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    already = step(findings, "Ladestrom 10 A")
    assert already.status == "ok"
    assert re.fullmatch(
        rf"Tessie {SECONDS} s; Wirkung nicht messbar \(Strom am Wall Connector schon bei 10 A\)",
        already.detail,
    )
    # beim nächsten Schritt liegt der Strom weit weg vom Sollwert: Er wird gemessen
    assert "Wall Connector " in step(findings, "Verhalten bei 5 A").detail
    assert "nicht messbar" not in step(findings, "Verhalten bei 5 A").detail


@pytest.mark.parametrize(
    ("phase_a", "reached"), [(11.5, True), (8.5, True), (11.6, False), (8.4, False)]
)
async def test_the_largest_phase_within_one_and_a_half_amps_counts(
    tesla_session, respx_mock, cfg: Config, phase_a: float, reached: bool
) -> None:
    # Nur L1 liegt in der Nähe von 10 A, L2 und L3 ziehen kaum etwas: Der größte Phasenstrom zählt.
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, twc = tesla_session(
        answers=[False],
        config=with_tesla(cfg, min_a=10),
        state={"wallbox.twc.l2.current_a": 2.0, "wallbox.twc.l3.current_a": 2.0},
    )
    asyncio.get_running_loop().call_later(
        0.05, put_signal, twc.store, "wallbox.twc.l1.current_a", phase_a
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Verhalten bei 10 A")
    assert result.status == ("ok" if reached else "warn")


async def test_a_wall_connector_that_does_not_follow_is_a_warning(
    tesla_session, respx_mock
) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False])  # der Wall Connector bleibt bei 16 A
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Ladestrom 10 A")
    assert result.status == "warn"
    assert re.fullmatch(
        rf"Tessie {SECONDS} s; Wall Connector nach 0,3 s nicht bei 10 A \(Ist 16 A\)",
        result.detail,
    )


async def test_a_wall_connector_without_phase_currents_is_unknown(
    tesla_session, respx_mock
) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[False], state={f"wallbox.twc.l{phase}.current_a": None for phase in (1, 2, 3)}
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Ladestrom 10 A").detail.endswith("nicht bei 10 A (Ist unbekannt)")


async def test_a_command_tessie_refuses_is_an_error(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": False})
    session, _ = tesla_session(answers=[False])
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Ladestrom 10 A") == Finding(
        "17.2-7", "Ladestrom 10 A", "fail", "Fehler: Tessie meldet result=false."
    )


async def test_a_command_tessie_does_not_answer_names_the_deadline(
    tesla_session, respx_mock
) -> None:
    async def silence(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()  # Tessie antwortet nie
        raise AssertionError("unerreichbar")

    respx_mock.post(AMPS_URL).mock(side_effect=silence)
    session, _ = tesla_session(answers=[False])
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Ladestrom 10 A") == Finding(
        "17.2-7", "Ladestrom 10 A", "fail", "nicht bestätigt nach 0,3 s"
    )


# Laden aus und an


async def test_charging_off_and_on_names_the_contactor(tesla_session, respx_mock) -> None:
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    asked: list[str] = []
    session, _ = tesla_session(answers=[True], twc_follows=True, asked=asked)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert (result.item, result.status) == ("17.2-7", "ok")
    assert re.fullmatch(
        rf"aus nach {SECONDS} s \(Schütz offen nach {SECONDS} s\), an nach {SECONDS} s",
        result.detail,
    )
    assert asked == [CHARGING_QUESTION]
    # erst aus, dann an; das Laden lief vorher, also braucht es danach kein Wiederherstellen
    assert commands(respx_mock)[3:] == ["stop_charging", "start_charging"]
    assert findings[-1] is result


async def test_declined_charging_step_is_skipped(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    stray = stray_requests(respx_mock)
    asked: list[str] = []
    session, _ = tesla_session(answers=[False], twc_follows=True, asked=asked)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Laden aus und an") == Finding(
        "17.2-7", "Laden aus und an", "info", "übersprungen"
    )
    assert asked == [CHARGING_QUESTION]
    assert set(commands(respx_mock)) == {"set_charging_amps"}  # weder aus noch an
    assert not stray.called


async def test_the_charging_step_needs_no_hand_so_yes_does_not_skip_it(
    tesla_session, respx_mock
) -> None:
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(answers=[True], twc_follows=True, unattended=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Laden aus und an").status == "ok"
    assert not any("--yes" in f.detail for f in findings)


async def test_a_contactor_that_was_open_proves_nothing(tesla_session, respx_mock) -> None:
    # Das Auto lädt nicht (Schütz offen, kein Strom): Ausschalten ändert am Wall Connector nichts.
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[True],
        state={
            "vehicle.tesla.charging_state": "Stopped",
            "wallbox.twc.contactor_closed": False,
            **{f"wallbox.twc.l{phase}.current_a": 0.0 for phase in (1, 2, 3)},
        },
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "ok"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s \(Schütz schon offen\), an nach {SECONDS} s", result.detail
    )


async def test_a_contactor_that_stays_closed_is_a_warning(tesla_session, respx_mock) -> None:
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(answers=[True])  # der Wall Connector folgt nicht
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "warn"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s \(Schütz nach 0,3 s nicht offen\), an nach {SECONDS} s",
        result.detail,
    )
    assert commands(respx_mock)[-1] == "start_charging"  # es geht trotzdem wieder an


async def test_no_vehicle_at_the_wall_connector_means_no_contactor_to_watch(
    tesla_session, respx_mock
) -> None:
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(answers=[True], state={"wallbox.twc.vehicle_connected": False})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "ok"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s \(Wirkung nicht messbar: nicht am Wall Connector\), "
        rf"an nach {SECONDS} s",
        result.detail,
    )


async def test_charging_off_not_confirmed_is_an_error(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    respx_mock.post(STOP_URL).respond(json={"result": False})
    respx_mock.post(START_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[True], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result == Finding(
        "17.2-7", "Laden aus und an", "fail", "Laden aus: Fehler: Tessie meldet result=false."
    )
    # Kein „an“ im Schritt; das Wiederherstellen danach stellt das vorgefundene Laden sicher.
    follow_up = findings[findings.index(result) + 1]
    assert (follow_up.title, follow_up.status) == ("Wiederherstellen: Laden an", "ok")


async def test_charging_on_not_confirmed_is_an_error(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    respx_mock.post(STOP_URL).respond(json={"result": True})
    respx_mock.post(START_URL).respond(json={"result": False})
    session, _ = tesla_session(answers=[True], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "fail"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s \(Schütz offen nach {SECONDS} s\), "
        r"Laden an: Fehler: Tessie meldet result=false\.",
        result.detail,
    )


@pytest.mark.parametrize(("recovers", "status"), [(True, "ok"), (False, "fail")])
async def test_a_charging_on_that_failed_is_sent_again_by_the_restore(
    tesla_session, respx_mock, recovers: bool, status: str
) -> None:
    # Tessie bestätigt „aus“ und lehnt „an“ ab. Der Cache zeigt noch „Charging“, und zuletzt ging
    # „an“ hinaus; bestätigt aber ist nur „aus“. Das Wiederherstellen nach dem Block sendet „an“
    # genau einmal noch, und die Zeile dazu steht hinter dem Schritt.
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    respx_mock.post(STOP_URL).respond(json={"result": True})
    starts: list[httpx.Request] = []

    def start(request: httpx.Request) -> httpx.Response:
        starts.append(request)
        return httpx.Response(200, json={"result": recovers and len(starts) > 1})

    respx_mock.post(START_URL).mock(side_effect=start)
    session, _ = tesla_session(answers=[True], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "fail"
    follow_up = findings[findings.index(result) + 1]
    assert (follow_up.title, follow_up.status) == ("Wiederherstellen: Laden an", status)
    assert len(starts) == 2  # einmal im Schritt, genau einmal mehr beim Wiederherstellen


# Nach der Rückfrage: Werte von Tessie, die nicht neuer sind als die Antwort, gelten als unbekannt


async def test_a_car_that_fell_asleep_during_the_question_is_not_woken(
    tesla_session, respx_mock
) -> None:
    # Die Rückfrage hielt den Adapter von Tessie an. Die Antwort kam, als das Auto schon schlief;
    # die ersten Werte danach sagen es. Ein „Laden aus“ weckte es, also geht nichts hinaus.
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    stray = stray_requests(respx_mock)  # weder aus noch an hat eine Route
    session, _ = tesla_session(
        answers=[True],
        after_answer={
            CHARGING_QUESTION: lambda twc: put_signal(twc.store, "vehicle.tesla.online", False)
        },
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Laden aus und an") == Finding(
        "17.2-7", "Laden aus und an", "fail", f"Laden aus: nicht steuerbar: {ASLEEP}"
    )
    assert [kind for kind, _ in session.actuators["vehicle.tesla"].sent] == ["current"] * 3
    assert not stray.called


async def test_no_report_from_tessie_after_the_question_is_an_error_and_sends_nothing(
    tesla_session, respx_mock
) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    stray = stray_requests(respx_mock)
    session, _ = tesla_session(answers=[True], tessie_polls=False)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Laden aus und an") == Finding(
        "17.2-7", "Laden aus und an", "fail", "Fahrzeugdaten nach der Rückfrage nicht aktuell."
    )
    assert [kind for kind, _ in session.actuators["vehicle.tesla"].sent] == ["current"] * 3
    assert not stray.called


async def test_the_charging_step_waits_for_a_report_from_tessie_after_the_question(
    tesla_session, respx_mock
) -> None:
    # Tessie meldet sich erst 0,15 s nach der Antwort: Vorher geht der Befehl nicht hinaus.
    reported: list[float] = []
    stopped: list[float] = []

    def stop(request: httpx.Request) -> httpx.Response:
        stopped.append(time.monotonic())
        return httpx.Response(200, json={"result": True})

    respx_mock.post(AMPS_URL).respond(json={"result": True})
    respx_mock.post(STOP_URL).mock(side_effect=stop)
    respx_mock.post(START_URL).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[True],
        twc_follows=True,
        tessie_delay_s=0.15,
        after_answer={CHARGING_QUESTION: lambda twc: reported.append(time.monotonic())},
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=ROOMY)
    assert step(findings, "Laden aus und an").status == "ok"
    assert len(stopped) == 1 and stopped[0] >= reported[0]


async def test_a_declined_question_waits_for_nothing(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, _ = tesla_session(answers=[False], twc_follows=True, tessie_polls=False)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert step(findings, "Laden aus und an") == Finding(
        "17.2-7", "Laden aus und an", "info", "übersprungen"
    )


# Wiederherstellen nach jedem Block (Präzisierung 11)


async def test_each_block_is_undone_before_the_next(tesla_session, respx_mock) -> None:
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(
        answers=[True],
        state={
            "vehicle.tesla.charge_amps": 12,
            "vehicle.tesla.charging_state": "Stopped",
            "wallbox.twc.vehicle_connected": False,  # nichts zu messen, das geht schneller
        },
    )
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    # die beiden Ströme sind ein Block, das Laden der nächste
    assert [(f.title, f.status) for f in findings] == [
        ("Tesla bereit", "ok"),
        ("Ladestrom 10 A", "ok"),
        ("Verhalten bei 5 A", "ok"),
        ("Wiederherstellen: Strom 12 A", "ok"),
        ("Laden aus und an", "ok"),
        ("Wiederherstellen: Laden aus", "ok"),
    ]
    assert commands(respx_mock) == [
        *["set_charging_amps"] * 3,
        *["stop_charging", "start_charging", "stop_charging"],
    ]
    assert amps(respx_mock) == ["10", "5", "12"]
    assert await session.restore() == []  # am Ende ist nichts mehr offen


async def test_a_block_that_ends_where_it_began_needs_no_restore(tesla_session, respx_mock) -> None:
    # Das Fahrzeug lud vorher und lädt nach „aus“ und „an“ wieder; nur die Ströme brauchen Block 1.
    for url in (AMPS_URL, STOP_URL, START_URL):
        respx_mock.post(url).respond(json={"result": True})
    session, _ = tesla_session(answers=[True], twc_follows=True)
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert [f.title for f in findings if f.title.startswith("Wiederherstellen")] == [
        "Wiederherstellen: Strom 16 A"
    ]
    assert await session.restore() == []


async def test_the_restore_after_a_block_waits_as_long_as_tessie_may_take(
    tesla_session, respx_mock
) -> None:
    # Der dritte Strombefehl, das Wiederherstellen, bleibt unbeantwortet: Die Frist ist die des
    # Teils (hier 0,3 s), nicht die der Sitzung (15 s).
    sent: list[str] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["amps"])
        if len(sent) > 2:
            await asyncio.Event().wait()  # Tessie antwortet beim Wiederherstellen nicht
        return httpx.Response(200, json={"result": True})

    respx_mock.post(AMPS_URL).mock(side_effect=answer)
    session, _ = tesla_session(answers=[False], twc_follows=True)
    started = time.monotonic()
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert time.monotonic() - started < 5.0
    assert sent == ["10", "5", "16"]
    assert step(findings, "Wiederherstellen: Strom 16 A") == Finding(
        "17.2-7", "Wiederherstellen: Strom 16 A", "fail", "nicht bestätigt nach 0,3 s"
    )


# Schlafendes Auto


async def test_sleep_check_names_how_long_it_watched(tesla_session) -> None:
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    tessie_reports(twc.store, every_s=0.05, until_s=2.0)
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert len(findings) == 1
    assert (findings[0].item, findings[0].title, findings[0].status) == (
        "17.2-7",
        SLEEP_TITLE,
        "ok",
    )
    assert re.fullmatch(r"\d+(,\d)? min ohne Wecken, Befehle verweigert", findings[0].detail)


async def test_sleep_check_asks_the_capabilities_at_every_check(tesla_session) -> None:
    session, _ = tesla_session(state={"vehicle.tesla.online": False})
    car = session.actuators["vehicle.tesla"]
    seen: list[tuple[bool, tuple[str, ...]]] = []
    capabilities = car.capabilities

    def spy(snap, now):
        result = capabilities(snap, now)
        seen.append((result.controllable, result.reasons))
        return result

    car.capabilities = spy  # type: ignore[method-assign]
    await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert len(seen) >= 2  # am Anfang und danach alle 0,1 s bis zu den 0,3 s
    assert set(seen) == {(False, (ASLEEP,))}  # jedes Mal verweigert, wegen des Schlafs
    assert car.sent == []


async def test_sleep_check_asks_nothing_and_has_no_other_step(tesla_session) -> None:
    asked: list[str] = []
    session, _ = tesla_session(state={"vehicle.tesla.online": False}, asked=asked)
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert asked == [] and [f.title for f in findings] == [SLEEP_TITLE]
    assert session.findings == findings


async def test_sleep_check_reports_a_car_that_wakes_up(tesla_session) -> None:
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    asyncio.get_running_loop().call_later(0.15, put_signal, twc.store, "vehicle.tesla.online", True)
    timings = replace(FAST, sleep_total_s=30.0)  # endet beim Wachwerden, nicht nach 30 s
    started = time.monotonic()
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=timings)
    assert time.monotonic() - started < 5.0
    assert [(f.title, f.status) for f in findings] == [(SLEEP_TITLE, "warn")]
    assert re.fullmatch(
        r"Fahrzeug wurde nach \d+(,\d)? min wach; Ursache prüfen \(App, Zeitplan\)",
        findings[0].detail,
    )
    assert session.actuators["vehicle.tesla"].sent == []


async def test_sleep_check_needs_a_sleeping_car(tesla_session) -> None:
    session, _ = tesla_session()  # online
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            SLEEP_TITLE,
            "fail",
            "Fahrzeug ist wach; die Prüfung braucht ein schlafendes Fahrzeug.",
        )
    ]
    assert session.actuators["vehicle.tesla"].sent == []


async def test_sleep_check_needs_to_know_the_state(tesla_session) -> None:
    # Ohne Werte von Tessie weiß niemand, ob das Auto schläft.
    session, _ = tesla_session(state={"vehicle.tesla.online": None}, tessie_connected=False)
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert findings == [
        Finding("17.2-7", SLEEP_TITLE, "fail", "nicht beobachtbar: Tessie nicht erreichbar.")
    ]


async def test_sleep_check_needs_a_known_state_from_a_reachable_tessie(tesla_session) -> None:
    session, _ = tesla_session(state={"vehicle.tesla.online": None})
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            SLEEP_TITLE,
            "fail",
            "Zustand des Fahrzeugs unbekannt; die Prüfung braucht ein schlafendes Fahrzeug.",
        )
    ]


# Der Schlaf-Test braucht frische Meldungen von Tessie, zu Beginn und am Ende: Fällt Tessie aus,
# gelten die Werte bis zu 900 s weiter, ohne dass jemand das Fahrzeug beobachtet.


async def test_sleep_check_with_tessie_failing_is_not_observed(tesla_session) -> None:
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    tessie_reports(twc.store, every_s=0.05, until_s=0.1)  # danach fällt Tessie aus
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert findings == [
        Finding(
            "17.2-7",
            SLEEP_TITLE,
            "fail",
            "nicht beobachtbar: Wert veraltet; Tessie hat sich zum Ende der Prüfung nicht neu "
            "gemeldet.",
        )
    ]
    assert session.actuators["vehicle.tesla"].sent == []


async def test_sleep_check_with_tessie_unreachable_at_the_end_is_not_observed(
    tesla_session,
) -> None:
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    tessie_reports(twc.store, every_s=0.05, until_s=2.0)
    asyncio.get_running_loop().call_later(0.1, twc.store.set_connected, "tessie:tesla", False)
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert findings == [
        Finding("17.2-7", SLEEP_TITLE, "fail", "nicht beobachtbar: Tessie nicht erreichbar.")
    ]


async def test_sleep_check_reports_a_car_that_is_awake_in_the_last_report(tesla_session) -> None:
    # Die letzte Prüfung der Schleife sah noch ein schlafendes Auto; die frische Meldung am Ende
    # sagt „wach“.
    session, twc = tesla_session(state={"vehicle.tesla.online": False})
    asyncio.get_running_loop().call_later(0.45, put_signal, twc.store, "vehicle.tesla.online", True)
    timings = replace(FAST, sleep_check_s=1.0)  # eine Prüfung in der Schleife, bei 0,3 s
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=timings)
    assert [(f.title, f.status) for f in findings] == [(SLEEP_TITLE, "warn")]
    assert findings[0].detail.startswith("Fahrzeug wurde nach ")


# Mit den echten Adaptern


async def test_the_part_reads_what_the_real_adapters_write(cfg: Config, respx_mock) -> None:
    # Tessie und der Wall Connector liefern über ihre Adapter, was der Teil liest; ein Befehl an
    # Tessie ändert den Strom, den der Wall Connector danach meldet.
    state = json.loads((FIXTURES / "tessie_state.json").read_text(encoding="utf-8"))
    vitals = json.loads((FIXTURES / "twc_vitals.json").read_text(encoding="utf-8"))

    def command(request: httpx.Request) -> httpx.Response:
        amps = float(request.url.params["amps"])
        vitals.update(currentA_a=amps, currentB_a=amps, currentC_a=amps)
        return httpx.Response(200, json={"result": True})

    respx_mock.get(f"https://api.tessie.com/{VIN}/state").respond(json=state)
    respx_mock.get("http://twc.lan/api/1/version").respond(json={"firmware_version": "25.42.1"})
    respx_mock.get("http://twc.lan/api/1/vitals").mock(
        side_effect=lambda request: httpx.Response(200, json=vitals)
    )
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1000})
    respx_mock.post(AMPS_URL).mock(side_effect=command)
    store = StateStore(SystemClock())
    client = httpx.AsyncClient()
    token = SecretStr("tessie-token")
    vehicle = cfg.vehicles["tesla"]
    car = TeslaActuator(
        "tesla",
        vehicle,
        token,
        client,
        lambda: store.connected("tessie:tesla") is True,
        clock=SystemClock().now,
    )
    car.verify_s = 0.0  # der Cache von Tessie folgt hier keinem Befehl: Tessies Bestätigung zählt
    session = Part2Session(
        cfg,
        store=store,
        adapters=[
            TessieAdapter("tesla", vehicle, cfg.site.location, token, store, client),
            TwcAdapter("twc", cfg.wallboxes["twc"], store, client, vitals_s=0.02),
        ],
        actuators={car.id: car},
        ask=lambda _question: False,
        poll_s=0.01,
        cleanup=[client.aclose],
    )
    await session.start()
    try:
        findings = await run_tesla(session, cfg, "tesla", timings=ROOMY)
    finally:
        await session.stop()
    assert findings[0] == Finding(
        "17.2-7", "Tesla bereit", "ok", "SOC 63 %, Ladestrom 16 A, Charging"
    )
    for title in ("Ladestrom 10 A", "Verhalten bei 5 A"):
        assert re.fullmatch(
            rf"Tessie {SECONDS} s, Wall Connector {SECONDS} s", step(findings, title).detail
        )
    assert step(findings, "Wiederherstellen: Strom 16 A").status == "ok"
