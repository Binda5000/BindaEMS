"""Eine Leistungsquelle: alles, dessen Energie und HA-Werte die app berechnet (Verbraucher, PV)."""

from __future__ import annotations

from typing import Protocol


class PowerSource(Protocol):
    """Leistung aus einem core-Signal (``source_kind == "core"``) oder einer HA-Entität."""

    @property
    def id(self) -> int: ...

    @property
    def source_kind(self) -> str: ...

    @property
    def power_ref(self) -> str: ...

    @property
    def power_unit(self) -> str | None: ...
