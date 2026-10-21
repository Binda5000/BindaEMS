"""Die Logs der Werkzeuge gehen nach stderr: Auf der Standardausgabe stehen nur ihre Zeilen."""

from __future__ import annotations

import inspect
import logging
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest
import structlog

from bindaems.shared.logging import configure_logging
from bindaems.tools import export_days, scenario_days
from bindaems.tools.sim.__main__ import main as sim_main


def _root_stream() -> object:
    return getattr(logging.getLogger().handlers[0], "stream", None)


def test_tool_logs_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", stream=sys.stderr)
    structlog.get_logger("werkzeug").warning("Ein Hinweis des Werkzeugs")
    logging.getLogger("fremd").warning("Ein Hinweis der Bibliothek")
    shown = capsys.readouterr()
    assert shown.out == ""
    assert "Ein Hinweis des Werkzeugs" in shown.err
    assert "Ein Hinweis der Bibliothek" in shown.err


def test_the_services_still_log_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")  # core und app: wie bisher
    structlog.get_logger("dienst").warning("Ein Hinweis des Dienstes")
    shown = capsys.readouterr()
    assert "Ein Hinweis des Dienstes" in shown.out
    assert shown.err == ""


@pytest.mark.parametrize("module", [export_days, scenario_days])
def test_the_tools_started_as_programs_log_to_stderr(
    module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "x.md"
    argv = {
        "export_days": ["--von", "2026-10-15", "--bis", "2026-10-14", "--out", str(out)],
        "scenario_days": ["--synthetisch", "winter-spread", "--anzahl", "0", "--out", str(out)],
    }[module.__name__.rsplit(".", 1)[1]]
    monkeypatch.setattr(sys, "argv", ["werkzeug", *argv])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(inspect.getfile(module), run_name="__main__")
    assert stopped.value.code == 2
    assert _root_stream() is sys.stderr


def test_the_simulator_logs_to_stderr(tmp_path: Path) -> None:
    out = tmp_path / "bericht.md"
    assert sim_main(["--szenario", "reserve", "--stunden", "0.05", "--out", str(out)]) == 0
    assert _root_stream() is sys.stderr
