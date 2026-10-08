from types import MappingProxyType

import pytest
from tests.helpers import T0, snap
from tests.unit.core.state.conftest import BASE

from bindaems.core.state.derived import derive
from bindaems.shared.domain import Quality, Snapshot


def test_phase_import_uses_power_sign(cfg) -> None:
    d = derive(snap(BASE | {"grid.l1.power_w": -2300.0, "grid.l1.current_a": 10.0}), cfg)
    assert d.phase_import_a["L1"] == pytest.approx(-10.0)


def test_phase_import_requires_at_least_100_v(cfg) -> None:
    d = derive(snap(BASE | {"grid.l2.voltage_v": 99.0}), cfg)
    assert d.phase_import_a["L2"] is None
    assert d.phase_import_a["L1"] == pytest.approx(1000.0 / 230.0)


def test_grid_power_falls_back_to_phase_sum(cfg) -> None:
    values = {k: v for k, v in BASE.items() if k != "grid.power_w"} | {"grid.l1.power_w": 1500.0}
    assert derive(snap(values), cfg).grid_w == pytest.approx(3500.0)


def test_consumption_and_battery_ac(cfg) -> None:
    d = derive(snap(BASE), cfg)
    assert d.consumption_l["L1"] == pytest.approx(1800.0)
    assert d.consumption_w == pytest.approx(5400.0) and d.battery_ac_w == pytest.approx(600.0)


def test_pv_total_counts_totals_and_dc_but_not_phase_values(cfg) -> None:
    assert derive(snap(BASE), cfg).pv_total_w == pytest.approx(3000.0)  # DC fehlt → 0
    d = derive(snap(BASE | {"pv_dc.power_w": 400.0}), cfg)
    assert d.pv_total_w == pytest.approx(3400.0)
    assert d.battery_w == pytest.approx(1000.0)  # 600 W AC + 400 W DC


def test_stale_pv_inverter_yields_none(cfg) -> None:
    stale = snap({"pv.huawei.power_w": 3000.0}, quality=Quality.STALE)
    s = Snapshot(T0, MappingProxyType({**snap(BASE).readings, **stale.readings}))
    d = derive(s, cfg)
    assert d.pv_total_w is None and d.consumption_w is None


def test_house_load_subtracts_wallboxes(cfg) -> None:
    d = derive(snap(BASE | {"wallbox.evcs.power_w": 2000.0, "wallbox.twc.power_w": 1000.0}), cfg)
    assert d.house_load_w == pytest.approx(2400.0)


def test_evcs_phase_inference_three_and_two_phase(cfg) -> None:
    d3 = derive(snap(BASE | {"wallbox.evcs.power_w": 11040.0, "wallbox.evcs.current_a": 16.0}), cfg)
    assert d3.wallbox_phase_a["evcs"] == {"L1": 16.0, "L2": 16.0, "L3": 16.0}
    d2 = derive(snap(BASE | {"wallbox.evcs.power_w": 7360.0, "wallbox.evcs.current_a": 16.0}), cfg)
    assert d2.wallbox_phase_a["evcs"] == {"L1": 16.0, "L2": 16.0, "L3": 0.0}


def test_evcs_current_falls_back_to_gx(cfg) -> None:
    d = derive(
        snap(BASE | {"wallbox.evcs.power_w": 3680.0, "wallbox.evcs.gx_current_a": 16.0}), cfg
    )
    assert d.wallbox_phase_a["evcs"] == {"L1": 16.0, "L2": 0.0, "L3": 0.0}


def test_twc_phase_currents_taken_directly(cfg) -> None:
    currents = {"wallbox.twc.l1.current_a": 15.9, "wallbox.twc.l2.current_a": 16.0}
    d = derive(snap(BASE | currents | {"wallbox.twc.l3.current_a": 16.1}), cfg)
    assert d.wallbox_phase_a["twc"] == {"L1": 15.9, "L2": 16.0, "L3": 16.1}
    assert "twc" not in derive(snap(BASE | currents), cfg).wallbox_phase_a  # L3 fehlt


def test_balance_residual(cfg) -> None:
    d = derive(snap(BASE | {"battery.power_w": -2000.0}), cfg)
    assert d.balance_residual_w == pytest.approx(2600.0)  # 3000 + 3000 + 2000 − 5400


def test_missing_vebus_yields_none(cfg) -> None:
    d = derive(snap({k: v for k, v in BASE.items() if not k.startswith("vebus")}), cfg)
    assert d.consumption_w is None and d.house_load_w is None and d.battery_ac_w is None
