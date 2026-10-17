import pytest
from hypothesis import given
from hypothesis import strategies as st

from bindaems.app.ledger.estimate import allocate_wh
from bindaems.core.accounting.flows import allocate


@given(
    pv=st.floats(0, 20000),
    battery=st.floats(-12000, 12000),
    house=st.floats(0, 15000),
    wbs=st.lists(st.floats(0, 11000), max_size=3),
)
def test_allocation_matches_the_core(
    pv: float, battery: float, house: float, wbs: list[float]
) -> None:
    wallboxes = {f"wb{i}": wh for i, wh in enumerate(wbs)}
    grid = house + sum(wbs) + battery - pv  # Netz aus der Bilanz
    expected = allocate(pv, grid, battery, house, wallboxes)
    actual = allocate_wh(pv, battery, house, wallboxes)
    assert actual.keys() == expected.keys()
    for flow, value in expected.items():
        assert actual[flow] == pytest.approx(value, abs=1e-9)


@given(
    pv=st.floats(-100, 20000),
    battery=st.floats(-12000, 12000),
    house=st.floats(-100, 15000),
    wbs=st.lists(st.floats(-100, 11000), max_size=3),
)
def test_allocation_matches_the_core_for_negative_inputs(
    pv: float, battery: float, house: float, wbs: list[float]
) -> None:
    wallboxes = {f"wb{i}": wh for i, wh in enumerate(wbs)}
    expected = allocate(pv, 0.0, battery, house, wallboxes)
    actual = allocate_wh(pv, battery, house, wallboxes)
    assert actual.keys() == expected.keys()
    for flow, value in expected.items():
        assert actual[flow] == pytest.approx(value, abs=1e-9)
