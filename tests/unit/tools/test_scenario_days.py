"""Tests von ``scenario_days`` und dem Bericht (Plan 3b, Task 16)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import structlog

from bindaems.tools import scenario_days
from bindaems.tools.scenario.data import ScenarioFile
from bindaems.tools.scenario.report import passed, render_report
from bindaems.tools.scenario.runner import DayResult, ScenarioResult
from bindaems.tools.scenario.synthetic import synthetic
from bindaems.tools.scenario_days import main


def result_of(**changes: object) -> ScenarioResult:
    day = DayResult(
        day=date(2027, 1, 11),
        baseline_ct=250.0,
        optimizer_ct=210.0,
        grid_charge_kwh=4.26,
        hold_slots=3,
        r1_violations=0,
        fallbacks=1,
    )
    values: dict[str, object] = {
        "name": "Probe",
        "step_slots": 4,
        "days": [day],
        "baseline_ct": 250.0,
        "optimizer_ct": 210.0,
        "adjusted_optimizer_ct": 205.0,
        "baseline_power_ct": 0.0,
        "optimizer_power_ct": 0.0,
        "r1_violations": 0,
        "charge_before_export_kwh": 1.5,
        "min_soc_kwh": 6.0,
        "reserve_kwh": 6.0,
        "plans": 24,
        "fallbacks": 1,
    }
    values.update(changes)
    return ScenarioResult(**values)  # type: ignore[arg-type]


def test_scenario_days_report_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bericht.md"
    code = main(
        ["--synthetisch", "winter-spread", "--anzahl", "1", "--takt", "60", "--out", str(out)]
    )
    text = out.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("# Szenario-Vergleich mit Victron allein")
    assert "| Tag | Victron allein (€) | Optimierer (€) |" in text and "Bestanden" in text
    assert "Weniger als 30 Tage: Für die Abnahme der Phase 3 zählen mindestens 30." in text
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("2027-01-11: Victron allein ")


def test_scenario_days_rejects_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "tage.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["--tage", str(bad), "--out", str(tmp_path / "b.md")]) == 2


def test_scenario_days_rejects_missing_file_and_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--tage", str(tmp_path / "fehlt.json"), "--out", str(tmp_path / "b.md")]) == 2
    assert "nicht gelesen" in capsys.readouterr().err


def test_scenario_days_checks_the_output_directory_before_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs: list[object] = []
    monkeypatch.setattr(scenario_days, "run_scenario", lambda *a, **k: runs.append(a))
    code = main(["--synthetisch", "winter-spread", "--out", str(tmp_path / "fehlt" / "b.md")])
    assert code == 2 and "nicht geschrieben" in capsys.readouterr().err
    monkeypatch.setattr(scenario_days.os, "access", lambda path, mode: False)
    assert main(["--synthetisch", "winter-spread", "--out", str(tmp_path / "b.md")]) == 2
    assert "nicht beschreibbar" in capsys.readouterr().err
    assert runs == []


def test_scenario_days_rejects_bad_arguments(tmp_path: Path) -> None:
    out = str(tmp_path / "b.md")
    assert main(["--synthetisch", "unbekannt", "--out", out]) == 2
    assert main(["--synthetisch", "winter-spread", "--anzahl", "0", "--out", out]) == 2
    assert main(["--synthetisch", "winter-spread", "--takt", "30", "--out", out]) == 2
    assert main(["--out", out]) == 2
    assert main(["--synthetisch", "winter-spread", "--tage", out, "--out", out]) == 2


def test_scenario_days_reads_file_and_passes_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    source = tmp_path / "tage.json"
    source.write_text(data.model_dump_json(), encoding="utf-8")
    seen: list[tuple[str, int, bool | None]] = []

    def fake(file: object, *, step_slots: int, grid_charge: bool | None) -> ScenarioResult:
        seen.append((file.name, step_slots, grid_charge))
        return result_of()

    monkeypatch.setattr(scenario_days, "run_scenario", fake)
    out = str(tmp_path / "b.md")
    for option, expected in (("an", True), ("aus", False), ("einstellung", None)):
        assert main(["--tage", str(source), "--out", out, "--netzladen", option]) == 0
        assert seen[-1] == (data.name, 1, expected)
    assert main(["--tage", str(source), "--out", out, "--takt", "60"]) == 0
    assert seen[-1][1] == 4


def test_scenario_days_exit_code_is_one_when_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scenario_days, "run_scenario", lambda *a, **k: result_of(r1_violations=2))
    out = tmp_path / "b.md"
    assert main(["--synthetisch", "winter-spread", "--out", str(out)]) == 1
    assert "Nicht bestanden" in out.read_text(encoding="utf-8")


def test_scenario_days_default_is_every_quarter_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps: list[int] = []

    def fake(file: object, *, step_slots: int, grid_charge: bool | None) -> ScenarioResult:
        steps.append(step_slots)
        return result_of(step_slots=step_slots)

    monkeypatch.setattr(scenario_days, "run_scenario", fake)
    main(["--synthetisch", "sommer-sonnig", "--out", str(tmp_path / "b.md")])
    assert steps == [1]


# --- Bericht ---------------------------------------------------------------------------------


def _without_cars(data: ScenarioFile) -> ScenarioFile:
    """Die Datei wie in Version 1, ohne Autos (so liefert sie ``export_days``)."""
    return data.model_copy(update={"version": 1, "cars": []})


def test_report_table_and_rows() -> None:
    data = _without_cars(synthetic("winter-spread", date(2027, 1, 11), 1))
    text = render_report(result_of(), data)
    assert text.startswith("# Szenario-Vergleich mit Victron allein\n")
    assert (
        "| Tag | Victron allein (€) | Optimierer (€) | Differenz (€) | Netzladen (kWh) "
        "| Halten (Slots) | Regel R | Ersatzpläne |"
    ) in text
    assert "| 2027-01-11 | 2,50 | 2,10 | -0,40 | 4,3 | 3 | 0 | 1 |" in text
    assert "| Gesamt | 2,50 | 2,10 | -0,40 | 4,3 | 3 | 0 | 1 |" in text
    assert "| Optimierer bereinigt um den End-SOC |" in text and "2,05" in text
    assert "| Leistungspreis | 0,00 | 0,00 | 0,00 |" in text
    assert "| Netzladen vor tatsächlicher Einspeisung | – | – | – | 1,5 |" in text
    assert "Takt: 60 min" in text
    assert text.rstrip().endswith("Ergebnis: Bestanden")


def test_report_takt_follows_step() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    assert "Takt: 15 min" in render_report(result_of(step_slots=1), data)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"adjusted_optimizer_ct": 250.0}, True),
        ({"adjusted_optimizer_ct": 250.5}, False),
        ({"r1_violations": 1}, False),
        ({"targets": 1, "targets_missed": 1}, False),
        ({"r2_violations": 1}, False),
    ],
)
def test_passed_rule(changes: dict[str, object], expected: bool) -> None:
    assert passed(result_of(**changes)) is expected


def test_report_not_passed_names_reasons() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    text = render_report(result_of(adjusted_optimizer_ct=300.0, r1_violations=2), data)
    assert "Nicht bestanden" in text and text.count("Bestanden") == 0
    assert "Bereinigte Kosten über Victron allein" in text and "Regel R verletzt (2)" in text


def test_report_with_cars_shows_targets_and_rule_r2() -> None:
    # Mit Autos (Version 2) kommen die Spalten „Ziele erreicht“ und „Regel R2“ dazu.
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    day = result_of().days[0]
    days = [day, replace(day, day=date(2027, 1, 12), targets=1, targets_reached=1)]
    text = render_report(result_of(days=days, targets=1), data)
    assert (
        "| Tag | Victron allein (€) | Optimierer (€) | Differenz (€) | Netzladen (kWh) "
        "| Halten (Slots) | Regel R | Ersatzpläne | Ziele erreicht | Regel R2 |"
    ) in text
    assert "| 2027-01-11 | 2,50 | 2,10 | -0,40 | 4,3 | 3 | 0 | 1 | – | 0 |" in text
    assert "| 2027-01-12 | 2,50 | 2,10 | -0,40 | 4,3 | 3 | 0 | 1 | 1 von 1 | 0 |" in text
    assert "| Gesamt | 2,50 | 2,10 | -0,40 | 8,5 | 6 | 0 | 1 | 1 von 1 | 0 |" in text
    assert "| Leistungspreis | 0,00 | 0,00 | 0,00 | – | – | – | – | – | – |" in text
    assert text.rstrip().endswith("Ergebnis: Bestanden")


def test_report_with_car_battery_shows_battery_to_car() -> None:
    # Mit dem Schalter „Akku ins Auto, wenn übrig“ kommt die Spalte „Akku → Auto (kWh)“ dazu.
    data = synthetic("winter-spread", date(2027, 1, 11), 2, car_battery=True)
    day = result_of().days[0]
    days = [
        replace(day, car_battery_kwh=1.26),
        replace(day, day=date(2027, 1, 12), targets=1, targets_reached=1, car_battery_kwh=2.0),
    ]
    text = render_report(result_of(days=days, targets=1), data)
    assert (
        "| Tag | Victron allein (€) | Optimierer (€) | Differenz (€) | Netzladen (kWh) "
        "| Halten (Slots) | Regel R | Ersatzpläne | Ziele erreicht | Regel R2 "
        "| Akku → Auto (kWh) |"
    ) in text
    assert "| 2027-01-11 | 2,50 | 2,10 | -0,40 | 4,3 | 3 | 0 | 1 | – | 0 | 1,3 |" in text
    assert "| Gesamt | 2,50 | 2,10 | -0,40 | 8,5 | 6 | 0 | 1 | 1 von 1 | 0 | 3,3 |" in text
    assert "| Leistungspreis | 0,00 | 0,00 | 0,00 | – | – | – | – | – | – | – |" in text
    assert "Verschleiß" in text


def test_report_not_passed_names_missed_targets_and_rule_r2() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 2)
    text = render_report(result_of(targets=2, targets_missed=1, r2_violations=3), data)
    assert "Nicht bestanden" in text
    assert "- Ziel-SOC verfehlt (1 von 2)" in text and "- Regel R2 verletzt (3)" in text


def test_report_thirty_days_have_no_hint() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1)
    day = result_of().days[0]
    text = render_report(result_of(days=[day] * 30), data)
    assert "Weniger als 30 Tage" not in text
    assert "Weniger als 30 Tage" in render_report(result_of(days=[day] * 29), data)


def test_main_leaves_the_logging_configuration_alone(tmp_path: Path) -> None:
    # Nur der Aufruf als Programm stellt die Logs ein; ``main`` aus einem Test darf structlog
    # und die Handler der Wurzel nicht umbiegen (sonst schreiben spätere Tests ins Leere).
    handlers = list(logging.getLogger().handlers)
    configured = structlog.is_configured()
    assert main(["--synthetisch", "winter-spread", "--anzahl", "0", "--out", str(tmp_path)]) == 2
    assert logging.getLogger().handlers == handlers
    assert structlog.is_configured() is configured


def test_report_lists_skipped_days_below_the_table() -> None:
    data = synthetic("winter-spread", date(2027, 1, 11), 1).model_copy(
        update={"skipped_days": ["2027-01-12: keine Preise", "2027-01-14: 6 Lücken"]}
    )
    lines = render_report(result_of(), data).splitlines()
    table_end = max(i for i, line in enumerate(lines) if line.startswith("|"))
    assert lines[table_end + 1 : table_end + 3] == [
        "",
        "Übersprungene Tage: 2027-01-12, 2027-01-14",
    ]
    plain = synthetic("winter-spread", date(2027, 1, 11), 1)
    assert "Übersprungene Tage" not in render_report(result_of(), plain)


def test_scenario_exception_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_args: object, **_kwargs: object) -> object:
        raise ZeroDivisionError("Rechenfehler")

    monkeypatch.setattr(scenario_days, "run_scenario", broken)
    out = tmp_path / "bericht.md"
    code = main(["--synthetisch", "winter-spread", "--anzahl", "1", "--out", str(out)])
    assert code == 2
    assert not out.exists()
    error = capsys.readouterr().err
    assert "Rechnung gescheitert" in error and "ZeroDivisionError: Rechenfehler" in error
