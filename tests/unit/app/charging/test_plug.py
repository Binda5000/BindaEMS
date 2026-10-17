from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st
from tests.helpers import snap

from bindaems.app.charging.plug import wallbox_connected
from bindaems.core.control.charge_inputs import _evcs_state, _twc_state
from bindaems.shared.config import EvcsConfig, TwcConfig
from bindaems.shared.domain import Value

EVCS = EvcsConfig(type="victron_evcs_ns", host="evcs.lan")
TWC = TwcConfig(type="tesla_wall_connector_gen3", host="twc.lan")


def values(signals: dict[str, Value]) -> Callable[[str], Value]:
    return lambda name: signals.get(name)


def test_evcs_connected_from_status() -> None:
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": 0})) is False
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": 1})) is True
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": 2})) is True
    assert wallbox_connected(EVCS, "evcs", values({})) is None
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": True})) is None
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": "x"})) is None
    assert wallbox_connected(EVCS, "evcs", values({"wallbox.evcs.status": float("nan")})) is None


def test_twc_connected_from_flag() -> None:
    assert wallbox_connected(TWC, "twc", values({"wallbox.twc.vehicle_connected": True})) is True
    assert wallbox_connected(TWC, "twc", values({"wallbox.twc.vehicle_connected": False})) is False
    assert wallbox_connected(TWC, "twc", values({})) is None
    assert wallbox_connected(TWC, "twc", values({"wallbox.twc.vehicle_connected": 1})) is None


@given(status=st.integers(0, 10) | st.none(), flag=st.booleans() | st.none())
def test_plug_state_matches_the_core(status, flag) -> None:
    signals: dict[str, Value] = {}
    if status is not None:
        signals["wallbox.evcs.status"] = status
    if flag is not None:
        signals["wallbox.twc.vehicle_connected"] = flag
    shot = snap(signals)
    assert wallbox_connected(EVCS, "evcs", shot.ok) == _evcs_state(shot, "evcs")[0]
    assert wallbox_connected(TWC, "twc", shot.ok) == _twc_state(shot, "twc")[0]
