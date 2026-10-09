import httpx
import pytest

from bindaems.app.prices.sources import AwattarSource, EnergyChartsSource, SmartEnergySource


@pytest.fixture
def source(clock) -> SmartEnergySource:
    return SmartEnergySource(httpx.AsyncClient(), clock)


@pytest.fixture
def energy_charts(clock) -> EnergyChartsSource:
    return EnergyChartsSource(httpx.AsyncClient(), clock)


@pytest.fixture
def awattar(clock) -> AwattarSource:
    return AwattarSource(httpx.AsyncClient(), clock)
