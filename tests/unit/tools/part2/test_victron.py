"""Tests des Teils Victron im Prüfprotokoll Teil 2 (Spec 17.2-1 bis 17.2-4 und 17.2-8)."""

import asyncio
import re
from dataclasses import replace

import pytest
from tests.unit.tools.part2.conftest import FakeCerbo

from bindaems.shared.config import Config
from bindaems.tools.part2.victron import DEFAULT_TIMINGS, VictronTimings, run_victron
from bindaems.tools.verify_checks import Finding

FAST = VictronTimings(confirm_s=0.2, observe_s=0.05, effect_s=0.2, hold_s=0.1, reboot_s=0.2)
ROOMY = replace(FAST, confirm_s=1.0)  # Luft für Wartezeiten
REBOOT = replace(ROOMY, reboot_s=2.0)

SETPOINT = "hub4/0/Overrides/Setpoint"
DISCHARGE = "settings/0/Settings/CGwacs/MaxDischargePower"
SECONDS = r"\d+(,\d)?"  # eine Zeit in Sekunden mit höchstens einer Nachkommastelle

# Die Rückfragen. „Cerbo jetzt neu starten?“ und die zur Last stehen im Plan, die übrigen nicht.
Q_OVERRIDE = (
    "Netz-Sollwert-Override kurz auf 1000 W setzen und mit null wieder aufheben? "
    "Der Akku lädt dabei aus dem Netz."
)
Q_HOLD = "Override auf 1000 W setzen und 0 min halten, um einen Timeout zu erkennen?"
Q_REBOOT = (
    "Override auf 1000 W setzen und den Cerbo dann neu starten, um die Wirkung des Neustarts "
    "zu prüfen?"
)
Q_RESTART = "Cerbo jetzt neu starten?"
Q_DISCHARGE = (
    "Entladegrenze kurz auf 0 W setzen? Der Akku entlädt dann nicht; die Einstellung wird "
    "gespeichert."
)
Q_CHARGE = "Netzladen testen: Override kurz auf 2000 W setzen? Der Akku lädt dann aus dem Netz."
Q_CAR = (
    "Die EVCS lädt mit 3700 W. Override auf 3700 W setzen? Der Akku soll nicht ins Auto entladen."
)
Q_IMPORT = "Last so einschalten, dass eine Phase über 20 A käme?"

# Antworten für Läufe, in denen nur ein Schritt zählt (die übrigen Rückfragen bekommen Nein)
NO = [False] * 6
ONLY_OVERRIDE = [True, *NO]
ONLY_HOLD = [False, True, False, False, False, False]
ONLY_DISCHARGE = [False, False, False, True, False, False]
ONLY_CHARGE = [False, False, False, False, True, False]
ONLY_CAR = [False, False, False, False, False, True, False]  # die EVCS lädt: eine Rückfrage mehr
CAR = {"wallbox.evcs.power_w": 3700.4}  # die EVCS lädt


def step(findings: list[Finding], title: str) -> Finding:
    return next(f for f in findings if f.title == title)


def setpoints(cerbo: FakeCerbo) -> list[float | int | None]:
    return [v for p, v in cerbo.writes if p == SETPOINT]


def limits(cerbo: FakeCerbo) -> list[float | int | None]:
    return [v for p, v in cerbo.writes if p == DISCHARGE]


def with_cap(cfg: Config, cap_w: float) -> Config:
    """Die Beispielkonfiguration mit einem anderen maximalen Netzladesetpoint."""
    victron = cfg.victron.model_copy(update={"max_grid_charge_setpoint_w": cap_w})
    return cfg.model_copy(update={"victron": victron})


