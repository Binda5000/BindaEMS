from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bindaems.app.ledger.estimate import allocate_wh, estimate_gap
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


@given(
    imp=st.floats(0, 200),
    exp=st.floats(0, 200),
    pv=st.floats(0, 300),
    battery=st.floats(-30, 30),
    wbs=st.lists(st.floats(0, 100), min_size=2, max_size=2),
    n=st.integers(1, 672),
)
def test_estimated_slots_keep_the_grid_balance_of_the_gap(
    imp: float, exp: float, pv: float, battery: float, wbs: list[float], n: int
) -> None:
    # Σ (Bezug − Einspeisung) der Slots = Bezug − Einspeisung der Lücke, auch wenn das Haus als
    # Rest negativ wäre (der Fehlbetrag zählt als DC-PV)
    energies = {"import": imp, "export": exp, "pv": pv, "wb:evcs": wbs[0], "wb:twc": wbs[1]}
    slots = estimate_gap(datetime(2026, 10, 9, tzinfo=UTC), n, energies, battery, ["evcs", "twc"])
    assert len(slots) == n
    grid_in = sum(wh for s in slots for name, wh in s.flows_wh.items() if name.startswith("grid>"))
    grid_out = sum(wh for s in slots for name, wh in s.flows_wh.items() if name.endswith(">grid"))
    assert (grid_in - grid_out) / 1000 == pytest.approx(imp - exp, abs=1e-6)
    assert all(wh >= 0 for s in slots for wh in s.flows_wh.values())
