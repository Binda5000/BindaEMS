"""Tests des Berichts eines Simulatorlaufs (``report.render_report``) an gebauten Ergebnissen."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

from bindaems.shared.config import Config
from bindaems.shared.control import CommandKind
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.tools.sim.metrics import CarSample, Sample, measure
from bindaems.tools.sim.report import render_report
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import SCENARIOS, Check, Checks

CONFIG: Final = "deploy/config.example.yaml"
T0: Final = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit, Start von „reserve“


def quiet(t: int, *, l1: float = 0.0) -> Sample:
    """Die Anlage nach Sekunde ``t``: der e-Golf lädt mit 10 A, L1 mit ``l1`` A am Netz."""
    car = CarSample(True, 10.0, 4600.0, "now", 6)
    grid = {"L1": l1, "L2": 0.0, "L3": 0.0}
    return Sample(t, grid, dict.fromkeys(grid, 0.0), 0.0, 50.0, {"egolf": car})


def decision(
    seconds: int,
    actuator: str,
    kind: CommandKind,
    value: object,
    result: DecisionResult,
    *codes: str,
) -> Decision:
    return Decision(
        id=f"{actuator}/{kind}/{seconds}",
        ts=T0 + timedelta(seconds=seconds),
        actuator=actuator,
        kind=kind,
        requested=value,  # type: ignore[arg-type]
        applied=value,  # type: ignore[arg-type]
        dry_run=result is DecisionResult.DRY_RUN,
        outcome="accepted",
        result=result,
        codes=codes,
        text="",
        inputs={},
    )


def result_of(
    cfg: Config, samples: list[Sample], decisions: list[Decision], checks: Checks
) -> SimResult:
    scenario = replace(SCENARIOS["reserve"], checks=checks)
    return SimResult(scenario, measure(samples, decisions, cfg), decisions, [], samples, [])


def passing(result: SimResult) -> list[str]:
    return []


def failing(result: SimResult) -> list[str]:
    return ["Erster Verstoß.", "Zweiter Verstoß."]


CHECKS: Final = Checks((Check("Alles gut.", passing), Check("Nichts geht.", failing)))


def section(text: str, heading: str) -> list[str]:
    """Die Zeilen eines Abschnitts ``## heading`` bis zum nächsten."""
    lines = text.splitlines()
    start = lines.index(f"## {heading}") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [line for line in lines[start:end] if line]


def test_the_report_names_scenario_time_and_config(cfg) -> None:
    text = render_report(result_of(cfg, [quiet(t) for t in range(360)], [], CHECKS), CONFIG)
    lines = text.splitlines()
    assert lines[0] == "# Simulation „Sofort knapp über der USV-Reserve“"
    assert lines[2:7] == [
        "| Angabe | Wert |",
        "|---|---|",
        "| Szenario | reserve |",
        "| Simulierte Zeit (Wiener Zeit) | 12.10.2026 18:00 bis 18:06 |",
        f"| Konfiguration | {CONFIG} |",
    ]


def test_a_run_over_midnight_names_both_days(cfg) -> None:
    late = replace(SCENARIOS["reserve"], start=datetime(2026, 10, 12, 21, 30, tzinfo=UTC))
    result = replace(result_of(cfg, [quiet(t) for t in range(3600)], [], CHECKS), scenario=late)
    text = render_report(result, CONFIG)
    assert "| Simulierte Zeit (Wiener Zeit) | 12.10.2026 23:30 bis 13.10.2026 00:30 |" in text