def charges(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
    """Die Anlage: Mit Override lädt der Akku mit 1400 W, ohne entlädt er mit 50 W."""
    if path == SETPOINT:
        cerbo.put("battery.power_w", 1400.0 if value is not None else -50.0)


def grid_follows(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
    """Die Anlage: Mit Override bezieht das Netz 1000 W, ohne 300 W."""
    if path == SETPOINT:
        powers = (400.0, 300.0, 300.0) if value is not None else (100.0, 100.0, 100.0)
        for phase, power in enumerate(powers, start=1):
            cerbo.put(f"grid.l{phase}.power_w", power)


# Vorgabe des Plans


async def test_dess_on_stops_without_writes(victron_session) -> None:
    session, cerbo = victron_session(state={"dess.mode": 1})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert (
        findings[0].detail
        == "Dynamic ESS ist aktiv (Modus 1); die Victron-Punkte erst nach dem Abschalten von DESS."
    )
    assert cerbo.writes == []


async def test_override_and_its_release(victron_session) -> None:
    session, cerbo = victron_session(answers=[True] + [False] * 6)
    findings = await run_victron(session, session.cfg, timings=FAST)
    step = next(f for f in findings if f.title == "Netz-Sollwert-Override")
    assert step.status == "ok" and "aufgehoben nach" in step.detail
    assert [v for p, v in cerbo.writes if p == "hub4/0/Overrides/Setpoint"] == [1000.0, None]


async def test_null_that_is_not_confirmed_is_reported(victron_session) -> None:
    session, _ = victron_session(answers=[True] + [False] * 6, null_clears=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    step = next(f for f in findings if f.title == "Netz-Sollwert-Override")
    assert (step.status, step.detail) == (
        "fail",
        "Aufheben mit null nicht bestätigt; Weg für die Rücknahme klären.",
    )


# Aufruf


def test_the_timings_default_to_the_values_of_the_plan() -> None:
    expected = VictronTimings(
        confirm_s=15.0, observe_s=30.0, effect_s=60.0, hold_s=600.0, reboot_s=600.0
    )
    assert VictronTimings() == DEFAULT_TIMINGS == expected


async def test_findings_are_kept_in_the_session_as_they_come(victron_session) -> None:
    # Nach Strg+C nennt der Bericht die Schritte bis dahin: Sie stehen schon in der Sitzung.
    session, _ = victron_session(answers=NO)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert session.findings == findings


async def test_a_session_without_victron_is_refused(evcs_session) -> None:
    session, _ = evcs_session()
    with pytest.raises(ValueError, match="Kein Aktor „victron“"):
        await run_victron(session, session.cfg, timings=FAST)
    assert session.findings == []


# Victron bereit


async def test_ready_lists_the_hub4_overrides(victron_session) -> None:
    session, _ = victron_session(
        answers=NO, state={"ess.override.forcecharge": 0, "ess.override.feedinexcess": 2}
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[0] == Finding(
        "17.2-1",
        "Victron bereit",
        "ok",
        "hub4-Overrides vorher: feedinexcess = 2, forcecharge = 0, setpoint = null; "
        "Entladegrenze vorher: 5000 W",
    )


async def test_ready_without_any_override_signal(victron_session) -> None:
    session, _ = victron_session(answers=NO, without=["ess.override.setpoint"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[0] == Finding(
        "17.2-1",
        "Victron bereit",
        "ok",
        "hub4-Overrides vorher: keine; Entladegrenze vorher: 5000 W",
    )


@pytest.mark.parametrize(("mode", "shown"), [(1, "1"), (4, "4"), (2.0, "2")])
async def test_dess_active_names_the_mode_and_ends_the_part(
    victron_session, mode: float, shown: str
) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(state={"dess.mode": mode}, asked=asked)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [
        Finding(
            "17.2-1",
            "Victron bereit",
            "fail",
            f"Dynamic ESS ist aktiv (Modus {shown}); die Victron-Punkte erst nach dem Abschalten "
            "von DESS.",
        )
    ]
    assert cerbo.writes == [] and asked == []


async def test_an_unknown_dess_mode_counts_as_active(victron_session) -> None:
    session, cerbo = victron_session(without=["dess.mode"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [
        Finding(
            "17.2-1",
            "Victron bereit",
            "fail",
            "Dynamic ESS ist aktiv (Modus unbekannt); die Victron-Punkte erst nach dem Abschalten "
            "von DESS.",
        )
    ]
    assert cerbo.writes == []


async def test_not_controllable_reports_the_reasons_and_asks_nothing(victron_session) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(asked=asked)
    cerbo.disconnect()
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [Finding("17.2-1", "Victron bereit", "fail", "Cerbo nicht verbunden.")]
    assert asked == [] and cerbo.writes == []


async def test_ready_waits_for_the_values_after_the_start(victron_session) -> None:
    session, cerbo = victron_session(answers=NO, without=["dess.mode"])
    asyncio.get_running_loop().call_later(0.05, cerbo.put, "dess.mode", 0)
    findings = await run_victron(session, session.cfg, timings=ROOMY)
    assert findings[0].status == "ok"


OVERRIDE_ACTIVE = (
    "Netz-Sollwert-Override aktiv ({}); ein anderes System regelt – vor dem Test aufheben."
)


@pytest.mark.parametrize(
    ("override", "shown"), [(500.0, "500 W"), (0.0, "0 W"), (9000.0, "9000 W"), (-300.0, "−300 W")]
)
async def test_a_found_override_means_another_system_regulates(
    victron_session, override: float, shown: str
) -> None:
    # Der Teil schriebe über den Override eines anderen Systems und ließe ihn danach liegen oder
    # löschte ihn: Er fragt nichts und schreibt nichts.
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[True] * 7, state={"ess.override.setpoint": override}, asked=asked
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [Finding("17.2-1", "Victron bereit", "fail", OVERRIDE_ACTIVE.format(shown))]
    assert cerbo.writes == [] and asked == []
    assert cerbo.values["ess.override.setpoint"] == override  # unangetastet


async def test_dess_comes_before_a_found_override(victron_session) -> None:
    state = {"dess.mode": 1, "ess.override.setpoint": 500.0}
    session, cerbo = victron_session(state=state)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert len(findings) == 1 and findings[0].detail.startswith("Dynamic ESS ist aktiv (Modus 1)")
    assert cerbo.writes == []


@pytest.mark.parametrize(
    ("limit", "shown", "problem"),
    [
        (20000.0, "20000", "Auf den Höchstwert 12000 W begrenzt."),
        (12000.4, "12000,4", "Auf den Höchstwert 12000 W begrenzt."),
        (-5.0, "−5", "Wert −5 W liegt außerhalb des zulässigen Bereichs."),
    ],
)
async def test_a_found_discharge_limit_outside_the_limits_is_not_ready(
    victron_session, limit: float, shown: str, problem: str
) -> None:
    # Ein solcher Wert ließe sich nach dem Test nicht wiederherstellen: Es wird nichts geschrieben,
    # und niemand wird gefragt.
    asked: list[str] = []
    session, cerbo = victron_session(state={"ess.max_discharge_w": limit}, asked=asked)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [
        Finding(
            "17.2-1",
            "Victron bereit",
            "fail",
            f"Ausgangswert Entladegrenze {shown} W außerhalb der Grenzen; er ließe sich nicht "
            f"wiederherstellen: {problem}",
        )
    ]
    assert cerbo.writes == [] and asked == []


@pytest.mark.parametrize(
    ("limit", "shown"), [(-1.0, "unbegrenzt"), (1.5, "1,5 W"), (12000.0, "12000 W")]
)
async def test_found_discharge_limits_inside_the_limits_are_ready(
    victron_session, limit: float, shown: str
) -> None:
    # „unbegrenzt“ (−1), knapp über der Sperre und der Höchstwert selbst
    session, _ = victron_session(answers=NO, state={"ess.max_discharge_w": limit})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[0] == Finding(
        "17.2-1",
        "Victron bereit",
        "ok",
        f"hub4-Overrides vorher: setpoint = null; Entladegrenze vorher: {shown}",
    )


@pytest.mark.parametrize(("limit", "shown"), [(0.0, "0"), (0, "0"), (1.0, "1"), (0.4, "0,4")])
async def test_a_found_discharge_lock_ends_the_part_without_writes(
    victron_session, limit: float, shown: str
) -> None:
    # Ein abgebrochener Lauf kann die Entladegrenze auf 0 W hinterlassen. Als Ausgangswert stellte
    # das Werkzeug sie am Ende wieder her: Das Entladen bliebe gesperrt.
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[True] * 7, state={"ess.max_discharge_w": limit}, asked=asked
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings == [
        Finding(
            "17.2-1",
            "Victron bereit",
            "fail",
            f"Entladegrenze steht auf {shown} W (Entladen gesperrt), vermutlich von einem "
            "abgebrochenen Lauf – vor dem Test zurücksetzen.",
        )
    ]
    assert cerbo.writes == [] and asked == []


# „bereit“ wartet bis confirm_s auf Override und Entladegrenze, ehe es entscheidet


async def test_ready_waits_for_the_override_and_the_discharge_limit(victron_session) -> None:
    session, cerbo = victron_session(
        answers=NO, without=["ess.override.setpoint", "ess.max_discharge_w"]
    )
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, cerbo.put, "ess.override.setpoint", None)
    loop.call_later(0.08, cerbo.put, "ess.max_discharge_w", 5000.0)
    findings = await run_victron(session, session.cfg, timings=ROOMY)
    # Override und Entladegrenze stehen im Befund, denn „bereit“ hat auf sie gewartet.
    assert findings[0] == Finding(
        "17.2-1",
        "Victron bereit",
        "ok",
        "hub4-Overrides vorher: setpoint = null; Entladegrenze vorher: 5000 W",
    )


async def test_ready_checks_a_discharge_limit_that_is_reported_late(victron_session) -> None:
    # Der Cerbo meldet die Entladegrenze erst nach dem Start, und sie liegt über dem Höchstwert.
    # Ohne Prüfung bliebe sie nach dem Test bei 0 W: Sie ließe sich nicht zurückschreiben.
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=ONLY_DISCHARGE, without=["ess.max_discharge_w"], asked=asked
    )
    asyncio.get_running_loop().call_later(0.05, cerbo.put, "ess.max_discharge_w", 20000.0)
    findings = await run_victron(session, session.cfg, timings=ROOMY)
    assert findings == [
        Finding(
            "17.2-1",
            "Victron bereit",
            "fail",
            "Ausgangswert Entladegrenze 20000 W außerhalb der Grenzen; er ließe sich nicht "
            "wiederherstellen: Auf den Höchstwert 12000 W begrenzt.",
        )
    ]
    assert cerbo.writes == [] and asked == []


async def test_ready_checks_an_override_that_is_reported_late(victron_session) -> None:
    session, cerbo = victron_session(answers=NO, without=["ess.override.setpoint"])
    asyncio.get_running_loop().call_later(0.05, cerbo.put, "ess.override.setpoint", 500.0)
    findings = await run_victron(session, session.cfg, timings=ROOMY)
    assert findings == [
        Finding("17.2-1", "Victron bereit", "fail", OVERRIDE_ACTIVE.format("500 W"))
    ]
    assert cerbo.writes == []


async def test_ready_waits_at_most_confirm_s_for_values_that_do_not_come(victron_session) -> None:
    # Meldet der Cerbo sie nie, entscheidet „bereit“ nach confirm_s mit dem, was da ist; die
    # Schritte, die den Wert brauchen, melden ihn dann als unbekannt.
    session, _ = victron_session(
        answers=NO, without=["ess.override.setpoint", "ess.max_discharge_w"]
    )
    started = asyncio.get_running_loop().time()
    findings = await run_victron(session, session.cfg, timings=replace(FAST, confirm_s=0.3))
    elapsed = asyncio.get_running_loop().time() - started
    assert findings[0] == Finding(
        "17.2-1",
        "Victron bereit",
        "ok",
        "hub4-Overrides vorher: keine; Entladegrenze vorher: unbekannt",
    )
    assert 0.28 <= elapsed < 2.0


async def test_a_discharge_limit_that_comes_after_bereit_is_still_checked_before_the_write(
    victron_session,
) -> None:
    # „bereit“ wartet nur confirm_s. Meldet der Cerbo die Entladegrenze erst danach, etwa wenn der
    # Betreiber die Rückfrage beantwortet, prüft die Sitzung sie vor dem ersten Schreiben.
    def reported(cerbo: FakeCerbo) -> None:
        cerbo.put("ess.max_discharge_w", 20000.0)

    session, cerbo = victron_session(
        answers=ONLY_DISCHARGE, without=["ess.max_discharge_w"], react={Q_DISCHARGE: reported}
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[0].status == "ok"  # „bereit“ wusste noch nichts davon
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2",
        "Entladesperre",
        "fail",
        "Entladegrenze 0 W: nicht gesendet: Ausgangswert Entladegrenze 20000 W außerhalb der "
        "Grenzen; er ließe sich nicht wiederherstellen: Auf den Höchstwert 12000 W begrenzt.",
    )
    assert limits(cerbo) == []
    assert cerbo.values["ess.max_discharge_w"] == 20000.0  # nichts verstellt


# Rückfragen


async def test_declined_steps_are_skipped(victron_session) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(answers=NO, asked=asked)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert asked == [Q_OVERRIDE, Q_HOLD, Q_REBOOT, Q_DISCHARGE, Q_CHARGE, Q_IMPORT]
    assert findings[1:] == [
        Finding("17.2-1", "Netz-Sollwert-Override", "info", "übersprungen"),
        Finding("17.2-1", "Override ohne Timeout", "info", "übersprungen"),
        Finding("17.2-1", "Override nach Neustart des Cerbo", "info", "übersprungen"),
        Finding(
            "17.2-1",
            "Zusammenspiel mit dem persistenten Sollwert",
            "info",
            "übersprungen: kein Override gesetzt",
        ),
        Finding(
            "17.2-2",
            "Override für die Entladegrenze",
            "info",
            "ess.override.maxdischargepower existiert nicht",
        ),
        Finding("17.2-2", "Entladesperre", "info", "übersprungen"),
        Finding("17.2-3", "Netzladen", "info", "übersprungen"),
        Finding("17.2-4", "Kein Akku ins Auto", "info", "übersprungen: kein Laden an der EVCS"),
        Finding("17.2-8", "Importlimit", "info", "übersprungen"),
    ]
    assert cerbo.writes == []


async def test_the_hold_question_names_the_minutes(victron_session) -> None:
    asked: list[str] = []
    session, _ = victron_session(answers=NO, asked=asked)
    await run_victron(session, session.cfg, timings=replace(FAST, hold_s=600.0))
    assert asked[1] == "Override auf 1000 W setzen und 10 min halten, um einen Timeout zu erkennen?"


# Netz-Sollwert-Override


async def test_override_step_names_the_effect_on_the_grid(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_OVERRIDE, plant=grid_follows)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[1].item == "17.2-1"
    assert re.fullmatch(
        rf"1000 W nach {SECONDS} s bestätigt; Netz vorher 300 W, mit Override 1000 W; "
        rf"aufgehoben nach {SECONDS} s, danach 300 W",
        step(findings, "Netz-Sollwert-Override").detail,
    )


@pytest.mark.parametrize(
    ("state", "without", "grid"),
    [
        ({}, [], "300 W"),  # die Summe der drei Phasen
        ({"grid.power_w": 999.0}, [], "300 W"),  # die Phasen gehen vor
        ({"grid.power_w": 250.0}, ["grid.l1.power_w"], "250 W"),  # ohne Phase der Gesamtwert
        ({}, ["grid.l1.power_w", "grid.l2.power_w", "grid.l3.power_w"], "unbekannt"),
    ],
)
async def test_grid_power_is_the_sum_of_the_phases(
    victron_session, state: dict[str, float], without: list[str], grid: str
) -> None:
    session, _ = victron_session(answers=ONLY_OVERRIDE, state=state, without=without)
    findings = await run_victron(session, session.cfg, timings=FAST)
    detail = step(findings, "Netz-Sollwert-Override").detail
    assert f"Netz vorher {grid}, mit Override {grid}; aufgehoben" in detail
    assert detail.endswith(f"danach {grid}")


async def test_grid_power_is_the_mean_over_the_observation(victron_session) -> None:
    # Die Netzleistung springt während der Beobachtung von 0 auf 600 W; das Mittel liegt dazwischen.
    def jump(cerbo: FakeCerbo) -> None:
        loop = asyncio.get_running_loop()
        for phase in (1, 2, 3):
            cerbo.put(f"grid.l{phase}.power_w", 0.0)
            loop.call_later(0.1, cerbo.put, f"grid.l{phase}.power_w", 200.0)

    session, _ = victron_session(answers=ONLY_OVERRIDE, react={Q_OVERRIDE: jump})
    timings = replace(FAST, observe_s=0.3)
    findings = await run_victron(session, session.cfg, timings=timings)
    found = re.search(r"Netz vorher (\d+) W", step(findings, "Netz-Sollwert-Override").detail)
    assert found is not None and 150 < int(found[1]) < 550


async def test_unconfirmed_override_is_an_error_and_lifted_again(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_OVERRIDE, follow=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override") == Finding(
        "17.2-1", "Netz-Sollwert-Override", "fail", "Override 1000 W: nicht bestätigt nach 0,2 s"
    )
    assert setpoints(cerbo) == [
        1000.0,
        None,
    ]  # der Wiederherstellen-Schritt hebt ihn sicherheitshalber auf


async def test_refused_override_names_the_reason(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_OVERRIDE, accept=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override") == Finding(
        "17.2-1",
        "Netz-Sollwert-Override",
        "fail",
        "Override 1000 W: nicht gesendet: Cerbo-Verbindung: Auftrag nicht angenommen.",
    )
    assert cerbo.writes == []


async def test_an_unknown_override_state_is_not_written(victron_session) -> None:
    # Ohne den vorgefundenen Zustand ließe sich der Override nicht zurücknehmen.
    session, cerbo = victron_session(answers=ONLY_OVERRIDE, without=["ess.override.setpoint"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override") == Finding(
        "17.2-1",
        "Netz-Sollwert-Override",
        "fail",
        "Override 1000 W: nicht gesendet: Ausgangswert unbekannt; er ließe sich nicht "
        "wiederherstellen.",
    )
    assert cerbo.writes == []


async def test_the_override_is_at_most_the_setpoint_limit(victron_session, cfg: Config) -> None:
    # min(1000, maximaler Netzladesetpoint)
    session, cerbo = victron_session(answers=ONLY_OVERRIDE, config=with_cap(cfg, 600.0))
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert setpoints(cerbo) == [600.0, None]
    assert step(findings, "Netz-Sollwert-Override").detail.startswith("600 W nach ")


# Weitere Override-Schritte nach einem Override, der sich nicht aufheben ließ


async def test_after_a_stuck_override_no_further_override_is_written(victron_session) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[True, False, False], null_clears=False, asked=asked, state=CAR
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    # Entladesperre und Importlimit brauchen keinen Override und fragen weiter
    assert asked == [Q_OVERRIDE, Q_DISCHARGE, Q_IMPORT]
    reason = "übersprungen: Override ließ sich nicht aufheben"
    skipped = [f.title for f in findings if f.detail == reason]
    assert skipped == [
        "Override ohne Timeout",
        "Override nach Neustart des Cerbo",
        "Netzladen",
        "Kein Akku ins Auto",
    ]
    assert [v for v in setpoints(cerbo) if v is not None] == [1000.0]  # nur der erste Override
    # Schritte, die nichts schreiben, wiederholen das gescheiterte Wiederherstellen nicht
    restores = [f for f in findings if f.title == "Wiederherstellen: kein Netz-Sollwert-Override"]
    assert [(f.status, f.detail) for f in restores] == [("fail", "nicht bestätigt nach 0,2 s")]


async def test_without_a_car_the_reason_stays_the_missing_charging(victron_session) -> None:
    # Auch bei einem Override, der steht, nennt der Befund zuerst, dass kein Auto lädt.
    session, _ = victron_session(answers=[True, False, False], null_clears=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "info", "übersprungen: kein Laden an der EVCS"
    )


async def test_a_lost_release_that_the_restore_repeats_does_not_stop_the_later_steps(
    victron_session,
) -> None:
    # Das Aufheben im Schritt geht verloren, das Wiederherstellen danach kommt an: Der Override ist
    # weg, und der nächste Schritt läuft.
    session, cerbo = victron_session(answers=[True, True, False, False, False, False])
    cerbo.ignore_nulls = 1
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override").status == "fail"
    assert step(findings, "Wiederherstellen: kein Netz-Sollwert-Override").status == "ok"
    assert step(findings, "Override ohne Timeout").status == "ok"  # nicht übersprungen


# Override ohne Timeout


async def test_override_still_active_after_the_hold(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_HOLD)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override ohne Timeout") == Finding(
        "17.2-1", "Override ohne Timeout", "ok", "nach 0 min noch aktiv"
    )
    # danach hebt das Wiederherstellen ihn auf
    assert setpoints(cerbo) == [1000.0, None]
    assert cerbo.values["ess.override.setpoint"] is None


async def test_the_hold_lasts_hold_s(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_HOLD)
    started = asyncio.get_running_loop().time()
    await run_victron(session, session.cfg, timings=replace(FAST, hold_s=0.4))
    assert asyncio.get_running_loop().time() - started >= 0.4


async def test_override_that_vanishes_is_reported_with_the_time(victron_session) -> None:
    def vanishes(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            asyncio.get_running_loop().call_later(0.1, cerbo.put, "ess.override.setpoint", None)

    session, _ = victron_session(answers=ONLY_HOLD, plant=vanishes)
    findings = await run_victron(session, session.cfg, timings=replace(FAST, hold_s=0.6))
    assert step(findings, "Override ohne Timeout") == Finding(
        "17.2-1", "Override ohne Timeout", "info", "nach 0 min verschwunden"
    )


async def test_override_that_changes_is_a_warning(victron_session) -> None:
    def zeroed(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            asyncio.get_running_loop().call_later(0.1, cerbo.put, "ess.override.setpoint", 0.0)

    session, _ = victron_session(answers=ONLY_HOLD, plant=zeroed)
    findings = await run_victron(session, session.cfg, timings=replace(FAST, hold_s=0.6))
    assert step(findings, "Override ohne Timeout") == Finding(
        "17.2-1", "Override ohne Timeout", "warn", "nach 0 min geändert auf 0 W"
    )


async def test_a_hold_without_readings_at_the_end_is_not_called_active(victron_session) -> None:
    # Meldet der Cerbo den Override am Ende nicht (getrennt), lässt sich „noch aktiv“ nicht belegen.
    def drops(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            asyncio.get_running_loop().call_later(0.03, cerbo.disconnect)

    session, _ = victron_session(answers=ONLY_HOLD, plant=drops)
    findings = await run_victron(session, session.cfg, timings=replace(FAST, hold_s=0.3))
    assert step(findings, "Override ohne Timeout") == Finding(
        "17.2-1",
        "Override ohne Timeout",
        "warn",
        "nach 0 min nicht prüfbar: Override nicht gemeldet",
    )


async def test_hold_with_an_override_that_is_not_confirmed(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_HOLD, follow=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override ohne Timeout") == Finding(
        "17.2-1", "Override ohne Timeout", "fail", "Override 1000 W: nicht bestätigt nach 0,2 s"
    )


# Override nach dem Neustart des Cerbo

ASK_RESTART = [False, False, True, True, False, False, False]  # Neustart gewählt und bejaht


def restarts(*, keeps_override: bool, comes_back: bool = True):
    def react(cerbo: FakeCerbo) -> None:
        cerbo.restart(down_s=0.1, keeps_override=keeps_override, comes_back=comes_back)

    return {Q_RESTART: react}


async def test_override_is_gone_after_the_restart(
    victron_session, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=ASK_RESTART, react=restarts(keeps_override=False), asked=asked
    )
    findings = await run_victron(session, session.cfg, timings=REBOOT)
    assert asked[2:4] == [Q_REBOOT, Q_RESTART]
    # Die Rückfrage hält die Adapter an: Der Betreiber antwortet zuerst und startet dann neu.
    assert "Jetzt den Cerbo neu starten" in capsys.readouterr().out
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1", "Override nach Neustart des Cerbo", "ok", "nach dem Neustart aufgehoben"
    )
    assert cerbo.values["ess.override.setpoint"] is None


async def test_override_still_active_after_the_restart_is_a_warning(victron_session) -> None:
    session, cerbo = victron_session(answers=ASK_RESTART, react=restarts(keeps_override=True))
    findings = await run_victron(session, session.cfg, timings=REBOOT)
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1",
        "Override nach Neustart des Cerbo",
        "warn",
        "nach dem Neustart noch aktiv (1000 W)",
    )
    # das Wiederherstellen danach hebt ihn auf
    after = findings.index(step(findings, "Override nach Neustart des Cerbo")) + 1
    assert findings[after].title == "Wiederherstellen: kein Netz-Sollwert-Override"
    assert findings[after].status == "ok"
    assert cerbo.values["ess.override.setpoint"] is None


async def test_no_restart_is_a_warning(victron_session) -> None:
    # Der Betreiber hat bejaht, doch der Cerbo bleibt verbunden.
    session, _ = victron_session(answers=ASK_RESTART)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1",
        "Override nach Neustart des Cerbo",
        "warn",
        "Cerbo nach 0,2 s nicht getrennt; kein Neustart erkannt",
    )


async def test_a_cerbo_that_does_not_come_back_is_a_warning(victron_session) -> None:
    session, _ = victron_session(
        answers=ASK_RESTART, react=restarts(keeps_override=True, comes_back=False)
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1",
        "Override nach Neustart des Cerbo",
        "warn",
        "Cerbo nach 0,2 s nicht wieder verbunden",
    )
    # lösen lässt sich der Override jetzt nicht: das Wiederherstellen meldet es
    restore = step(findings, "Wiederherstellen: kein Netz-Sollwert-Override")
    assert (restore.status, restore.detail) == ("fail", "nicht steuerbar: Cerbo nicht verbunden.")


async def test_the_restart_step_waits_until_the_cerbo_reports_the_override_again(
    victron_session,
) -> None:
    # Nach dem Verbinden gilt der Override aus der Zeit vor dem Neustart nicht mehr, bis der Cerbo
    # ihn erneut meldet. Wer ihn schon dann liest, sähe „kein Override“, denn es fehlt der Wert.
    def restart(cerbo: FakeCerbo) -> None:
        cerbo.restart(down_s=0.1, keeps_override=True, republish_s=0.2)

    session, _ = victron_session(answers=ASK_RESTART, react={Q_RESTART: restart})
    findings = await run_victron(session, session.cfg, timings=REBOOT)
    assert step(findings, "Override nach Neustart des Cerbo").detail == (
        "nach dem Neustart noch aktiv (1000 W)"
    )


async def test_restart_step_with_an_override_that_is_not_confirmed(victron_session) -> None:
    asked: list[str] = []
    session, _ = victron_session(
        answers=[False, False, True, False, False, False], follow=False, asked=asked
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1",
        "Override nach Neustart des Cerbo",
        "fail",
        "Override 1000 W: nicht bestätigt nach 0,2 s",
    )
    assert Q_RESTART not in asked  # ohne Override gibt es nichts zu prüfen


async def test_a_declined_restart_leaves_the_override_to_the_restore(victron_session) -> None:
    session, cerbo = victron_session(answers=[False, False, True, False, False, False, False])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1", "Override nach Neustart des Cerbo", "info", "übersprungen"
    )
    assert setpoints(cerbo) == [1000.0, None]


async def test_the_restart_step_needs_the_operator(victron_session) -> None:
    # Mit --yes startet niemand den Cerbo neu.
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[False, False, False, False], asked=asked, unattended=True
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert Q_REBOOT not in asked and Q_RESTART not in asked
    assert step(findings, "Override nach Neustart des Cerbo") == Finding(
        "17.2-1", "Override nach Neustart des Cerbo", "info", "übersprungen (--yes)"
    )
    assert cerbo.writes == []


# Zusammenspiel mit dem persistenten Sollwert


async def test_the_persistent_setpoint_stays_unchanged(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_OVERRIDE)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Zusammenspiel mit dem persistenten Sollwert") == Finding(
        "17.2-1",
        "Zusammenspiel mit dem persistenten Sollwert",
        "info",
        "AcPowerSetPoint 50 W blieb unverändert",
    )
    assert {path for path, _ in cerbo.writes} == {SETPOINT}  # der persistente Wert bleibt tabu


async def test_a_changed_persistent_setpoint_is_named(victron_session) -> None:
    def changes(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is None:
            cerbo.put("ess.grid_setpoint_w", 120.0)

    session, _ = victron_session(answers=ONLY_OVERRIDE, plant=changes)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Zusammenspiel mit dem persistenten Sollwert").detail == (
        "AcPowerSetPoint änderte sich von 50 W auf 120 W"
    )


async def test_the_persistent_setpoint_is_read_after_all_override_steps(victron_session) -> None:
    # Es ändert sich erst im Schritt „Override ohne Timeout“.
    def changes(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None and len(setpoints(cerbo)) >= 2:
            cerbo.put("ess.grid_setpoint_w", 0.0)

    session, _ = victron_session(answers=[True, True, False, False, False, False], plant=changes)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Zusammenspiel mit dem persistenten Sollwert").detail == (
        "AcPowerSetPoint änderte sich von 50 W auf 0 W"
    )


async def test_an_unknown_persistent_setpoint_is_not_called_unchanged(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_OVERRIDE, without=["ess.grid_setpoint_w"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Zusammenspiel mit dem persistenten Sollwert") == Finding(
        "17.2-1",
        "Zusammenspiel mit dem persistenten Sollwert",
        "info",
        "AcPowerSetPoint vorher unbekannt, nachher unbekannt",
    )


# Entladesperre


async def test_discharge_lock_is_confirmed_and_the_limit_restored(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_DISCHARGE)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2", "Entladesperre", "ok", "Akku entlädt höchstens 50 W"
    )
    assert limits(cerbo) == [0.0, 5000.0]  # Sperre, dann der Originalwert
    assert cerbo.values["ess.max_discharge_w"] == 5000.0
    restore = step(findings, "Wiederherstellen: Entladegrenze 5000 W")
    assert (restore.item, restore.status) == ("17.2-2", "ok")
    assert findings.index(restore) == findings.index(step(findings, "Entladesperre")) + 1


@pytest.mark.parametrize(
    ("battery_w", "status", "detail"),
    [
        (-100.0, "ok", "Akku entlädt höchstens 100 W"),  # der Rand gehört dazu
        (-100.4, "ok", "Akku entlädt höchstens 100 W"),  # entschieden wird nach dem gezeigten Wert
        (-101.0, "warn", "Akku entlädt bis zu 101 W; erwartet höchstens 100 W"),
        (-800.0, "warn", "Akku entlädt bis zu 800 W; erwartet höchstens 100 W"),
        (300.0, "ok", "Akku entlädt höchstens 0 W"),  # der Akku lädt
    ],
)
async def test_discharge_lock_verdict(
    victron_session, battery_w: float, status: str, detail: str
) -> None:
    session, _ = victron_session(answers=ONLY_DISCHARGE, state={"battery.power_w": battery_w})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding("17.2-2", "Entladesperre", status, detail)


async def test_discharge_lock_judges_the_end_of_the_observation(victron_session) -> None:
    # Gleich nach dem Schreiben entlädt der Akku noch mit 800 W und fällt dann ab: Das Ende zählt.
    def fades(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == DISCHARGE and value == 0.0:
            cerbo.put("battery.power_w", -800.0)
            asyncio.get_running_loop().call_later(0.08, cerbo.put, "battery.power_w", -20.0)

    session, _ = victron_session(answers=ONLY_DISCHARGE, plant=fades)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre").detail == "Akku entlädt höchstens 20 W"


async def test_discharge_lock_without_battery_readings(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_DISCHARGE, without=["battery.power_w"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2", "Entladesperre", "warn", "Akkuleistung nicht gemeldet"
    )


async def test_the_override_for_the_discharge_limit_is_named(victron_session) -> None:
    session, _ = victron_session(
        answers=NO, state={"ess.override.maxdischargepower": None}
    )  # gemeldet, aber ohne Wert
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Override für die Entladegrenze") == Finding(
        "17.2-2",
        "Override für die Entladegrenze",
        "info",
        "ess.override.maxdischargepower existiert",
    )


async def test_declined_discharge_lock_writes_nothing(victron_session) -> None:
    session, cerbo = victron_session(answers=NO)
    await run_victron(session, session.cfg, timings=FAST)
    assert limits(cerbo) == []


async def test_discharge_limit_with_an_unknown_original_is_not_written(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_DISCHARGE, without=["ess.max_discharge_w"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2",
        "Entladesperre",
        "fail",
        "Entladegrenze 0 W: nicht gesendet: Ausgangswert unbekannt; er ließe sich nicht "
        "wiederherstellen.",
    )
    assert cerbo.writes == []


async def test_unconfirmed_discharge_lock_is_restored_once_per_call(victron_session) -> None:
    # Persistente Einstellungen gehen je Aufruf höchstens einmal hinaus, auch ohne Bestätigung.
    session, cerbo = victron_session(answers=ONLY_DISCHARGE, follow=False, resend_s=0.05)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2", "Entladesperre", "fail", "Entladegrenze 0 W: nicht bestätigt nach 0,2 s"
    )
    assert limits(cerbo) == [0.0, 5000.0]  # trotz resend_s: der Schritt sendet sie nur einmal


async def test_no_min_soc_is_ever_written(victron_session) -> None:
    session, cerbo = victron_session(
        answers=[True, True, False, True, True, True, False], state=CAR, plant=charges
    )
    await run_victron(session, session.cfg, timings=FAST)
    assert {path for path, _ in cerbo.writes} <= {SETPOINT, DISCHARGE}


# Netzladen


async def test_grid_charging_names_the_charge_power(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_CHARGE, plant=charges)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3", "Netzladen", "ok", "Akku lädt mit 1400 W"
    )
    assert setpoints(cerbo) == [2000.0, None]  # der Override geht wieder weg


@pytest.mark.parametrize(
    ("battery_w", "status", "detail"),
    [
        (501.0, "ok", "Akku lädt mit 501 W"),
        (500.0, "warn", "Akkuleistung 500 W (positiv = Laden), erwartet über 500 W"),
        (-50.0, "warn", "Akkuleistung −50 W (positiv = Laden), erwartet über 500 W"),
    ],
)
async def test_grid_charging_verdict(
    victron_session, battery_w: float, status: str, detail: str
) -> None:
    session, cerbo = victron_session(answers=ONLY_CHARGE, state={"battery.power_w": battery_w})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding("17.2-3", "Netzladen", status, detail)
    assert setpoints(cerbo) == [2000.0, None]  # auch bei WARNUNG wird der Override aufgehoben


async def test_grid_charging_counts_when_the_power_was_reached_within_the_observation(
    victron_session,
) -> None:
    # „Binnen effect_s“: Lädt der Akku zu Beginn mit 1400 W und fällt dann ab, zeigt das Fenster
    # mit der höchsten Leistung, dass das Netzladen wirkt.
    def fades(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            cerbo.put("battery.power_w", 1400.0)
            asyncio.get_running_loop().call_later(0.12, cerbo.put, "battery.power_w", -50.0)

    session, _ = victron_session(answers=ONLY_CHARGE, plant=fades)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3", "Netzladen", "ok", "Akku lädt mit 1400 W"
    )


async def test_grid_charging_averages_the_window(victron_session) -> None:
    # Eine einzelne Spitze über 500 W genügt nicht: Das Fenster von observe_s zählt als Mittel.
    def spike(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            cerbo.put("battery.power_w", 2000.0)
            asyncio.get_running_loop().call_later(0.01, cerbo.put, "battery.power_w", -50.0)

    session, _ = victron_session(answers=ONLY_CHARGE, plant=spike)
    timings = replace(FAST, observe_s=0.1)
    findings = await run_victron(session, session.cfg, timings=timings)
    assert step(findings, "Netzladen").status == "warn"


async def test_grid_charging_without_battery_readings(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_CHARGE, without=["battery.power_w"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3", "Netzladen", "warn", "Akkuleistung nicht gemeldet"
    )


async def test_grid_charging_keeps_the_result_when_the_release_fails(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_CHARGE, plant=charges, null_clears=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3",
        "Netzladen",
        "fail",
        "Akku lädt mit 1400 W; Aufheben mit null nicht bestätigt; Weg für die Rücknahme klären.",
    )


async def test_grid_charging_is_at_most_the_setpoint_limit(victron_session, cfg: Config) -> None:
    # min(2000, maximaler Netzladesetpoint)
    asked: list[str] = []
    session, cerbo = victron_session(answers=ONLY_CHARGE, config=with_cap(cfg, 1500.0), asked=asked)
    await run_victron(session, session.cfg, timings=FAST)
    assert setpoints(cerbo) == [1500.0, None]
    assert asked[4] == (
        "Netzladen testen: Override kurz auf 1500 W setzen? Der Akku lädt dann aus dem Netz."
    )


async def test_unconfirmed_grid_charging_is_an_error(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_CHARGE, follow=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3", "Netzladen", "fail", "Override 2000 W: nicht bestätigt nach 0,2 s"
    )


# Kein Akku ins Auto


def covered(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
    """Die Anlage: Mit Override entlädt der Akku mit 30 W, ohne mit 50 W."""
    if path == SETPOINT:
        cerbo.put("battery.power_w", -30.0 if value is not None else -50.0)


async def test_no_battery_into_the_car(victron_session) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(answers=ONLY_CAR, state=CAR, plant=covered, asked=asked)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert Q_CAR in asked
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "ok", "Akku entlädt höchstens 30 W"
    )
    assert setpoints(cerbo) == [3700.0, None]  # die Ladeleistung in ganzen Watt, dann aufgehoben


@pytest.mark.parametrize("power", [None, 0.0, 999.0, 1000.0])
async def test_no_car_charging_skips_the_step_without_a_question(
    victron_session, power: float | None
) -> None:
    asked: list[str] = []
    state = {} if power is None else {"wallbox.evcs.power_w": power}
    session, cerbo = victron_session(answers=NO, state=state, asked=asked)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "info", "übersprungen: kein Laden an der EVCS"
    )
    assert len(asked) == 6 and cerbo.writes == []  # keine Rückfrage für diesen Schritt


async def test_a_car_above_the_threshold_is_asked(victron_session) -> None:
    asked: list[str] = []
    state = {"wallbox.evcs.power_w": 1001.0}
    session, _ = victron_session(answers=ONLY_CAR, state=state, asked=asked)
    await run_victron(session, session.cfg, timings=FAST)
    assert asked[5] == (
        "Die EVCS lädt mit 1001 W. Override auf 1001 W setzen? "
        "Der Akku soll nicht ins Auto entladen."
    )


async def test_the_override_for_the_car_is_capped_and_the_detail_names_the_cap(
    victron_session, cfg: Config
) -> None:
    session, cerbo = victron_session(
        answers=ONLY_CAR, state=CAR, plant=covered, config=with_cap(cfg, 2500.0)
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert setpoints(cerbo) == [2500.0, None]
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4",
        "Kein Akku ins Auto",
        "ok",
        "Akku entlädt höchstens 30 W (Override auf 2500 W begrenzt; EVCS-Leistung 3700 W)",
    )


async def test_the_override_for_the_car_stays_inside_a_fractional_limit(
    victron_session, cfg: Config
) -> None:
    # Gerundet auf ganze Watt, doch nie über der Grenze: 2500,6 W wird nicht zu 2501 W (die Regeln
    # 1–3 würden 2501 W klemmen, und die Sitzung wiese ihn als Fehler des Werkzeugs ab).
    session, cerbo = victron_session(
        answers=ONLY_CAR, state=CAR, plant=covered, config=with_cap(cfg, 2500.6)
    )
    await run_victron(session, session.cfg, timings=FAST)
    assert setpoints(cerbo) == [2500.0, None]


async def test_the_cap_is_not_named_when_it_does_not_apply(victron_session, cfg: Config) -> None:
    session, _ = victron_session(
        answers=ONLY_CAR, state=CAR, plant=covered, config=with_cap(cfg, 3700.0)
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto").detail == "Akku entlädt höchstens 30 W"


async def test_the_power_of_all_evcs_counts(victron_session, cfg: Config) -> None:
    # Jede EVCS kann ein Auto laden; der Wall Connector gehört nicht dazu.
    wallboxes = {**cfg.wallboxes, "evcs2": cfg.wallboxes["evcs"]}
    config = cfg.model_copy(update={"wallboxes": wallboxes})
    asked: list[str] = []
    state = {
        "wallbox.evcs.power_w": 2000.0,
        "wallbox.evcs2.power_w": 1500.0,
        "wallbox.twc.power_w": 9000.0,
    }
    session, cerbo = victron_session(
        answers=ONLY_CAR, state=state, plant=covered, config=config, asked=asked
    )
    await run_victron(session, session.cfg, timings=FAST)
    assert setpoints(cerbo) == [3500.0, None]
    assert asked[5].startswith("Die EVCS lädt mit 3500 W.")


@pytest.mark.parametrize(
    ("battery_w", "status", "detail"),
    [
        (-200.0, "ok", "Akku entlädt höchstens 200 W"),
        (-400.0, "warn", "Akku entlädt bis zu 400 W; erwartet höchstens 200 W"),
    ],
)
async def test_no_battery_into_the_car_verdict(
    victron_session, battery_w: float, status: str, detail: str
) -> None:
    session, cerbo = victron_session(answers=ONLY_CAR, state=CAR | {"battery.power_w": battery_w})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", status, detail
    )
    assert setpoints(cerbo) == [3700.0, None]


async def test_the_car_step_still_asks_with_yes(victron_session) -> None:
    # Die Rückfrage zum Auto ist keine Handarbeit: Mit --yes wird sie beantwortet.
    asked: list[str] = []
    session, cerbo = victron_session(answers=[False] * 5, state=CAR, asked=asked, unattended=True)
    await run_victron(session, session.cfg, timings=FAST)
    assert Q_CAR in asked and cerbo.writes == []


async def test_a_declined_car_step_writes_nothing(victron_session) -> None:
    session, cerbo = victron_session(answers=[False] * 7, state=CAR)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "info", "übersprungen"
    )
    assert cerbo.writes == []


async def test_a_car_that_stopped_charging_during_the_question_is_not_followed(
    victron_session,
) -> None:
    def stops(cerbo: FakeCerbo) -> None:
        cerbo.put("wallbox.evcs.power_w", 20.0)

    session, cerbo = victron_session(answers=ONLY_CAR, state=CAR, react={Q_CAR: stops})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "info", "übersprungen: kein Laden an der EVCS"
    )
    assert cerbo.writes == []


async def test_the_car_step_keeps_the_result_when_the_release_fails(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_CAR, state=CAR, plant=covered, null_clears=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4",
        "Kein Akku ins Auto",
        "fail",
        "Akku entlädt höchstens 30 W; Aufheben mit null nicht bestätigt; "
        "Weg für die Rücknahme klären.",
    )


async def test_the_car_override_that_is_not_confirmed_is_an_error(victron_session) -> None:
    session, _ = victron_session(answers=ONLY_CAR, state=CAR, follow=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Kein Akku ins Auto") == Finding(
        "17.2-4", "Kein Akku ins Auto", "fail", "Override 3700 W: nicht bestätigt nach 0,2 s"
    )


# Importlimit


def load_on(cerbo: FakeCerbo) -> None:
    """Der Betreiber schaltet eine Last ein: kurz danach 21,74 A auf L2 und der Akku entlädt."""

    def switch() -> None:
        cerbo.put("grid.l2.current_a", 21.74)
        cerbo.put("battery.power_w", -3200.0)

    asyncio.get_running_loop().call_later(0.05, switch)


async def test_import_limit_names_the_highest_phase_current(victron_session) -> None:
    session, cerbo = victron_session(
        answers=[False] * 5 + [True], state={"grid.l1.current_a": 9.0}, react={Q_IMPORT: load_on}
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Importlimit") == Finding(
        "17.2-8", "Importlimit", "info", "höchster Netzstrom 21,7 A (L2), Akku −3200 W"
    )
    assert cerbo.writes == []  # der Schritt schreibt nichts


async def test_import_limit_without_a_rise(victron_session) -> None:
    session, _ = victron_session(answers=[False] * 5 + [True])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Importlimit") == Finding(
        "17.2-8", "Importlimit", "info", "höchster Netzstrom 0,5 A (L1), Akku −50 W"
    )


async def test_import_limit_without_currents(victron_session) -> None:
    currents = ["grid.l1.current_a", "grid.l2.current_a", "grid.l3.current_a"]
    session, _ = victron_session(answers=[False] * 5 + [True], without=currents)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Importlimit") == Finding(
        "17.2-8", "Importlimit", "warn", "keine Netzströme gemeldet"
    )


async def test_import_limit_without_battery_readings(victron_session) -> None:
    session, _ = victron_session(answers=[False] * 5 + [True], without=["battery.power_w"])
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Importlimit").detail == "höchster Netzstrom 0,5 A (L1), Akku unbekannt"


async def test_import_limit_needs_the_operator(victron_session) -> None:
    asked: list[str] = []
    session, _ = victron_session(answers=[False] * 4, asked=asked, unattended=True)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert Q_IMPORT not in asked
    assert step(findings, "Importlimit") == Finding(
        "17.2-8", "Importlimit", "info", "übersprungen (--yes)"
    )


# Wiederherstellen nach jedem Schritt (Präzisierung 11)


async def test_each_step_is_undone_before_the_next(victron_session) -> None:
    asked: list[str] = []
    seen: list[tuple[object, object]] = []

    def look(cerbo: FakeCerbo) -> None:
        seen.append((cerbo.values["ess.override.setpoint"], cerbo.values["ess.max_discharge_w"]))

    session, cerbo = victron_session(
        answers=[True] * 4,
        unattended=True,
        asked=asked,
        plant=charges,
        react={Q_HOLD: look, Q_DISCHARGE: look, Q_CHARGE: look},
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert asked == [Q_OVERRIDE, Q_HOLD, Q_DISCHARGE, Q_CHARGE]  # nie der Neustart, nie die Last
    assert seen == [(None, 5000.0)] * 3  # vor jeder Rückfrage steht wieder, was vorgefunden wurde
    assert [(f.title, f.status) for f in findings] == [
        ("Victron bereit", "ok"),
        ("Netz-Sollwert-Override", "ok"),
        ("Override ohne Timeout", "ok"),
        ("Wiederherstellen: kein Netz-Sollwert-Override", "ok"),
        ("Override nach Neustart des Cerbo", "info"),
        ("Zusammenspiel mit dem persistenten Sollwert", "info"),
        ("Override für die Entladegrenze", "info"),
        ("Entladesperre", "ok"),
        ("Wiederherstellen: Entladegrenze 5000 W", "ok"),
        ("Netzladen", "ok"),
        ("Kein Akku ins Auto", "info"),
        ("Importlimit", "info"),
    ]
    assert await session.restore() == []  # am Ende ist nichts mehr offen
    assert {path for path, _ in cerbo.writes} <= {SETPOINT, DISCHARGE}


async def test_a_run_that_starts_without_an_override_ends_with_it_lifted(victron_session) -> None:
    # Vorgefunden ist „kein Override“ (sonst endet „bereit“); danach hebt jeder Block ihn auf, auch
    # der Schritt, der ihn stehen lässt (Ohne Timeout).
    session, cerbo = victron_session(answers=[True] * 4, unattended=True, plant=charges)
    await run_victron(session, session.cfg, timings=FAST)
    assert cerbo.values["ess.override.setpoint"] is None
    assert any(v is not None for v in setpoints(cerbo))  # er war gesetzt
    assert setpoints(cerbo)[-1] is None
    assert await session.restore() == []


# Dynamic ESS vor jedem schreibenden Schritt: Geht es mitten im Lauf an, schreibt der Teil nichts
# mehr; das Wiederherstellen übernimmt der Aufrufer wie bei einem Abbruch.

DESS_ON = "Dynamic ESS ist aktiv (Modus 1); die Victron-Punkte erst nach dem Abschalten von DESS."


def dess_on(cerbo: FakeCerbo) -> None:
    cerbo.put("dess.mode", 1)


async def test_dess_switched_on_before_the_discharge_lock_stops_the_writes(
    victron_session,
) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[True] * 7, unattended=True, asked=asked, react={Q_DISCHARGE: dess_on}
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Entladesperre") == Finding(
        "17.2-2", "Entladesperre", "fail", f"Entladegrenze 0 W: {DESS_ON}"
    )
    assert limits(cerbo) == []  # die Sperre ging nicht hinaus
    assert asked[-1] == Q_DISCHARGE  # danach keine Rückfrage mehr
    assert findings[-1].title == "Entladesperre"  # und kein weiterer Schritt
    assert await session.restore() == []  # nichts mehr offen


async def test_dess_switched_on_during_the_first_question_writes_nothing(victron_session) -> None:
    asked: list[str] = []
    session, cerbo = victron_session(answers=[True] * 7, asked=asked, react={Q_OVERRIDE: dess_on})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[1:] == [
        Finding("17.2-1", "Netz-Sollwert-Override", "fail", f"Override 1000 W: {DESS_ON}")
    ]
    assert cerbo.writes == [] and asked == [Q_OVERRIDE]


async def test_dess_is_checked_before_each_writing_step(victron_session) -> None:
    # Der Override-Schritt läuft durch; vor dem Netzladen ist DESS an.
    asked: list[str] = []
    session, cerbo = victron_session(
        answers=[True, False, False, False, True, False],
        asked=asked,
        plant=charges,
        react={Q_CHARGE: dess_on},
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override").status == "ok"
    assert step(findings, "Netzladen") == Finding(
        "17.2-3", "Netzladen", "fail", f"Override 2000 W: {DESS_ON}"
    )
    assert setpoints(cerbo) == [1000.0, None]  # nur der erste Schritt schrieb
    assert asked[-1] == Q_CHARGE


async def test_an_unknown_dess_mode_before_a_write_counts_as_active(victron_session) -> None:
    def unknown(cerbo: FakeCerbo) -> None:
        cerbo.put("dess.mode", None)

    session, cerbo = victron_session(answers=[True] * 7, react={Q_OVERRIDE: unknown})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override").detail == (
        "Override 1000 W: Dynamic ESS ist aktiv (Modus unbekannt); die Victron-Punkte erst nach "
        "dem Abschalten von DESS."
    )
    assert cerbo.writes == []


# Ein Aufheben mit null, das nie hinausging, ist kein Befund über null


def refuses_after_the_override(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
    """Die Anlage: Nach dem ersten Override nimmt der Cerbo keinen Auftrag mehr an."""
    if path == SETPOINT and value is not None:
        cerbo.accept = False


async def test_a_release_that_was_never_sent_names_the_send_error(victron_session) -> None:
    session, cerbo = victron_session(answers=ONLY_OVERRIDE, plant=refuses_after_the_override)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override") == Finding(
        "17.2-1",
        "Netz-Sollwert-Override",
        "fail",
        "Aufheben mit null: nicht gesendet: Cerbo-Verbindung: Auftrag nicht angenommen.",
    )
    assert setpoints(cerbo) == [1000.0]  # das null ging nie hinaus


async def test_a_release_without_the_cerbo_names_the_send_error(victron_session) -> None:
    def drops(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        if path == SETPOINT and value is not None:
            cerbo.connected = False  # der Aktor sieht den Cerbo getrennt; 1000 W sind bestätigt

    session, _ = victron_session(answers=ONLY_OVERRIDE, plant=drops)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netz-Sollwert-Override").detail == (
        "Aufheben mit null: nicht steuerbar: Cerbo nicht verbunden."
    )


async def test_a_forced_release_that_was_never_sent_names_the_send_error(victron_session) -> None:
    def charges_then_refuses(cerbo: FakeCerbo, path: str, value: float | int | None) -> None:
        charges(cerbo, path, value)
        refuses_after_the_override(cerbo, path, value)

    session, _ = victron_session(answers=ONLY_CHARGE, plant=charges_then_refuses)
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert step(findings, "Netzladen") == Finding(
        "17.2-3",
        "Netzladen",
        "fail",
        "Akku lädt mit 1400 W; Aufheben mit null: nicht gesendet: Cerbo-Verbindung: Auftrag "
        "nicht angenommen.",
    )


# Kein Akku ins Auto: geschrieben wird der Wert, den der Betreiber bestätigt hat


async def test_the_car_step_writes_the_value_the_operator_confirmed(victron_session) -> None:
    def more_power(cerbo: FakeCerbo) -> None:
        cerbo.put("wallbox.evcs.power_w", 4200.0)  # das Auto lädt inzwischen stärker

    asked: list[str] = []
    session, cerbo = victron_session(
        answers=ONLY_CAR, state=CAR, plant=covered, asked=asked, react={Q_CAR: more_power}
    )
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert Q_CAR in asked  # die Rückfrage nannte 3700 W
    assert setpoints(cerbo) == [3700.0, None]
    assert step(findings, "Kein Akku ins Auto").status == "ok"
