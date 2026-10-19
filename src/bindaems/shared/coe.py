"""CoE-V2-Codec (UDP, Technische Alternative C.M.I.): rein, ohne IO.

Ein Paket besteht aus einem Kopf ``0x02 0x00 <4+8n> <n>`` und ``n`` Werten zu je acht Bytes:
``<Knoten> <Ausgang−1> <1 = analog | 0 = digital> <Einheit> <int32 little endian>``. Bei digitalen
Werten steht 0 oder 1 im ersten Byte des Rohwerts (Plan 6a, Präzisierung 1).
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from bindaems.shared.config import CoeChannel

_VERSION = 2
_HEADER = struct.Struct("<BBBB")
_VALUE = struct.Struct("<BBBBi")
_MAX_VALUES = 16
_TENTHS_UNITS = frozenset({1, 7})  # °C und K kommen in Zehnteln
_INT32 = range(-(2**31), 2**31)


class CoeError(ValueError):
    """Ein empfangenes Paket ist kein gültiges CoE-V2-Paket."""


@dataclass(frozen=True)
class CoeValue:
    """Ein Wert eines CoE-Pakets; ``output`` ist 1-basiert wie in der UVR."""

    node: int
    output: int
    analog: bool
    unit: int
    raw: int


def encode(values: Sequence[CoeValue]) -> bytes:
    """Das Paket mit den Werten; ``ValueError`` bei falscher Anzahl oder Feldern außerhalb."""
    count = len(values)
    if not 1 <= count <= _MAX_VALUES:
        raise ValueError(f"Ein CoE-Paket trägt 1 bis {_MAX_VALUES} Werte, nicht {count}.")
    parts = [_HEADER.pack(_VERSION, 0, _HEADER.size + _VALUE.size * count, count)]
    for value in values:
        if not (
            0 <= value.node <= 255
            and 1 <= value.output <= 256
            and 0 <= value.unit <= 255
            and value.raw in _INT32
        ):
            raise ValueError(f"Der CoE-Wert {value} liegt außerhalb des Wertebereichs.")
        parts.append(
            _VALUE.pack(value.node, value.output - 1, int(value.analog), value.unit, value.raw)
        )
    return b"".join(parts)


def decode(data: bytes) -> list[CoeValue]:
    """Die Werte des Pakets; ``CoeError`` bei falscher Version, Länge oder Anzahl."""
    if len(data) < _HEADER.size:
        raise CoeError(f"Das CoE-Paket ist mit {len(data)} Bytes kürzer als der Kopf.")
    version, _, length, count = _HEADER.unpack_from(data)
    if version != _VERSION:
        raise CoeError(f"Die CoE-Version {version} wird nicht unterstützt.")
    if not 1 <= count <= _MAX_VALUES:
        raise CoeError(f"Ein CoE-Paket trägt 1 bis {_MAX_VALUES} Werte, nicht {count}.")
    expected = _HEADER.size + _VALUE.size * count
    if length != expected or len(data) != expected:
        raise CoeError(
            f"Die Länge passt nicht: erwartet {expected} Bytes, Kopf sagt {length}, "
            f"empfangen {len(data)}."
        )
    values: list[CoeValue] = []
    for node, output, kind, unit, raw in _VALUE.iter_unpack(data[_HEADER.size :]):
        if kind > 1:
            raise CoeError(f"Die Art {kind} eines CoE-Werts ist weder analog noch digital.")
        values.append(CoeValue(node, output + 1, kind == 1, unit, raw))
    return values


def offset_packet(offset_k: float, *, node: int, output: int) -> bytes:
    """Das Paket mit einem Sollwertversatz in Kelvin: analog, Einheit 1, in Zehnteln."""
    return encode([CoeValue(node, output, True, 1, round(offset_k * 10))])


def scaled(value: CoeValue, channel: CoeChannel) -> float | bool | None:
    """Der Wert in seiner Einheit, oder ``None``, wenn er nicht zum Kanal passt.

    Analog: Einheit 1 und 7 in Zehnteln, jede andere Einheit durch ``channel.scale``. Hat der Kanal
    eine Einheit und das Paket eine andere, ist der Wert ungültig. Digital: das erste Byte des
    Rohwerts.
    """
    if value.analog != (channel.analog is not None):
        return None
    if not value.analog:
        return bool(value.raw & 0xFF)
    if channel.unit is not None and channel.unit != value.unit:
        return None
    if value.unit in _TENTHS_UNITS:
        return value.raw / 10
    if channel.scale is None:
        return None
    return value.raw / channel.scale
