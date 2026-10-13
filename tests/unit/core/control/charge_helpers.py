"""Gemeinsame Hilfen für die Tests der Laderegelung (nur für Tests, keine Testfunktionen).

Die Vorgaben sind die Annahmen aller Tests der Laderegelung; ein Test ändert nur, was er prüft:

- ``evcs``: die EVCS mit einem Fahrzeug, ``twc``: der Wall Connector ohne Fahrzeug
- ``tesla``: der Tesla mit Tessie, abgesteckt; ``NONE``: ein Fahrzeug ohne Tessie (der e-Golf)
- ``grid``: das Netz mit 2 A je Phase und Überschuss
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, GridObs, VehicleObs, WallboxObs
from bindaems.core.state.derived import PHASES

NONE = NO_VEHICLE_DATA


def evcs(**kw: Any) -> WallboxObs:
    """Die EVCS: erreichbar und verbunden, lädt nicht, nicht geladen, 0 A je Phase, 0 W."""
    base = WallboxObs(True, True, False, False, dict.fromkeys(PHASES, 0.0), 0.0)
    return replace(base, **kw)


def twc(**kw: Any) -> WallboxObs:
    """Der Wall Connector: erreichbar, kein Fahrzeug, lädt nicht, ``complete`` unbekannt.

    Er misst 0 A je Phase und 0 W.
    """
    base = WallboxObs(True, False, False, None, dict.fromkeys(PHASES, 0.0), 0.0)
    return replace(base, **kw)


def tesla(**kw: Any) -> VehicleObs:
    """Der Tesla: erreichbar, nicht angesteckt, lädt nicht, nicht geladen, wach, zu Hause.

    Er zieht 0 A und hat 3 Phasen.
    """
    return replace(VehicleObs(True, False, False, False, False, True, 0.0, 3), **kw)


def grid(
    l1: float | None = 2.0,
    l2: float | None = 2.0,
    l3: float | None = 2.0,
    *,
    feed: tuple[float | None, float | None, float | None] = (0.0, 0.0, 0.0),
    grid_w: float | None = -4000.0,
    battery_w: float | None = 0.0,
    soc: float | None = 60.0,
) -> GridObs:
    """Das Netz: Netzstrom je Phase (``l1`` bis ``l3``), Akku-Einspeisung ``feed``, Überschuss."""
    return GridObs(
        phase_import_a=dict(zip(PHASES, (l1, l2, l3), strict=True)),
        battery_feed_a=dict(zip(PHASES, feed, strict=True)),
        grid_w=grid_w,
        battery_w=battery_w,
        soc_pct=soc,
    )
