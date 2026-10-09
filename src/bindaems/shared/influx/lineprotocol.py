"""InfluxDB-Line-Protocol (1.x) mit Zeitstempeln in Millisekunden."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from bindaems.shared.timeutil import ensure_utc

FieldValue = float | int | bool | str

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MEASUREMENT_ESCAPES = str.maketrans({",": r"\,", " ": r"\ "})
_KEY_ESCAPES = str.maketrans({",": r"\,", "=": r"\=", " ": r"\ "})


@dataclass(frozen=True)
class Point:
    measurement: str
    tags: Mapping[str, str]
    fields: Mapping[str, FieldValue]
    ts: datetime
    rp: str = "raw"


class PointSink(Protocol):
    """Nimmt Messpunkte entgegen (z. B. ``InfluxWriter``); ``write`` blockiert nie."""

    def write(self, point: Point) -> None: ...


def _field(value: FieldValue) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else None  # Influx lehnt NaN/inf ab
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def epoch_ms(ts: datetime) -> int:
    return (ensure_utc(ts) - _EPOCH) // timedelta(milliseconds=1)


def to_line(p: Point) -> str:
    """Eine Zeile; leere Tag-Werte und nicht endliche Zahlen entfallen.

    Ohne gültiges Feld wird ``ValueError`` ausgelöst.
    """
    fields = [
        f"{key.translate(_KEY_ESCAPES)}={text}"
        for key, value in sorted(p.fields.items())
        if (text := _field(value)) is not None
    ]
    if not fields:
        raise ValueError(f"Messpunkt ohne gültige Felder: {p.measurement}")
    head = p.measurement.translate(_MEASUREMENT_ESCAPES)
    tags = "".join(
        f",{key.translate(_KEY_ESCAPES)}={value.translate(_KEY_ESCAPES)}"
        for key, value in sorted(p.tags.items())
        if value != ""
    )
    return f"{head}{tags} {','.join(fields)} {epoch_ms(p.ts)}"
