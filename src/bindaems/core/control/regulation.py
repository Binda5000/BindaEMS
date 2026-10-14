"""Regelungs-Kern: die einzige Schnittstelle der Laufzeit zur Regelung (Plan 2c-2a, Design 4.1).

- ``RegulationInputs``: die Eingaben eines Zyklus der Regelung
- ``Regulation``: der Kern; er hält die Bausteine der Regelung und gibt jeden Aufruf an sie weiter

Sein einziger Baustein ist bisher die Laderegelung (``ChargeController``): Jede Methode gibt an sie
weiter, und ``alarms`` und ``grid_budget_w`` haben noch nichts zu melden.

Der Kern fängt keine Ausnahme; das tut die Verdrahtung neben der Laufzeit (``RegulationLink``,
Plan 2c-2a, Präzisierung 15). Die Schicht ist rein: Die Zeit kommt mit den Eingaben. Nicht für
mehrere Threads gedacht.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bindaems.core.control.charge_inputs import ChargeInputs
from bindaems.core.control.charging import ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import Command
from bindaems.shared.domain import Alarm


@dataclass(frozen=True)
class RegulationInputs:
    """Die Eingaben eines Zyklus der Regelung.

    - ``charge``: die Eingaben der Laderegelung; ihre Uhren gelten für den ganzen Kern
    """

    charge: ChargeInputs


class Regulation:
    """Der Regelungs-Kern: je Zyklus die Befehle seiner Bausteine.

    ``charger`` ist die Laderegelung. ``started`` ist die Wanduhr beim Start der Regelung
    (``ChargeController``). Der Zustand lebt nur im Speicher.
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self.charger = ChargeController(cfg, limits, started=started)

    def step(self, inputs: RegulationInputs) -> list[ControlCommand]:
        """Ein Regelzyklus: die Befehle der Ladepunkte in der Reihenfolge der Priorität."""
        return self.charger.step(inputs.charge)

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Das Urteil der Safety-Schicht über einen eigenen Befehl (``ChargeController``)."""
        self.charger.note_verdict(command, verdict, not_sent=not_sent)

    def note_result(self, result: WriteResult) -> None:
        """Das Ergebnis eines abgeschlossenen Schreibvorgangs, auch eines des Keepers."""
        self.charger.note_result(result)

    def forget(self, actuators: Collection[str]) -> None:
        """Vergisst die letzten eigenen Werte dieser Aktoren."""
        self.charger.forget(actuators)

    def idle(self, now: datetime) -> None:
        """Ein Zyklus ohne Regelung; ``now`` ist die Zeit der Safety-Schicht."""
        self.charger.idle(now)

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox aus der Zuordnung der Laderegelung, für die Safety-Schicht."""
        return self.charger.vehicle_wallbox()

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten Zyklus: ``vehicles`` je Fahrzeug (``ChargeController.health``)."""
        return {"vehicles": self.charger.health()}

    def alarms(self, wall_now: datetime) -> list[Alarm]:
        """Die Alarme der Regelung zur Wanduhr ``wall_now``; bisher keine."""
        return []

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext``; bisher keins (``None``)."""
        return None
