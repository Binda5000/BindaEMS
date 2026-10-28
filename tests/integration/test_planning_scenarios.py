"""Szenarien der Planung in der CI (Plan 3b, Präzisierungen 18 und 19): erzeugte Tage, neu geplant
jede Stunde, gegen „Victron allein“ auf denselben Ist-Werten.

Jede Tagesart hat ein geplantes Auto (Plan 4b, Task 13: am ersten Abend um 18:00 mit 40 %
angesteckt, Abfahrt um 07:00 mit Ziel 80 %); „Victron allein“ lädt es ab dem Anstecken voll.
Bestanden heißt zusätzlich: das Ziel erreicht und kein Plan über der Netzenergie nach Regel R2.
Dieselben Tage ohne Auto (wie eine Datei der Version 1) prüfen dasselbe wie vor Plan 4b.

Akku ins Auto (Plan Akku ins Auto, Task 6): dieselben Tage mit „Akku ins Auto, wenn übrig“ gegen
denselben Lauf ohne den Schalter, die Kosten des Optimierers mit dem Verschleiß für Akku → Auto
(Entscheidung 10). Gemessen am 28.10.2026: `sommer-sonnig` 16,0 kWh aus dem Akku ins Auto, bereinigt
−2,65 € statt −0,24 € (2,41 € günstiger, Ziel erreicht, Akku nie unter der Reserve);
`winter-spread` nichts aus dem Akku (der Abend braucht ihn), 9,33 € wie ohne Schalter.

Budget der Datei: 150 s (gemessen 129 s, davon 14 s für Akku ins Auto). Rund 90 s davon braucht
`winter-leistung` mit Auto: Mit Leistungspreis und der Mindestleistung des Autos erreicht der Solver
in vier Plänen nach dem Anstecken die Lücke nicht und endet am Zeitlimit von 20 s, mit einer
gültigen Lösung (Status ``time_limit``).
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


def test_car_battery_saves_on_a_sunny_summer() -> None:
    off = run_scenario(synthetic("sommer-sonnig", date(2027, 7, 5), 2), step_slots=4)
    on = run_scenario(
        synthetic("sommer-sonnig", date(2027, 7, 5), 2, car_battery=True), step_slots=4
    )
    assert sum(d.car_battery_kwh for d in on.days) > 1.0
    assert on.adjusted_optimizer_ct < off.adjusted_optimizer_ct - 10.0
    assert on.targets_missed == 0 and on.r1_violations == 0 and on.r2_violations == 0
    assert on.min_soc_kwh >= on.reserve_kwh - 0.01


def test_car_battery_does_not_cost_in_winter() -> None:
    off = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=4)
    on = run_scenario(
        synthetic("winter-spread", date(2027, 1, 11), 2, car_battery=True), step_slots=4
    )
    assert on.adjusted_optimizer_ct <= off.adjusted_optimizer_ct + 1.0
    assert on.r1_violations == 0 and on.r2_violations == 0
