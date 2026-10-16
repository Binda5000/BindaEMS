"""Szenario-Vergleich mit „Victron allein“ als Werkzeug (Plan 3b, Task 16).

Aufruf::

    python -m bindaems.tools.scenario_days --tage /data/tage.json --out /data/bericht.md
    python -m bindaems.tools.scenario_days --synthetisch winter-spread --anzahl 3 --takt 60 \
        --out bericht.md

Die Tage kommen aus einer Datei von ``export_days`` (``--tage``) oder aus erzeugten Tagen
(``--synthetisch``, Arten siehe ``scenario.synthetic``). ``--takt 15`` (Standard) plant jede
Viertelstunde neu wie der Dienst, ``--takt 60`` stündlich (viermal schneller, wie die CI).
``--netzladen`` ersetzt den Schalter „Netzladen erlaubt“ der Datei; ``einstellung`` (Standard)
lässt ihn.

Auf der Standardausgabe steht je Tag eine Zeile, der Bericht geht als Markdown nach ``--out``
(das Verzeichnis muss existieren). Exitcode 0 bei „Bestanden“ (bereinigte Kosten höchstens
„Victron allein“, keine Verletzung von Regel R), 1 bei „Nicht bestanden“, 2 bei ungültiger Eingabe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Final

from bindaems.shared.logging import configure_logging
from bindaems.tools.scenario.data import ScenarioFile
from bindaems.tools.scenario.report import SLOT_MINUTES, passed, render_report
from bindaems.tools.scenario.runner import ScenarioResult, run_scenario
from bindaems.tools.scenario.synthetic import DAY_TYPES, synthetic

# Erster Tag der erzeugten Tage: Winter nach dem Beginn des Beispiel-Leistungspreises (1. 1. 2027)
START_WINTER: Final = date(2027, 1, 11)
START_SUMMER: Final = date(2027, 7, 5)
STEP_MINUTES: Final = (SLOT_MINUTES, 60)
GRID_CHARGE: Final = {"an": True, "aus": False, "einstellung": None}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scenario_days", description="Szenario-Vergleich mit Victron allein"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tage", type=Path, help="Datei von export_days")
    source.add_argument(
        "--synthetisch", choices=DAY_TYPES, metavar="ART", help=", ".join(DAY_TYPES)
    )
    parser.add_argument("--anzahl", type=int, default=1, help="Zahl erzeugter Tage (Standard 1)")
    parser.add_argument("--out", type=Path, required=True, help="Bericht (Markdown)")
    parser.add_argument("--takt", type=int, choices=STEP_MINUTES, default=SLOT_MINUTES)
    parser.add_argument("--netzladen", choices=tuple(GRID_CHARGE), default="einstellung")
    return parser


def _load(args: argparse.Namespace) -> ScenarioFile:
    if args.tage is not None:
        try:
            text = args.tage.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Datei nicht gelesen: {exc}") from exc
        return ScenarioFile.model_validate_json(text)
    first = START_SUMMER if args.synthetisch == "sommer-sonnig" else START_WINTER
    return synthetic(args.synthetisch, first, args.anzahl)


def _day_lines(result: ScenarioResult) -> list[str]:
    return [
        f"{day.day.isoformat()}: Victron allein {day.baseline_ct / 100:.2f} €, "
        f"Optimierer {day.optimizer_ct / 100:.2f} €, "
        f"Differenz {(day.optimizer_ct - day.baseline_ct) / 100:.2f} €".replace(".", ",")
        for day in result.days
    ]


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse beendet sich bei Fehlern mit 2 (und bei --help mit 0)
        return exc.code if isinstance(exc.code, int) else 2
    configure_logging("WARNING")
    if args.anzahl < 1:
        print("--anzahl muss mindestens 1 sein", file=sys.stderr)
        return 2
    try:
        data = _load(args)
    except ValueError as exc:  # auch die Prüfung der Datei (pydantic) ist ein ValueError
        print(f"Eingabe ungültig: {exc}", file=sys.stderr)
        return 2
    result = run_scenario(
        data, step_slots=args.takt // SLOT_MINUTES, grid_charge=GRID_CHARGE[args.netzladen]
    )
    try:
        args.out.write_text(render_report(result, data), encoding="utf-8")
    except OSError as exc:
        print(f"Bericht nicht geschrieben: {exc}", file=sys.stderr)
        return 2
    for line in _day_lines(result):
        print(line)
    return 0 if passed(result) else 1


if __name__ == "__main__":
    sys.exit(main())
