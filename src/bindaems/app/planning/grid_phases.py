"""Netzgrenze je Phase und Phasen der Autos als Grundlage des Planers (Entscheidungen 1 bis 4).

- ``grid_import_limits_w``: die Summengrenze (wie bisher) und die Grenze je Phase
- ``car_grid_phases``: auf welchen Netzphasen ein Auto an seiner Wallbox lädt
- ``phase_shares``: der Anteil der Ladeleistung eines Autos je Phase
- ``phase_room_w``: ``R_t``, die Grenze je Phase im Slot (hält das Modell lösbar)

Reine Rechnung ohne Lesen oder Schreiben.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from bindaems.shared.config import Config, ControlConfig, GridConfig, Phase

PHASES: Final[tuple[Phase, Phase, Phase]] = ("L1", "L2", "L3")


def grid_import_limits_w(grid: GridConfig, control: ControlConfig) -> tuple[float, float]:
    """``(Summe, je Phase)`` des erlaubten Netzbezugs in W.

    Summe: ``max_import_kw · 1000``, sonst ``(fuse_a − fuse_margin_a) · U · 3``. Je Phase:
    ``(import_limit_a − import_margin_a) · U`` (Entscheidung 1, nicht die Sicherung).
    """
    if grid.max_import_kw is not None:
        total_w = grid.max_import_kw * 1000
    else:
        total_w = (grid.fuse_a - grid.fuse_margin_a) * grid.voltage_nominal_v * 3
    phase_w = (grid.import_limit_a - control.import_margin_a) * grid.voltage_nominal_v
    return total_w, phase_w


def car_grid_phases(cfg: Config, vehicle_key: str, wallbox_key: str) -> list[Phase]:
    """Die Netzphasen, auf denen das Auto an der Wallbox lädt: ``phase_map[: phases]`` (wie
    ``core.control.charge_points``, Entscheidung 3)."""
    return list(cfg.wallboxes[wallbox_key].phase_map[: cfg.vehicles[vehicle_key].phases])


def phase_shares(phases: Sequence[Phase] | None) -> dict[Phase, float]:
    """Der Anteil der Ladeleistung je Phase: gleich verteilt auf die Phasen des Autos; ohne
    Angabe (``None``, alte Eingangsdaten) 3-phasig symmetrisch."""
    used = PHASES if phases is None else tuple(phases)
    return {phase: 1 / len(used) for phase in used}


def phase_room_w(limit_w: float, load_w: float, pv_w: float) -> float:
    """``R_t``: ``max(Grenze, max(0, Last − PV) / 3)`` (Entscheidung 4). Liegt das Haus allein
    schon über der Grenze, gilt sein Netzanteil, damit das Modell lösbar bleibt."""
    return max(limit_w, max(0.0, load_w - pv_w) / 3)
