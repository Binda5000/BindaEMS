import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.app_helpers import k, tree_input

from bindaems.app.consumers.values import build_tree


@given(st.lists(st.floats(min_value=0, max_value=5000), min_size=1, max_size=8))
def test_children_plus_other_equal_parent_when_consistent(children: list[float]) -> None:
    consumers, power = tree_input([k(i + 1, p) for i, p in enumerate(children)])
    house = sum(children) + 100.0
    root = build_tree(consumers, power, house_w=house)
    assert root.other_w == pytest.approx(100.0) and not root.mismatch
