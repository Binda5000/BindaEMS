"""Szenarien des Simulators in der CI (Design 13.2): die echte Laufzeit gegen das Anlagenmodell.

Jedes Szenario besteht, wenn alle Kennzahlen in ihren Grenzen liegen und seine eigenen Prüfungen
nichts finden; ``failures`` nennt sonst jede Verletzung als Satz.
"""

import pytest

from bindaems.tools.sim.runner import run_scenario
from bindaems.tools.sim.scenarios import SCENARIOS


@pytest.mark.parametrize("name", ["sonnig", "wolkig", "abend-sofort", "reserve", "nur-laden"])
async def test_scenario(name, cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS[name], cfg, tmp_path)
    assert result.failures == []
