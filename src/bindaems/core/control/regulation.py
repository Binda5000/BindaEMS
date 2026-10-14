"""Regelungs-Kern: die einzige Schnittstelle der Laufzeit zur Regelung (Plan 2c-2a, Design 4.1).

- ``RegulationInputs``: die Eingaben eines Zyklus der Regelung
- ``Regulation``: der Kern; er hält die Bausteine der Regelung und verteilt jeden Aufruf an sie
- ``battery_cars``: die Autos für den Akku-Sollwert (Präzisierung 4)

Die Bausteine sind die Laderegelung (``ChargeController``) und der Akku-Sollwert
(``BatteryController``). Ein Zyklus (``step``) rechnet in der Reihenfolge von Präzisierung 3:

1. die Laderegelung,
2. die Autos je Wallbox aus der Zuordnung und den Modi dieses Zyklus
   (``ChargeController.cars``), dazu die Wallboxen ohne Fahrzeug (``battery_cars``),
3. Fehlbetrag und erlaubten Akku-Anteil (``BatteryController.assess``),
4. den Override (``BatteryController.decide``).

Die Befehle kommen in dieser Reihenfolge: erst die der Ladepunkte, dann der Override. Beide
Bausteine laufen auf der Uhr der Safety-Schicht (``ChargeInputs.now``). Den 15-min-Wächter gibt es
noch nicht: Der Override rechnet ohne Budget, ``alarms`` und ``grid_budget_w`` haben nichts zu
melden.

Urteile und Ergebnisse zum Override (``victron``, ``grid_setpoint``) gehen nur an den
Akku-Sollwert, alle anderen nur an die Laderegelung; ``forget`` und ``idle`` erreichen beide.

Der Kern fängt keine Ausnahme; das tut die Verdrahtung neben der Laufzeit (``RegulationLink``,
Plan 2c-2a, Präzisierung 15). Die Schicht ist rein: Die Zeit kommt mit den Eingaben. Nicht für
mehrere Threads gedacht.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from bindaems.core.control.battery import BatteryCar, BatteryController
from bindaems.core.control.charge_inputs import ChargeInputs, WallboxObs
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, ChargeMode, Command, CommandKind, GuestIntent
from bindaems.shared.domain import Alarm

_SETPOINT: Final[Key] = (VICTRON, CommandKind.GRID_SETPOINT)  # der Override des Akku-Sollwerts


@dataclass(frozen=True)
class RegulationInputs:
    """Die Eingaben eines Zyklus der Regelung; ``None`` heißt unbekannt.

    - ``charge``: die Eingaben der Laderegelung; ihre Uhren, die Ist-Werte (``present``), die
      Fähigkeiten (``controllable``) und die blockierten Arten (``blocked``) gelten für den ganzen
      Kern
    - ``pv_w``, ``house_w``: PV-Leistung und Hauslast in W (``Derived.pv_total_w``,
      ``Derived.house_load_w``)
    - ``base_setpoint_w``: der Basis-Sollwert ``ess.grid_setpoint_w`` in W
    - ``locks``: die Gründe der Sperren je Aktor, wie im ``SafetyContext``
    """

    charge: ChargeInputs
    pv_w: float | None = None
    house_w: float | None = None
    base_setpoint_w: float | None = None
    locks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


class Regulation:
    """Der Regelungs-Kern: je Zyklus die Befehle seiner Bausteine.

    ``charger`` ist die Laderegelung, ``battery`` der Akku-Sollwert. ``started`` ist die Wanduhr
    beim Start der Regelung (``ChargeController``). Der Zustand lebt nur im Speicher.
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self.charger = ChargeController(cfg, limits, started=started)
        self.battery = BatteryController(cfg)

    def step(self, inputs: RegulationInputs) -> list[ControlCommand]:
        """Ein Regelzyklus (Präzisierung 3): die Befehle der Ladepunkte in der Reihenfolge der
        Priorität, dahinter der Override.

        Victron ist für den Override gesperrt, wenn ``inputs.locks`` für ``victron`` Gründe nennt,
        und steuerbar nach ``charge.controllable``. Ein offener Schreibvorgang für den Override oder
        eine Übergabe von Victron (``charge.blocked``) hält nur seinen Befehl dieses Zyklus zurück.
        """
        charge = inputs.charge
        commands = self.charger.step(charge)
        guest = (GuestIntent() if charge.intent is None else charge.intent.guest).mode
        cars = battery_cars(self._cfg, self.charger.cars(), charge.wallboxes, guest)
        battery = self.battery
        need = battery.assess(charge.now, charge.grid.soc_pct, inputs.pv_w, inputs.house_w, cars)
        override = battery.decide(
            charge.now,
            need,
            base_w=inputs.base_setpoint_w,
            budget_w=None,  # ohne 15-min-Wächter kein Budget
            present=charge.present,
            controllable=charge.controllable.get(VICTRON) is True,
            locked=bool(inputs.locks.get(VICTRON)),
            blocked=_SETPOINT in charge.blocked,
        )
        return [*commands, *override]

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Das Urteil der Safety-Schicht über einen eigenen Befehl: zum Override an den
        Akku-Sollwert, sonst an die Laderegelung."""
        if (command.actuator, command.kind) == _SETPOINT:
            self.battery.note_verdict(command, verdict, not_sent=not_sent)
        else:
            self.charger.note_verdict(command, verdict, not_sent=not_sent)

    def note_result(self, result: WriteResult) -> None:
        """Das Ergebnis eines abgeschlossenen Schreibvorgangs, auch eines des Keepers: zum Override
        an den Akku-Sollwert, sonst an die Laderegelung."""
        if (result.actuator, result.kind) == _SETPOINT:
            self.battery.note_result(result)
        else:
            self.charger.note_result(result)

    def forget(self, actuators: Collection[str]) -> None:
        """Vergisst die letzten eigenen Werte dieser Aktoren, in beiden Bausteinen."""
        self.charger.forget(actuators)
        self.battery.forget(actuators)

    def idle(self, now: datetime) -> None:
        """Ein Zyklus ohne Regelung für beide Bausteine; ``now`` ist die Zeit der Safety-Schicht."""
        self.charger.idle(now)
        self.battery.idle()

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox aus der Zuordnung der Laderegelung, für die Safety-Schicht."""
        return self.charger.vehicle_wallbox()

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten Zyklus: ``vehicles`` je Fahrzeug (``ChargeController.health``)
        und ``battery`` (``BatteryController.health``)."""
        return {"vehicles": self.charger.health(), "battery": self.battery.health()}

    def alarms(self, wall_now: datetime) -> list[Alarm]:
        """Die Alarme der Regelung zur Wanduhr ``wall_now``; bisher keine."""
        return []

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext``; bisher keins (``None``)."""
        return None


def battery_cars(
    cfg: Config,
    cars: Sequence[CarView],
    wallboxes: Mapping[str, WallboxObs],
    guest: ChargeMode,
) -> list[BatteryCar]:
    """Die Autos für den Akku-Sollwert in der Reihenfolge von Präzisierung 4.

    Erst je Ladepunkt sein Fahrzeug (``cars``, aus ``ChargeController.cars`` in der Reihenfolge
    der Priorität), dann jede Wallbox ohne Fahrzeug in der Reihenfolge von ``config.yaml``: mit
    dem Modus des Gastes (``guest``), der Leistung der Wallbox und ob sie lädt. Eine Wallbox, die
    ``wallboxes`` nicht nennt, ist ganz unbekannt.
    """
    found = [BatteryCar(car.wallbox, car.mode, car.power_w, car.charging) for car in cars]
    taken = {car.wallbox for car in cars}
    mode = Effective(guest.value)
    for key in cfg.wallboxes:
        if key in taken:
            continue
        seen = wallboxes.get(key)
        if seen is None:
            found.append(BatteryCar(key, mode, None, None))
        else:
            found.append(BatteryCar(key, mode, seen.power_w, seen.charging))
    return found
