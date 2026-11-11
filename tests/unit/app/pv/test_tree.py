from bindaems.app.pv.service import PvSource
from bindaems.app.pv.tree import build_pv_tree


def src(id_: int, name: str, parent: int | None = None, kwp: float | None = None, sort: int = 0):
    return PvSource(
        id=id_,
        name=name,
        parent_id=parent,
        color="#f59e0b",
        source_kind="core",
        power_ref=f"pv.s{id_}.power_w",
        power_unit=None,
        kwp=kwp,
        sort=sort,
    )


def tree(sources, watts, kwh=None, total_w=None, total_kwh=None):
    kwh = kwh or {}
    return build_pv_tree(
        sources,
        lambda s: watts.get(s.id),
        lambda s: None,
        lambda s: (kwh.get(s.id), None),
        total_w,
        total_kwh,
    )


def test_unassigned_is_total_minus_systems() -> None:
    nodes = tree([src(1, "Huawei")], {1: 4200.0}, {1: 8.4}, 5000.0, 10.0)
    rest = nodes[-1]
    assert (rest.id, rest.kind, rest.name) == (None, "unassigned", "Nicht zugeordnet")
    assert (rest.power_w, rest.kwh_today) == (800.0, 1.6)


def test_no_unassigned_without_systems() -> None:
    assert tree([], {}, total_w=5000.0, total_kwh=10.0) == []


def test_unassigned_unknown_if_a_system_unknown() -> None:
    nodes = tree([src(1, "A"), src(2, "B")], {1: 100.0}, {1: 1.0}, 5000.0, 10.0)
    assert nodes[-1].power_w is None and nodes[-1].kwh_today is None
    assert nodes[0].kind == "system"


def test_strings_sum_without_rest_row() -> None:
    sources = [src(1, "Huawei"), src(2, "S1", 1), src(3, "S2", 1)]
    nodes = tree(sources, {1: 4200.0, 2: 1800.0, 3: 2550.0}, {1: 8.0, 2: 3.0, 3: 4.0})
    system = nodes[0]
    assert [c.name for c in system.children] == ["S1", "S2"]
    assert all(c.kind == "string" for c in system.children)
    assert (system.strings_power_w, system.strings_kwh_today) == (4350.0, 7.0)
    assert "unassigned" not in [c.kind for c in system.children]


def test_strings_sum_unknown_if_a_string_unknown() -> None:
    sources = [src(1, "A"), src(2, "S1", 1), src(3, "S2", 1)]
    system = tree(sources, {1: 1.0, 2: 1.0})[0]
    assert system.strings_power_w is None and system.strings_kwh_today is None


def test_kwh_per_kwp() -> None:
    sources = [src(1, "A", kwp=4.1), src(2, "B"), src(3, "C", kwp=5.0)]
    nodes = tree(sources, {}, {1: 8.2, 2: 8.2, 3: 0.0})
    assert [n.kwh_per_kwp for n in nodes[:3]] == [2.0, None, 0.0]


def test_zero_watts_is_a_value() -> None:
    node = tree([src(1, "A")], {1: 0.0}, {1: 0.0})[0]
    assert (node.power_w, node.note) == (0.0, None)


def test_sorted_by_sort_then_name() -> None:
    nodes = tree([src(1, "B"), src(2, "C", sort=-1), src(3, "A")], {})
    assert [n.name for n in nodes[:3]] == ["C", "A", "B"]
