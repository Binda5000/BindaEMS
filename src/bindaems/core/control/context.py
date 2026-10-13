"""Der ``SafetyContext`` eines Zyklus und die Eingangsgrößen seiner Entscheidungen (Spec 7.10, 8.3).

``safety_context`` stellt aus Snapshot und abgeleiteten Größen zusammen, was die Safety-Schicht für
die Prüfung eines Befehls braucht. ``decision_inputs`` nennt die Größen, die jede Entscheidung
mitschreibt. Die Netzströme je Phase und die Wallbox-Ströme kommen fertig berechnet als Argumente;
das Modul liest den Zustandsspeicher nicht.

Ein Wert zählt nur mit Qualität ``OK`` und als endliche Zahl (``finite_number``); sonst ist er
unbekannt (``None``). Die Schicht ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from bindaems.core.safety.types import InputStatus, SafetyContext
from bindaems.shared.config import Phase
from bindaems.shared.control import ActuatorMode, CommandKind, CommandValue, OperatingState
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import finite_number

_PHASES: Final[tuple[Phase, ...]] = ("L1", "L2", "L3")


def _number(snap: Snapshot, signal: str) -> float | None:
    """Der Wert des Signals als endliche Zahl mit Qualität ``OK``; sonst ``None`` (unbekannt)."""
    return finite_number(snap.ok(signal))


def safety_context(
    *,
    now: datetime,
    operating: OperatingState,
    modes: Mapping[str, ActuatorMode],
    locks: Mapping[str, tuple[str, ...]],
    inputs: InputStatus,
    snap: Snapshot,
    phase_import_a: Mapping[Phase, float | None],
    wallbox_phase_a: Mapping[str, Mapping[Phase, float]],
    present: Mapping[tuple[str, CommandKind], CommandValue],
    vehicle_wallbox: Mapping[str, str | None] = MappingProxyType({}),
) -> SafetyContext:
    """Der Kontext für die Befehle eines Zyklus; ``now`` ist die Zeit der Safety-Schicht.

    ``vehicle_wallbox`` nennt je Fahrzeug-Aktor die Wallbox aus der Zuordnung der Laderegelung
    (``ChargeController.vehicle_wallbox``). Fehlt ein Fahrzeug darin oder steht dort ``None``, ist
    seine Wallbox unbekannt, und die Phasenvorhersage rechnet vorsichtig mit allen drei Phasen; ohne
    Angabe gilt das für jedes Fahrzeug.

    Was nicht aus den Argumenten übernommen wird:

    - ``battery_discharge_w`` ist ``max(−battery.power_w, 0)``; die Akkuleistung ist beim Laden
      positiv.
    - ``soc_pct`` ist ``battery.soc_pct``.
    - ``base_grid_setpoint_w`` ist ``ess.grid_setpoint_w``, der persistente Netz-Sollwert.
    - ``grid_budget_w`` ist ``None``: Es gibt noch kein 15-min-Budget.
    """
    battery_w = _number(snap, "battery.power_w")
    return SafetyContext(
        now=now,
        operating=operating,
        modes=modes,
        locks=locks,
        inputs=inputs,
        phase_import_a=phase_import_a,
        wallbox_phase_a=wallbox_phase_a,
        vehicle_wallbox=vehicle_wallbox,
        present=present,
        battery_discharge_w=None if battery_w is None else max(-battery_w, 0.0),
        soc_pct=_number(snap, "battery.soc_pct"),
        base_grid_setpoint_w=_number(snap, "ess.grid_setpoint_w"),
        grid_budget_w=None,
    )


def decision_inputs(
    snap: Snapshot, phase_import_a: Mapping[Phase, float | None]
) -> dict[str, float | None]:
    """Die Eingangsgrößen, die jede Entscheidung des Zyklus mitschreibt; ``None`` heißt unbekannt.

    ``soc_pct`` (Akku-SOC in %), ``battery_w`` (Akkuleistung in W, positiv beim Laden) und
    ``grid_l1_a`` bis ``grid_l3_a`` (Netzbezug je Phase in A, positiv bei Bezug).
    """
    inputs: dict[str, float | None] = {
        "soc_pct": _number(snap, "battery.soc_pct"),
        "battery_w": _number(snap, "battery.power_w"),
    }
    for phase in _PHASES:
        inputs[f"grid_{phase.lower()}_a"] = finite_number(phase_import_a.get(phase))
    return inputs
