"""Gemeinsame Helfer der Prüfungen."""

from __future__ import annotations

import re

from bindaems.shared.domain import Snapshot, Value

_SCHEDULE_DAY = re.compile(r"ess\.schedule\.(?P<k>\d+)\.day")


def number(snap: Snapshot, signal: str) -> float | None:
    """Zahlenwert eines ``OK``-Signals, sonst ``None``."""
    value = snap.ok(signal)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def fmt(value: Value) -> str:
    """Zahlen ohne überflüssige Nachkommastellen (``1.0`` → ``1``)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:g}"
    return str(value)


def schedule_days(snap: Snapshot) -> dict[int, float]:
    """``OK``-Werte der Victron-Ladefenster, numerisch nach Fensternummer sortiert."""
    days: dict[int, float] = {}
    for signal in snap.readings:
        found = _SCHEDULE_DAY.fullmatch(signal)
        day = number(snap, signal) if found else None
        if found and day is not None:
            days[int(found["k"])] = day
    return dict(sorted(days.items()))
