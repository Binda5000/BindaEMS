"""Eigenschaften des CoE-V2-Codecs (Plan 6a, Präzisierung 1).

hypothesis erzeugt Pakete mit gültigen Wertebereichen, beliebige Bytes und Versätze.
"""

from hypothesis import given
from hypothesis import strategies as st

from bindaems.shared.coe import CoeError, CoeValue, decode, encode, offset_packet

values = st.builds(
    CoeValue,
    node=st.integers(0, 255),
    output=st.integers(1, 256),
    analog=st.booleans(),
    unit=st.integers(0, 255),
    raw=st.integers(-(2**31), 2**31 - 1),
)


@given(st.lists(values, min_size=1, max_size=16))
def test_decode_inverts_encode(batch: list[CoeValue]) -> None:
    packet = encode(batch)
    assert len(packet) == 4 + 8 * len(batch)
    assert decode(packet) == batch


@given(st.binary(max_size=200))
def test_decode_never_raises_anything_but_coe_error(data: bytes) -> None:
    try:
        result = decode(data)
    except CoeError:
        return
    assert 1 <= len(result) <= 16
    assert encode(result)[4:] == data[4:]  # was durchkommt, ist ein Paket, das wir selbst bauen


@given(
    st.integers(-30, 30).map(lambda tenths: tenths / 10),
    st.integers(1, 62),
    st.integers(1, 64),
)
def test_offset_packet_round_trips_in_tenths(offset_k: float, node: int, output: int) -> None:
    (value,) = decode(offset_packet(offset_k, node=node, output=output))
    assert (value.node, value.output, value.analog, value.unit) == (node, output, True, 1)
    assert value.raw / 10 == offset_k
