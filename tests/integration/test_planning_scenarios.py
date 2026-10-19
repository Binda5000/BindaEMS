"""Szenarien der Planung in der CI (Plan 3b, Präzisierungen 18 und 19): erzeugte Tage, neu geplant
jede Stunde, gegen „Victron allein“ auf denselben Ist-Werten.

Jede Tagesart hat ein geplantes Auto (Plan 4b, Task 13: am ersten Abend um 18:00 mit 40 %
angesteckt, Abfahrt um 07:00 mit Ziel 80 %); „Victron allein“ lädt es ab dem Anstecken voll.
Bestanden heißt zusätzlich: das Ziel erreicht und kein Plan über der Netzenergie nach Regel R2.
Dieselben Tage ohne Auto (wie eine Datei der Version 1) prüfen dasselbe wie vor Plan 4b.

Budget der Datei: 150 s. Rund 90 s davon braucht `winter-leistung` mit Auto: Mit Leistungspreis und
der Mindestleistung des Autos erreicht der Solver in vier Plänen nach dem Anstecken die Lücke nicht
und endet am Zeitlimit von 20 s, mit einer gültigen Lösung (Status ``time_limit``).
"""

from __future__ import annotations

from datetime import date

import pytest

from bindaems.tools.scenario.runner import run_scenario
from bindaems.tools.scenario.synthetic import synthetic

CASES = [
    ("sommer-sonnig", date(2027, 7, 5)),
    ("winter-spread", date(2027, 1, 11)),
    ("winter-leistung", date(2027, 1, 11)),
    ("wechselhaft", date(2027, 1, 11)),
]


@pytest.mark.parametrize("car", [True, False], ids=["mit-auto", "ohne-auto"])
@pytest.mark.parametrize("kind,first", CASES)
def test_optimizer_beats_victron_alone(kind: str, first: date, car: bool) -> None:
    data = synthetic(kind, first, 2)
    if not car:
        data = data.model_copy(update={"version": 1, "cars": []})
    result = run_scenario(data, step_slots=4)
    assert result.targets == (1 if car else 0)
    assert result.targets_missed == 0
    assert result.r2_violations == 0
    assert result.r1_violations == 0
    assert result.min_soc_kwh >= result.reserve_kwh - 0.01
    assert result.adjusted_optimizer_ct <= result.baseline_ct + 1.0  # 1 ct Toleranz für Rundung
    if kind.startswith("winter"):
        assert result.adjusted_optimizer_ct < result.baseline_ct - 10.0
        assert sum(d.grid_charge_kwh for d in result.days) > 0
