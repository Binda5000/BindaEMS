from datetime import datetime
from pathlib import Path

import httpx
import pytest

from bindaems.app.prices.checks import to_slots
from bindaems.app.prices.sources import (
    AwattarSource,
    EnergyChartsSource,
    PriceInterval,
    SmartEnergySource,
)


@pytest.fixture
def source(clock) -> SmartEnergySource:
    return SmartEnergySource(httpx.AsyncClient(), clock)


@pytest.fixture
def energy_charts(clock) -> EnergyChartsSource:
    return EnergyChartsSource(httpx.AsyncClient(), clock)


@pytest.fixture
def awattar(clock) -> AwattarSource:
    return AwattarSource(httpx.AsyncClient(), clock)


FIX = Path("tests/fixtures")


@pytest.fixture
def smartenergy_intervals(source) -> list[PriceInterval]:
    return source.parse((FIX / "smartenergy_2026-10-09.json").read_text())


@pytest.fixture
def smartenergy_slots(smartenergy_intervals) -> dict[datetime, float]:
    return to_slots(smartenergy_intervals)


@pytest.fixture
def energy_charts_slots(energy_charts) -> dict[datetime, float]:
    return to_slots(energy_charts.parse((FIX / "energycharts_at_2026-10-09.json").read_text()))


@pytest.fixture
def awattar_slots(awattar) -> dict[datetime, float]:
    return to_slots(awattar.parse((FIX / "awattar_2026-10-09.json").read_text()))
