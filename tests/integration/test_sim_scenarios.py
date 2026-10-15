"""Szenarien des Simulators in der CI (Design 13.2): die echte Laufzeit gegen das Anlagenmodell.

Jedes Szenario besteht, wenn alle Kennzahlen in ihren Grenzen liegen und seine eigenen Prüfungen
nichts finden; ``failures`` nennt sonst jede Verletzung als Satz. Dazu kommt eine Abwandlung von
„spitze-15min“ für den Boost-Anteil des 15-min-Wächters (Plan 2c-2a, Präzisierung 9).
"""

from dataclasses import replace
from datetime import timedelta
from functools import partial

import pytest

from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.tools.sim.runner import run_scenario
from bindaems.tools.sim.scenarios import (
    EGOLF,
    PEAK_LEAD_S,
    QUARTER_S,
    SCENARIOS,
    TESLA,
    Checks,
    _setup,
    _weather,
)

NAMES = [
    "sonnig",
    "wolkig",
    "abend-sofort",
    "reserve",
    "nur-laden",
    "tesla-5a",
    "tesla-schlaeft",
    "ausfaelle",
    "zuordnung",
    "dry-run",
]
# die Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2), mit Victron live
NEW = [
    "akku-wolkig",
    "akku-minpv",
    "akku-sofort",
    "boost-abend",
    "override-stunde",
    "dess-an",
    "spitze-15min",
    "schieflast-pv",
]


def test_all_scenarios_are_listed() -> None:
    assert list(SCENARIOS) == [*NAMES, *NEW]


def test_battery_and_peak_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[-8:] == NEW
    assert all(SCENARIOS[name].victron_live for name in NEW)
    assert not any(SCENARIOS[name].victron_live for name in list(SCENARIOS)[:10])


@pytest.mark.parametrize("name", [*NAMES, *NEW])
async def test_scenario(name, cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS[name], cfg, tmp_path)
    assert result.failures == []


async def test_boost_does_not_hide_the_grid_of_another_car(cfg, tmp_path) -> None:
    # Plan 2c-2a, Präzisierung 9: wie „spitze-15min“ (ohne PV, Akku 60 %, Victron live), aber mit
    # dem Ziel 5 kW; der Tesla lädt am Wall Connector von Anfang an in Boost, der e-Golf an der EVCS
    # in Sofort. Der Akku-Sollwert lässt Boost aus dem Akku laden und den e-Golf aus dem Netz. Der
    # Wächter zieht nur den Netzanteil des Boost ab: Er sieht den Netzbezug des e-Golf, drosselt ihn
    # und hält die Viertelstunde ab 18:00 beim Ziel (5 kW · 0,25 h, 1 % Toleranz wie in
    # „spitze-15min“). Verbuchte er den Netzbezug des e-Golf als Boost, läge sie still darüber.
    base = SCENARIOS["spitze-15min"]
    duration_s = PEAK_LEAD_S + QUARTER_S
    boost = VehicleIntent(boost_until=base.start + timedelta(seconds=duration_s))
    vehicles = {TESLA: boost, EGOLF: VehicleIntent(mode=ChargeMode.NOW)}
    scenario = replace(
        base,
        setup=partial(_setup, soc_pct=60.0),
        intent=Intent(version=1, control=True, vehicles=vehicles),
        events=_weather(base.start, duration_s, base.seed),
        checks=Checks(()),
        control={"peak_target_kw": 5.0},
    )
    result = await run_scenario(scenario, cfg, tmp_path, duration_s=duration_s)
    quarter = [s for s in result.samples if s.t >= PEAK_LEAD_S]
    assert len(quarter) == QUARTER_S
    assert sum(max(0.0, s.grid_w) for s in quarter) / 3_600_000 <= 5.0 * 0.25 * 1.01
    throttled = [d for d in result.decisions if "peak_guard" in d.codes]
    assert {d.actuator for d in throttled} >= {"wallbox.evcs"}  # der e-Golf, nie der Tesla
    assert "vehicle.tesla" not in {d.actuator for d in throttled}