def test_the_report_rates_every_metric(cfg) -> None:
    samples = [quiet(t, l1=36.0 if t == 5 else 1.0) for t in range(60)]
    text = render_report(result_of(cfg, samples, [], CHECKS), CONFIG)
    rows = section(text, "Kennzahlen")
    assert rows[:2] == ["| Kennzahl | Wert | Grenze | Ergebnis |", "|---|---|---|---|"]
    assert len(rows) == 2 + 10  # je Kennzahl eine Zeile
    assert rows[2] == "| Höchster Phasenstrom | 36 A | 35 A | FEHLER |"
    assert rows[3] == "| Längste Zeit über 33 A | 1 s | 10 s | OK |"
    assert rows[-1] == "| Wiederholte Dry-Run-Entscheidungen | 0 | 0 | OK |"


def test_each_check_gets_a_line_with_ok_or_its_findings(cfg) -> None:
    text = render_report(result_of(cfg, [quiet(0)], [], CHECKS), CONFIG)
    assert section(text, "Szenario-Prüfungen") == [
        "| Ergebnis | Prüfung |",
        "|---|---|",
        "| OK | Alles gut. |",
        "| FEHLER | Erster Verstoß. |",
        "| FEHLER | Zweiter Verstoß. |",
    ]


def test_table_cells_escape_the_pipe(cfg) -> None:
    def odd(result: SimResult) -> list[str]:
        return ["L1 | L2\nzu hoch."]

    checks = Checks((Check("a | b", passing), Check("c", odd)))
    text = render_report(result_of(cfg, [quiet(0)], [], checks), CONFIG)
    assert section(text, "Szenario-Prüfungen")[2:] == [
        "| OK | a \\| b |",
        "| FEHLER | L1 \\| L2<br>zu hoch. |",
    ]


def test_checks_without_a_text_show_their_findings(cfg) -> None:
    def lines_with(checks: object) -> list[str]:
        result = result_of(cfg, [quiet(0)], [], CHECKS)
        result = replace(result, scenario=replace(result.scenario, checks=checks))
        return section(render_report(result, CONFIG), "Szenario-Prüfungen")[2:]

    assert lines_with(failing) == ["| FEHLER | Erster Verstoß. |", "| FEHLER | Zweiter Verstoß. |"]
    assert lines_with(passing) == ["| OK | Keine Verstöße. |"]


def test_the_report_counts_decisions_and_reasons(cfg) -> None:
    evcs, tesla = "wallbox.evcs", "vehicle.tesla"
    current, charging = CommandKind.CURRENT, CommandKind.CHARGING
    ok, error = DecisionResult.OK, DecisionResult.ERROR
    decisions = [
        decision(61, evcs, current, 13.0, ok, "mode_now", "reserve_guard"),
        decision(62, evcs, charging, True, ok, "mode_now", "reserve_guard"),
        decision(90, tesla, current, 5.0, error, "mode_pv", "pv_surplus"),
        decision(180, tesla, current, 5.0, ok, "mode_pv", "pv_surplus", "not_taken_retry"),
        decision(300, evcs, current, 12.0, ok, "mode_now", "reserve_guard"),
    ]
    text = render_report(result_of(cfg, [quiet(0)], decisions, CHECKS), CONFIG)
    rows = section(text, "Entscheidungen")
    assert rows[:6] == [
        "| Aktor | Art | Ergebnis | Anzahl |",
        "|---|---|---|---|",
        "| vehicle.tesla | current | error | 1 |",
        "| vehicle.tesla | current | ok | 1 |",
        "| wallbox.evcs | charging | ok | 1 |",
        "| wallbox.evcs | current | ok | 2 |",
    ]
    assert rows[6:] == [
        "| Grund | Anzahl |",
        "|---|---|",
        "| mode_now | 3 |",
        "| reserve_guard | 3 |",
        "| mode_pv | 2 |",
        "| pv_surplus | 2 |",
        "| not_taken_retry | 1 |",
    ]


def test_a_run_without_decisions_says_so(cfg) -> None:
    text = render_report(result_of(cfg, [quiet(0)], [], CHECKS), CONFIG)
    assert section(text, "Entscheidungen") == ["Keine Entscheidungen."]
    assert text.endswith("Keine Entscheidungen.\n")
