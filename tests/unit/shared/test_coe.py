import pytest

from bindaems.shared.coe import CoeError, CoeValue, decode, encode, offset_packet, scaled
from bindaems.shared.config import CoeChannel


def test_vector_from_coe_rs() -> None:
    packet = encode([CoeValue(1, 2, True, 1, -102)])
    assert packet[4:] == bytes([1, 1, 1, 1, 0x9A, 0xFF, 0xFF, 0xFF])


def test_header_has_version_length_and_count() -> None:
    packet = encode([CoeValue(1, 1, True, 1, 0), CoeValue(2, 3, False, 43, 1)])
    assert packet[:4] == bytes([2, 0, 20, 2])
    assert len(packet) == 20


def test_offset_packet() -> None:
    data = offset_packet(-1.5, node=60, output=1)
    assert data[:4] == bytes([2, 0, 12, 1])
    assert decode(data) == [CoeValue(60, 1, True, 1, -15)]


def test_offset_packet_rounds_to_tenths() -> None:
    assert decode(offset_packet(0.5, node=60, output=1))[0].raw == 5
    assert decode(offset_packet(0.0, node=60, output=64))[0] == CoeValue(60, 64, True, 1, 0)


def test_digital_value_in_first_byte() -> None:
    packet = encode([CoeValue(56, 5, False, 43, 1)])
    assert packet[4:] == bytes([56, 4, 0, 43, 1, 0, 0, 0])
    assert decode(packet)[0].raw == 1


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x02\x00\x04",
        b"\x02\x00\x04\x00",
        b"\x01\x00\x0c\x01" + bytes(8),
        b"\x02\x00\x0d\x01" + bytes(8),
        b"\x02\x00\x0c\x02" + bytes(8),
        b"\x02\x00\x0c\x01" + bytes(7),
        b"\x02\x00\x0c\x01" + bytes(9),
        b"\x02\x00\x0c\x01" + bytes([1, 0, 2, 1, 0, 0, 0, 0]),
        bytes([2, 0, 4 + 8 * 17, 17]) + bytes(8 * 17),
    ],
)
def test_decode_rejects(data: bytes) -> None:
    with pytest.raises(CoeError):
        decode(data)


def test_coe_error_is_a_value_error() -> None:
    assert issubclass(CoeError, ValueError)


@pytest.mark.parametrize("count", [0, 17])
def test_encode_rejects_wrong_count(count: int) -> None:
    with pytest.raises(ValueError, match="1 bis 16"):
        encode([CoeValue(1, 1, True, 1, 0)] * count)


@pytest.mark.parametrize(
    "value",
    [
        CoeValue(256, 1, True, 1, 0),
        CoeValue(-1, 1, True, 1, 0),
        CoeValue(1, 0, True, 1, 0),
        CoeValue(1, 257, True, 1, 0),
        CoeValue(1, 1, True, 256, 0),
        CoeValue(1, 1, True, 1, 2**31),
        CoeValue(1, 1, True, 1, -(2**31) - 1),
    ],
)
def test_encode_rejects_out_of_range_fields(value: CoeValue) -> None:
    with pytest.raises(ValueError, match="Wertebereich"):
        encode([value])


def test_encode_accepts_the_int32_limits() -> None:
    values = [CoeValue(1, 1, True, 1, -(2**31)), CoeValue(1, 2, True, 1, 2**31 - 1)]
    assert decode(encode(values)) == values


def analog(unit: int | None = None, scale: float | None = None) -> CoeChannel:
    return CoeChannel(node=1, analog=2, unit=unit, scale=scale)


def digital() -> CoeChannel:
    return CoeChannel(node=56, digital=5)


def test_scaled_temperature_tenths() -> None:
    assert scaled(CoeValue(1, 2, True, 1, 240), analog(unit=1)) == 24.0
    assert scaled(CoeValue(1, 2, True, 1, -15), analog(unit=1)) == -1.5


def test_scaled_kelvin_tenths() -> None:
    assert scaled(CoeValue(1, 2, True, 7, 5), analog(unit=7)) == 0.5


def test_scaled_unit_mismatch_is_none() -> None:
    assert scaled(CoeValue(1, 2, True, 7, 240), analog(unit=1)) is None
    assert scaled(CoeValue(1, 2, True, 10, 240), analog(unit=10, scale=100)) == 2.4
    assert scaled(CoeValue(1, 2, True, 1, 240), analog(unit=10, scale=100)) is None
    assert scaled(CoeValue(1, 2, True, 10, 240), analog(unit=1)) is None


def test_scaled_other_unit_divides_by_scale() -> None:
    assert scaled(CoeValue(1, 4, True, 10, 250), analog(unit=10, scale=100)) == 2.5


def test_scaled_without_channel_unit_follows_packet_unit() -> None:
    assert scaled(CoeValue(1, 2, True, 1, 240), analog()) == 24.0
    assert scaled(CoeValue(1, 2, True, 7, -15), analog()) == -1.5
    assert scaled(CoeValue(1, 2, True, 10, 250), analog(scale=100)) == 2.5
    assert scaled(CoeValue(1, 2, True, 10, 250), analog()) is None


def test_scaled_digital_uses_the_low_byte() -> None:
    assert scaled(CoeValue(56, 5, False, 43, 1), digital()) is True
    assert scaled(CoeValue(56, 5, False, 43, 0), digital()) is False
    assert scaled(CoeValue(56, 5, False, 43, 0x100), digital()) is False
    assert scaled(CoeValue(56, 5, False, 43, 0x1FF), digital()) is True


def test_scaled_kind_mismatch_is_none() -> None:
    assert scaled(CoeValue(1, 2, False, 1, 1), analog(unit=1)) is None
    assert scaled(CoeValue(56, 5, True, 1, 1), digital()) is None
