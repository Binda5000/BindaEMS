"""Bericht eines Simulatorlaufs in Markdown (Design 13.4).

``render_report`` fasst ein ``SimResult`` zusammen:

- Überschrift mit dem Titel des Szenarios, dann eine Tabelle „Angabe | Wert“: Szenario, simulierte
  Zeit in Wiener Zeit und die Konfiguration, aus der die Grenzen kommen
- ``## Kennzahlen``: je Kennzahl Wert, Grenze und OK oder FEHLER (``Metrics.figures``)
- ``## Szenario-Prüfungen``: je Prüfung eine Zeile mit OK oder FEHLER, ihrem Satz und, wenn sie
  fehlschlägt, ihren Verstößen als Befund (``check_lines``)
- ``## Entscheidungen``: die Zahl der Entscheidungen je Aktor, Art und Ergebnis, danach die Zahl
  je Code; die Codes sind die Gründe der Regelung und die Befunde der Safety-Schicht

Die Grenzen liest der Bericht aus der Konfiguration ``config_path``, mit der das Szenario lief.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from bindaems.shared.config import load_config
from bindaems.shared.decisions import Decision
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.runner import SimResult
from bindaems.tools.sim.scenarios import Check, Checks

OK: Final = "OK"
FAILED: Final = "FEHLER"
SCENARIO_CHECKS: Final = "Prüfungen des Szenarios."  # Satz zu Prüfungen, die keine ``Checks`` sind
NO_FINDING: Final = "–"  # Befund einer bestandenen Prüfung
NO_DECISIONS: Final = "Keine Entscheidungen."


@dataclass(frozen=True)
class CheckLine:
    """Eine Prüfung des Szenarios im Ergebnis: ihr Satz und je Verstoß einer; ohne bestanden."""

    text: str
    findings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def rating(self) -> str:
        return OK if self.ok else FAILED

    @property
    def finding(self) -> str:
        """Die Verstöße in einem Text, ``NO_FINDING`` ohne."""
        return " ".join(self.findings) if self.findings else NO_FINDING


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
        "| Ergebnis | Prüfung | Befund |",
        "|---|---|---|",
        *(
            f"| {line.rating} | {_cell(line.text)} | {_cell(line.finding)} |"
            for line in check_lines(result)
        ),
        "",
        "## Entscheidungen",
        "",
        *_decisions(result.decisions),
    ]
    return "\n".join(lines) + "\n"


def check_lines(result: SimResult) -> list[CheckLine]:
    """Je Prüfung des Szenarios eine ``CheckLine`` mit ihrem Satz und ihren Verstößen.

    Sind die Prüfungen keine ``Checks``, gibt es keine Sätze dazu: dann sind sie eine Prüfung mit
    dem Satz ``SCENARIO_CHECKS``. Die Prüfungen sind reine Funktionen des Ergebnisses; der Bericht
    ruft sie erneut auf.
    """
    checks = result.scenario.checks
    items = checks.items if isinstance(checks, Checks) else (Check(SCENARIO_CHECKS, checks),)
    return [CheckLine(check.text, tuple(check.find(result))) for check in items]


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
