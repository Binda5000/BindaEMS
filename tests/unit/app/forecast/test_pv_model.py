import pytest
from tests.app_helpers import T_APP

from bindaems.app.forecast.pv_model import cell_temperature_c, combine, plane_power_w
from bindaems.shared.settings import PvModelSettings
from bindaems.shared.timeutil import SLOT


def test_cell_temperature() -> None:
    assert cell_temperature_c(20.0, 800.0, 45.0) == pytest.approx(45.0)


@pytest.mark.parametrize(
    ("gti", "temp", "expected"), [(800.0, 20.0, 6324.0), (1000.0, 25.0, 7570.3125)]
)
def test_plane_power_reference_values(gti: float, temp: float, expected: float) -> None:
    assert plane_power_w(gti, temp, 10.0, PvModelSettings()) == pytest.approx(expected)


def test_no_irradiance_no_power() -> None:
    assert plane_power_w(0.0, -5.0, 10.0, PvModelSettings()) == 0.0
    assert plane_power_w(-3.0, 20.0, 10.0, PvModelSettings()) == 0.0  # Messrauschen


def test_combine_sums_planes_and_clips_at_inverter_limit() -> None:
    t1, t2 = T_APP, T_APP + SLOT
    combined = combine([{t1: 6000.0, t2: 1000.0}, {t1: 6000.0}], inverter_ac_max_w=10000.0)
    assert combined == {t1: 10000.0}
