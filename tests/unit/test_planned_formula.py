"""Planer und core rechnen Bedarf und späteste Startzeit gleich (Plan 4b, Präzisierung 6).

Die app importiert nichts aus dem core, darum steht die Formel zweimal: in
``core.control.planned`` und in ``app.planning.vehicles``. Dieser Test importiert beide.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bindaems.app.planning import vehicles as app
from bindaems.core.control import planned as core
from bindaems.shared.config import Config

DEPARTURE = datetime.fromisoformat("2026-10-17T05:00:00+00:00")


def test_constants_are_the_same() -> None:
    assert app.LATEST_START_MARGIN == core.LATEST_START_MARGIN
    assert app.LATEST_START_BUFFER == core.LATEST_START_BUFFER


@settings(max_examples=200, deadline=None)
@given(
    soc=st.floats(0, 100),
    target=st.floats(20, 100),
    usable=st.floats(5, 120),
    efficiency=st.floats(0.5, 1.0),
    full_w=st.floats(1000, 22000),
    minutes=st.integers(-600, 3000),
)
def test_planner_and_core_use_the_same_formula(
    soc: float, target: float, usable: float, efficiency: float, full_w: float, minutes: int
) -> None:
    need = core.need_kwh(soc, target, usable, efficiency)
    assert app.need_kwh(soc, target, usable, efficiency) == need
    departure = DEPARTURE + timedelta(minutes=minutes)
    assert app.latest_start(departure, need, full_w) == core.latest_start(departure, need, full_w)


@pytest.mark.parametrize(
    ("soc", "target", "start"),
    [
        (40.0, 80.0, "2026-10-17T01:25:43.478261+00:00"),  # 33,3 kWh bei 11040 W
        (80.0, 80.0, None),  # am Ziel: kein Bedarf, keine Startzeit
        (90.0, 80.0, None),
    ],
)
def test_same_values_for_an_example(soc: float, target: float, start: str | None) -> None:
    need = app.need_kwh(soc, target, 75.0, 0.9)
    assert need == core.need_kwh(soc, target, 75.0, 0.9)
    expected = None if start is None else datetime.fromisoformat(start)
    assert app.latest_start(DEPARTURE, need, 11040.0) == expected
    assert core.latest_start(DEPARTURE, need, 11040.0) == expected


def test_full_power_is_the_same(cfg: Config) -> None:
    volts = cfg.grid.voltage_nominal_v
    for vehicle in cfg.vehicles.values():
        for wallbox in cfg.wallboxes.values():
            assert app.full_power_w(vehicle, wallbox, volts) == core.full_power_w(
                vehicle, wallbox, volts
            )
