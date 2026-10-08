import pytest
from tests.unit.core.telemetry.conftest import TS

from bindaems.core.telemetry.lineprotocol import Point, to_line


def test_to_line_types_and_order() -> None:
    p = Point(
        "power",
        {"source": "grid", "phase": "L1"},
        {"p_w": 812.5, "n": 3, "ok": True, "txt": 'a "b"'},
        TS,
    )
    assert (
        to_line(p)
        == 'power,phase=L1,source=grid n=3i,ok=true,p_w=812.5,txt="a \\"b\\"" 1791453600123'
    )


def test_to_line_escapes_tags() -> None:
    p = Point("power", {"id": "Ober geschoss,1=2"}, {"p_w": 1.0}, TS)
    assert to_line(p).startswith("power,id=Ober\\ geschoss\\,1\\=2 ")


def test_to_line_escapes_measurement_field_keys_and_backslash_in_strings() -> None:
    p = Point("my power,x", {}, {"a b=c": 1.0, "s": "x\\y"}, TS)
    assert to_line(p) == 'my\\ power\\,x a\\ b\\=c=1.0,s="x\\\\y" 1791453600123'


def test_to_line_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        to_line(Point("power", {}, {}, TS))


def test_to_line_skips_non_finite_floats() -> None:
    p = Point("power", {}, {"a": float("nan"), "b": 2.0, "c": float("inf")}, TS)
    assert to_line(p) == "power b=2.0 1791453600123"
    with pytest.raises(ValueError):
        to_line(Point("power", {}, {"a": float("nan")}, TS))


def test_to_line_omits_empty_tag_values() -> None:
    assert to_line(Point("power", {"source": ""}, {"v": 1.0}, TS)) == "power v=1.0 1791453600123"
