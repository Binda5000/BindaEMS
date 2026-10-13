"""Tests des Aufrufs ``python -m bindaems.tools.sim``: Bericht, Ausgabe und Rückgabewerte.

``main`` lässt ein Szenario mit ``asyncio.run`` laufen; aus einem Test mit laufendem Event-Loop
geht das nur in einem eigenen Thread (``asyncio.to_thread``). Die Läufe sind kurz (``--stunden``).
``main`` stellt die Logs auf WARNING; jeder Test stellt sie danach wieder her.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
import yaml

import bindaems.tools.sim.__main__ as sim_main
from bindaems.tools.sim.__main__ import main
from bindaems.tools.sim.scenarios import SCENARIOS

EXAMPLE = Path("deploy/config.example.yaml")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    structlog.reset_defaults()
    root.handlers[:] = handlers
    root.setLevel(level)


def config_with(tmp_path: Path, change: Any) -> Path:
    """Die Beispielkonfiguration, von ``change`` geändert, als Datei in ``tmp_path``."""
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def without_egolf(raw: dict[str, Any]) -> None:
    raw["vehicles"]["golf"] = raw["vehicles"].pop("egolf")


def without_twc(raw: dict[str, Any]) -> None:
    raw["wallboxes"]["wand"] = raw["wallboxes"].pop("twc")
    raw["vehicles"]["tesla"]["default_wallbox"] = "wand"


def tesla_without_tessie(raw: dict[str, Any]) -> None:
    del raw["vehicles"]["tesla"]["tessie"]


def evcs_is_a_wall_connector(raw: dict[str, Any]) -> None:
    boxes = raw["wallboxes"]
    boxes["box"], boxes["evcs"] = boxes.pop("evcs"), dict(boxes["twc"], host="twc2.lan")
    raw["vehicles"]["egolf"]["default_wallbox"] = "box"
    raw["victron"]["instances"]["evcharger"] = {40: "box"}


async def test_cli_writes_the_report(tmp_path) -> None:
    out = tmp_path / "bericht.md"
    code = await asyncio.to_thread(
        main, ["--szenario", "reserve", "--stunden", "0.1", "--out", str(out)]
    )
    text = out.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("# Simulation „Sofort knapp über der USV-Reserve“")
    assert "## Kennzahlen" in text and "| Höchster Phasenstrom |" in text


def test_unknown_scenario_is_an_error(tmp_path, capsys) -> None:
    assert main(["--szenario", "regen", "--out", str(tmp_path / "x.md")]) == 2
    error = capsys.readouterr().err
    assert error == f"Unbekanntes Szenario „regen“; bekannt sind {', '.join(SCENARIOS)}.\n"
    assert not (tmp_path / "x.md").exists()


def test_the_cli_prints_one_line_per_metric_and_logs_only_warnings(tmp_path, capsys) -> None:
    out = tmp_path / "bericht.md"
    assert main(["--szenario", "reserve", "--stunden", "0.05", "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Höchster Phasenstrom: ")
    assert lines[0].endswith(" A (Grenze 35 A) – OK")
    assert lines[9] == "Wiederholte Dry-Run-Entscheidungen: 0 (Grenze 0) – OK"
    assert lines[10:] == [
        "Szenario-Prüfung: OK – Eine Entscheidung nennt „reserve_guard“.",
        "Szenario-Prüfung: OK – Der SOC sinkt nie unter 20 %.",
        f"Bericht: {out}",
    ]  # kein Log der Laufzeit: Ihre Wechsel des Betriebszustands sind INFO


def test_a_failing_run_returns_1(tmp_path, capsys) -> None:
    out = tmp_path / "bericht.md"
    assert main(["--szenario", "abend-sofort", "--stunden", "0.05", "--out", str(out)]) == 1
    check = "Während der Lastspitze liegt der Strom der EVCS unter dem davor."
    finding = "Die Lastspitze liegt nicht im Lauf."
    assert f"| FEHLER | {check} | {finding} |" in out.read_text(encoding="utf-8")
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2:] == [
        f"Szenario-Prüfung: FEHLER – {check} Befund: {finding}",
        f"Bericht: {out}",
    ]


def test_every_run_gets_a_fresh_directory(tmp_path, monkeypatch) -> None:
    # In einem gebrauchten Verzeichnis nähme der core dieselbe Absicht noch einmal an (eine
    # Wiederholung mit gleichem Inhalt ist kein Fehler), aber der Lauf läse auch die Entscheidungen
    # des früheren Laufs: Der zweite Bericht zählte sie doppelt. Darum bekommt jeder Lauf ein
    # eigenes, leeres Verzeichnis.
    seen: list[tuple[Path, list[str]]] = []
    real = sim_main.run_scenario

    def spy(scenario: Any, cfg: Any, data_dir: Path, **kwargs: Any) -> Any:
        # der Inhalt beim Start des Laufs; zurück geht der Lauf selbst, ``main`` führt ihn aus
        seen.append((data_dir, sorted(entry.name for entry in data_dir.iterdir())))
        return real(scenario, cfg, data_dir, **kwargs)

    monkeypatch.setattr(sim_main, "run_scenario", spy)
    reports = []
    for n in range(2):
        out = tmp_path / f"bericht-{n}.md"
        assert main(["--szenario", "reserve", "--stunden", "0.02", "--out", str(out)]) == 0
        reports.append(out.read_text(encoding="utf-8"))
    (first, first_entries), (second, second_entries) = seen
    assert first != second and first_entries == second_entries == []
    decisions = [report.split("## Entscheidungen")[1] for report in reports]
    assert decisions[0] == decisions[1] and "| wallbox.evcs | charging | ok | 1 |" in decisions[0]


@pytest.mark.parametrize(
    "change", [without_egolf, without_twc, tesla_without_tessie, evcs_is_a_wall_connector]
)
def test_a_config_without_the_devices_of_the_scenarios_is_an_error(
    tmp_path, capsys, change
) -> None:
    config = config_with(tmp_path, change)
    out = tmp_path / "x.md"
    assert main(["--szenario", "reserve", "--config", str(config), "--out", str(out)]) == 2
    assert capsys.readouterr().err == (
        "Die Szenarien brauchen die Wallboxen „evcs“ (EVCS) und „twc“ (Wall Connector) und die "
        "Fahrzeuge „tesla“ (mit Tessie) und „egolf“.\n"
    )
    assert not out.exists()


def test_an_invalid_config_is_an_error(tmp_path, capsys) -> None:
    missing = tmp_path / "fehlt.yaml"
    assert main(["--szenario", "reserve", "--config", str(missing), "--out", "x.md"]) == 2
    assert capsys.readouterr().err.startswith("Konfiguration ungültig: Datei nicht gefunden")


def test_an_unwritable_report_is_an_error(tmp_path, capsys) -> None:
    blocked = tmp_path / "datei"
    blocked.write_text("", encoding="utf-8")
    out = blocked / "bericht.md"  # ein Verzeichnis, das eine Datei ist: Fehler vor dem Lauf
    assert main(["--szenario", "reserve", "--stunden", "0.02", "--out", str(out)]) == 2
    assert capsys.readouterr().err.startswith("Bericht nicht geschrieben: ")


def test_a_report_that_cannot_be_written_after_the_run_is_an_error(tmp_path, capsys) -> None:
    out = tmp_path / "bericht.md"
    out.mkdir()  # der Bericht wäre ein Verzeichnis: Das zeigt sich erst beim Schreiben
    assert main(["--szenario", "reserve", "--stunden", "0.02", "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("Bericht nicht geschrieben: ")
    assert "Bericht: " not in captured.out and captured.out.startswith("Höchster Phasenstrom: ")


@pytest.mark.parametrize("hours", ["0", "-1", "abc", "inf", "nan"])
def test_hours_must_be_a_finite_number_above_0(tmp_path, capsys, hours) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--szenario", "reserve", "--stunden", hours, "--out", str(tmp_path / "x.md")])
    assert exit_info.value.code == 2
    assert "--stunden" in capsys.readouterr().err


def test_hours_beyond_the_scenario_run_the_whole_scenario(tmp_path, monkeypatch, capsys) -> None:
    durations: list[int | None] = []
    real = sim_main.run_scenario

    def spy(scenario: Any, cfg: Any, data_dir: Path, *, duration_s: int | None = None) -> Any:
        durations.append(duration_s)  # verlangt von ``main``; der Lauf selbst ist kurz
        return real(scenario, cfg, data_dir, duration_s=2)

    monkeypatch.setattr(sim_main, "run_scenario", spy)
    for hours in (["--stunden", "5"], ["--stunden", "0.5"], []):
        main(["--szenario", "reserve", *hours, "--out", str(tmp_path / "x.md")])
    assert durations == [SCENARIOS["reserve"].duration_s, 1800, None]  # „reserve“ dauert 1 h
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    hours_help = "Dauer in Stunden statt der des Szenarios, höchstens diese;"
    assert f"--stunden STUNDEN {hours_help} mehr lässt es ganz laufen" in help_text
