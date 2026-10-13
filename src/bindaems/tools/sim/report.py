"""Bericht eines Simulatorlaufs in Markdown (Design 13.4).

``render_report`` fasst ein ``SimResult`` zusammen:

- Überschrift mit dem Titel des Szenarios, dann eine Tabelle „Angabe | Wert“: Szenario, simulierte
  Zeit in Wiener Zeit und die Konfiguration, aus der die Grenzen kommen
- ``## Kennzahlen``: je Kennzahl Wert, Grenze und OK oder FEHLER (``Metrics.figures``)
- ``## Szenario-Prüfungen``: je Prüfung eine Zeile, OK mit ihrem Satz oder je Verstoß FEHLER mit
  dessen Satz (``check_lines``)
- ``## Entscheidungen``: die Zahl der Entscheidungen je Aktor, Art und Ergebnis, danach die Zahl
  je Code; die Codes sind die Gründe der Regelung und die Befunde der Safety-Schicht

Die Grenzen liest der Bericht aus der Konfiguration ``config_path``, mit der das Szenario lief.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from bindaems.shared.config import load_config
from bindaems.shared.decisions import Decision
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import Checks

OK: Final = "OK"
FAILED: Final = "FEHLER"
NO_FINDINGS: Final = "Keine Verstöße."  # eine Prüfung ohne eigenen Satz, die bestanden ist
NO_DECISIONS: Final = "Keine Entscheidungen."


def render_report(result: SimResult, config_path: str) -> str:
    """Der Bericht zum Lauf ``result`` als Markdown; ``config_path`` ist seine Konfiguration."""
    cfg = load_config(Path(config_path))
    scenario = result.scenario
    lines = [
        f"# Simulation „{scenario.title}“",
        "",
        "| Angabe | Wert |",
        "|---|---|",
        f"| Szenario | {_cell(scenario.name)} |",
        f"| Simulierte Zeit (Wiener Zeit) | {_span(scenario.start, len(result.samples))} |",
        f"| Konfiguration | {_cell(config_path)} |",
        "",
        "## Kennzahlen",
        "",
        "| Kennzahl | Wert | Grenze | Ergebnis |",
        "|---|---|---|---|",
        *(
            f"| {f.name} | {f.value} | {f.limit} | {OK if f.ok else FAILED} |"
            for f in result.metrics.figures(cfg)
        ),
        "",
        "## Szenario-Prüfungen",
        "",
        "| Ergebnis | Prüfung |",
        "|---|---|",
        *(f"| {OK if ok else FAILED} | {_cell(text)} |" for ok, text in check_lines(result)),
        "",
        "## Entscheidungen",
        "",
        *_decisions(result.decisions),
    ]
    return "\n".join(lines) + "\n"


def check_lines(result: SimResult) -> list[tuple[bool, str]]:
    """Je Prüfung des Szenarios ``(True, ihr Satz)`` oder je Verstoß ``(False, sein Satz)``.

    Sind die Prüfungen keine ``Checks``, gibt es keine Sätze dazu: dann je Verstoß eine Zeile oder
    ``NO_FINDINGS``. Die Prüfungen sind reine Funktionen des Ergebnisses; der Bericht ruft sie
    erneut auf.
    """
    checks = result.scenario.checks
    if not isinstance(checks, Checks):
        found = checks(result)
        return [(False, text) for text in found] or [(True, NO_FINDINGS)]
    lines: list[tuple[bool, str]] = []
    for check in checks.items:
        found = check.find(result)
        lines += [(False, text) for text in found] or [(True, check.text)]
    return lines


def _span(start: datetime, seconds: int) -> str:
    """Beginn und Ende des Laufs in Wiener Zeit; das Datum des Endes nur an einem anderen Tag."""
    begin = start.astimezone(LOCAL_TZ)
    end = (start + timedelta(seconds=seconds)).astimezone(LOCAL_TZ)
    shown_end = f"{end:%H:%M}" if end.date() == begin.date() else f"{end:%d.%m.%Y %H:%M}"
    return f"{begin:%d.%m.%Y %H:%M} bis {shown_end}"


def _decisions(decisions: Sequence[Decision]) -> list[str]:
    """Die Tabellen der Entscheidungen: je Aktor, Art und Ergebnis, dann je Code."""
    if not decisions:
        return [NO_DECISIONS]
    groups = Counter((d.actuator, d.kind.value, d.result.value) for d in decisions)
    codes = Counter(code for d in decisions for code in d.codes)
    return [
        "| Aktor | Art | Ergebnis | Anzahl |",
        "|---|---|---|---|",
        *(
            f"| {actuator} | {kind} | {outcome} | {count} |"
            for (actuator, kind, outcome), count in sorted(groups.items())
        ),
        "",
        "| Grund | Anzahl |",
        "|---|---|",
        *(
            f"| {code} | {count} |"
            for code, count in sorted(codes.items(), key=lambda item: (-item[1], item[0]))
        ),
    ]


def _cell(text: str) -> str:
    """Ein Text in einer Tabellenzelle: ``|`` maskiert, Zeilenumbrüche als ``<br>``."""
    return text.replace("|", "\\|").replace("\n", "<br>")
