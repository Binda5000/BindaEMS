"""Szenarien der Planung in der CI (Plan 3b, Präzisierungen 18 und 19): erzeugte Tage, neu geplant
jede Stunde, gegen „Victron allein“ auf denselben Ist-Werten.

Budget der Datei: 90 s.
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


@pytest.mark.parametrize("kind,first", CASES)
def test_optimizer_beats_victron_alone(kind: str, first: date) -> None:
    result = run_scenario(synthetic(kind, first, 2), step_slots=4)
    assert result.r1_violations == 0
    assert result.min_soc_kwh >= result.reserve_kwh - 0.01
    assert result.adjusted_optimizer_ct <= result.baseline_ct + 1.0  # 1 ct Toleranz für Rundung
    if kind.startswith("winter"):
        assert result.adjusted_optimizer_ct < result.baseline_ct - 10.0
        assert sum(d.grid_charge_kwh for d in result.days) > 0
