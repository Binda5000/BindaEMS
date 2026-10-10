"""Gemeinsame Helfer der Prüfungen."""

from __future__ import annotations

import re

from bindaems.core.adapters.evcs import KNOWN_PRODUCT_IDS
from bindaems.shared.domain import Snapshot, Value

_SCHEDULE_DAY = re.compile(r"ess\.schedule\.(?P<k>\d+)\.day")


def number(snap: Snapshot, signal: str) -> float | None:
    """Zahlenwert eines ``OK``-Signals, sonst ``None``."""
    value = snap.ok(signal)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def evcs_mode(snap: Snapshot, name: str) -> float | None:
    """Modus der EVCS, wie ihn der Cerbo liest; ohne Cerbo nur aus gültigem eigenem Registerabbild.

    Ein leeres Registerabbild (alle 0, Prüfprotokoll vom 10.10.2026) läse sich sonst als „manuell“.
    """
    gx = number(snap, f"wallbox.{name}.gx_mode")
    if gx is not None:
        return gx
    product = number(snap, f"wallbox.{name}.product_id")
    if product is not None and int(product) in KNOWN_PRODUCT_IDS:
        return number(snap, f"wallbox.{name}.mode")
    return None


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
