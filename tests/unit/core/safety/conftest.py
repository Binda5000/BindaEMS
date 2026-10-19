"""Gemeinsame Hilfen für die Tests der Safety-Schicht.

Andere Tests importieren sie ausdrücklich aus ``tests.unit.core.safety.conftest``.
"""

from typing import Any

from tests.helpers import T0

from bindaems.core.safety.types import InputStatus, SafetyContext
from bindaems.shared.control import ActuatorMode, OperatingState

PHASES = ("L1", "L2", "L3")


def make_ctx(**changes: Any) -> SafetyContext:
    """Kontext im Regelbetrieb: alle Aktoren live, nichts gesperrt, Eingangsdaten ok, 10 A je Phase.

    ``changes`` ersetzt einzelne Felder; ein unbekannter Feldname ist ein Fehler.
    """
    fields: dict[str, Any] = {
        "now": T0,
        "operating": OperatingState.CONTROL,
        "modes": dict.fromkeys(
            ("victron", "wallbox.evcs", "vehicle.tesla", "thermal.klima"), ActuatorMode.LIVE
        ),
        "locks": {},
        "inputs": InputStatus(True),
        "phase_import_a": dict.fromkeys(PHASES, 10.0),
        "wallbox_phase_a": {},
        "vehicle_wallbox": {"vehicle.tesla": "twc"},
        "present": {},
        "battery_discharge_w": 0.0,
        "soc_pct": 50.0,
        "base_grid_setpoint_w": 0.0,
        "grid_budget_w": None,
    }
    return SafetyContext(**(fields | changes))
