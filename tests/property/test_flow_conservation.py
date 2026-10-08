import pytest
from hypothesis import given
from hypothesis import strategies as st

from bindaems.core.accounting.flows import allocate


@given(
    pv=st.floats(0, 20000),
    house=st.floats(0, 15000),
    wbs=st.lists(st.floats(0, 11000), max_size=2),
    battery=st.floats(-12000, 12000),
)
def test_flows_conserve_energy(pv: float, house: float, wbs: list[float], battery: float) -> None:
    wallbox_w = {f"wb{i}": w for i, w in enumerate(wbs)}
    grid = house + sum(wbs) + battery - pv
    flows = allocate(pv, grid, battery, house, wallbox_w)
    assert all(v >= 0 for v in flows.values())

    def out(src: str) -> float:
        return sum(v for k, v in flows.items() if k.startswith(src + ">"))

    def into(dst: str) -> float:
        return sum(v for k, v in flows.items() if k.endswith(">" + dst))

    assert out("pv") == pytest.approx(pv, abs=1e-6)
    assert out("grid") == pytest.approx(max(grid, 0), abs=1e-6)
    assert out("battery") == pytest.approx(max(-battery, 0), abs=1e-6)
    assert into("house") == pytest.approx(house, abs=1e-6)
    assert into("battery") == pytest.approx(max(battery, 0), abs=1e-6)
    assert into("grid") == pytest.approx(max(-grid, 0), abs=1e-6)
    for name, w in wallbox_w.items():
        assert into(f"wb:{name}") == pytest.approx(w, abs=1e-6)
