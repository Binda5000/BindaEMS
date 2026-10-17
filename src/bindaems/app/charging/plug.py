"""Steckzustand einer Wallbox aus dem State des core (Plan 4a, Präzisierung 5).

Dieselbe Regel wie der core in ``charge_inputs``: Bei der EVCS heißt ``wallbox.<id>.status``
ungleich 0 „verbunden“, beim Wall Connector gilt ``wallbox.<id>.vehicle_connected``. Fehlt das
Signal oder hat es nicht die erwartete Art, ist der Zustand unbekannt (``None``). Die app
importiert den core nicht; ein Test vergleicht beide.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from bindaems.shared.config import EvcsConfig, TwcConfig
from bindaems.shared.domain import Value
from bindaems.shared.numbers import finite_number

EVCS_DISCONNECTED: Final = 0  # Status der EVCS „getrennt“


def wallbox_connected(
    wallbox: EvcsConfig | TwcConfig, key: str, value: Callable[[str], Value]
) -> bool | None:
    """Ob an der Wallbox ``key`` ein Auto steckt; ``value`` wie ``LiveState.value``."""
    if isinstance(wallbox, TwcConfig):
        flag = value(f"wallbox.{key}.vehicle_connected")
        return flag if isinstance(flag, bool) else None
    status = finite_number(value(f"wallbox.{key}.status"))
    return None if status is None else status != EVCS_DISCONNECTED
