"""Regelungs-Kern: die einzige Schnittstelle der Laufzeit zur Regelung (Plan 2c-2a, Design 4.1).

- ``RegulationInputs``: die Eingaben eines Zyklus der Regelung
- ``Regulation``: der Kern; er hält die Bausteine der Regelung und verteilt jeden Aufruf an sie
- ``battery_cars``: die Autos für den Akku-Sollwert (Präzisierung 4)
- ``peak_wanted_w``: der gewollte Netzbezug für den 15-min-Wächter (Präzisierung 10)
- ``peak_cars``: die Autos für den 15-min-Wächter (Präzisierung 11)

Die Bausteine sind die Laderegelung (``ChargeController``), der Akku-Sollwert
(``BatteryController``) und der 15-min-Wächter (``PeakGuard``). Ein Zyklus (``step``) rechnet in der
Reihenfolge von Präzisierung 3:

1. die Laderegelung mit den Grenzen und der Start-Sperre, die der Wächter im vorigen Zyklus
   bestimmt hat,
2. die Autos je Wallbox aus der Zuordnung und den Modi dieses Zyklus
   (``ChargeController.cars``, auch ein Gast), dazu die Wallboxen ohne Ladepunkt
   (``battery_cars``),
3. Fehlbetrag und erlaubten Akku-Anteil (``BatteryController.assess``),
4. den Wächter (``PeakGuard.step``) mit dem gewollten Netzbezug, solange Victron handeln kann
   (``peak_wanted_w``); er kennt die feste Grenze von Regel 2: das Budget für diesen Zyklus, die
   Grenzen für den nächsten,
5. den Override (``BatteryController.decide``) mit diesem Budget.

Die Grenzen des Wächters wirken so einen Zyklus später; dafür rechnen Akku-Sollwert und Wächter mit
Zuordnung und Modi desselben Zyklus wie die Laderegelung. Die Befehle kommen in dieser Reihenfolge:
erst die der Ladepunkte, dann der Override. Laderegelung und Akku-Sollwert laufen auf der Uhr der
Safety-Schicht (``ChargeInputs.now``), der Wächter auf der Wanduhr (``ChargeInputs.wall_now``), wie
die Viertelstunden der Abrechnung. Sein Budget gilt nach ``step`` auch für die Safety-Schicht
(``grid_budget_w``, Regel 2), sein Alarm ``peak.exceeded`` bis zum Ende seiner Viertelstunde
(``alarms``).

Urteile und Ergebnisse zum Override (``victron``, ``grid_setpoint``) gehen nur an den
Akku-Sollwert, alle anderen nur an die Laderegelung; ``forget`` erreicht beide, ``idle`` alle drei
Bausteine.

Der Kern fängt keine Ausnahme; das tut die Verdrahtung neben der Laufzeit (``RegulationLink``,
Plan 2c-2a, Präzisierung 15). Die Schicht ist rein: Die Zeit kommt mit den Eingaben. Nicht für
mehrere Threads gedacht.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from bindaems.core.control.battery import BatteryCar, BatteryController, BatteryNeed
from bindaems.core.control.charge_inputs import ChargeInputs, WallboxObs
from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.charging import CarView, ChargeController
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key
from bindaems.core.control.peak_guard import NO_LIMITS, PeakCar, PeakGuard, PeakLimits, QuarterObs
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import VICTRON, ChargeMode, Command, CommandKind, GuestIntent
from bindaems.shared.domain import Alarm
from bindaems.shared.timeutil import slot_end

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
    - ``quarter``: die laufende Viertelstunde für den 15-min-Wächter (``QuarterObs``,
      Präzisierung 9): Energie und Leistung wie die Abrechnung, der Netzanteil je Wallbox mit den
      Wallboxen in Boost zuerst; ``None`` heißt nicht da
    """

    charge: ChargeInputs
    pv_w: float | None = None
    house_w: float | None = None
    base_setpoint_w: float | None = None
    locks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    quarter: QuarterObs | None = None


class Regulation:
    """Der Regelungs-Kern: je Zyklus die Befehle seiner Bausteine.

    ``charger`` ist die Laderegelung, ``battery`` der Akku-Sollwert, ``peak_guard`` der
    15-min-Wächter. ``started`` ist die Wanduhr beim Start der Regelung (``ChargeController``).
    Der Kern hält die Vorgaben des Wächters aus dem letzten Zyklus (``PeakLimits``): ``NO_LIMITS``
    nach dem Start und nach ``idle``. Der Zustand lebt nur im Speicher.
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self.charger = ChargeController(cfg, limits, started=started)
        self.battery = BatteryController(cfg)
        # Der Wächter kennt die feste Grenze des Overrides nach Regel 2, ohne sein Budget.
        ceiling_w = limits.grid_setpoint_max(None)[0]
        self.peak_guard = PeakGuard(cfg.control, cfg.battery.reserve_soc_pct, ceiling_w)
        self._peak: PeakLimits = NO_LIMITS  # die Vorgaben des Wächters aus dem letzten Zyklus

    def step(self, inputs: RegulationInputs) -> list[ControlCommand]:
        """Ein Regelzyklus (Präzisierung 3): die Befehle der Ladepunkte in der Reihenfolge der
        Priorität, dahinter der Override.

        Die Laderegelung bekommt Grenzen und Start-Sperre des 15-min-Wächters aus dem vorigen
        Zyklus; sein Budget aus diesem Zyklus gilt schon für den Override. Der Wächter misst
        ``inputs.quarter`` mit den Autos von ``peak_cars``, dem gewollten Netzbezug aus
        ``peak_wanted_w`` (``W`` vor dem Wächter, ungekappt; unbekannt, solange Victron nicht
        handeln kann), dem Basis-Sollwert und dem SOC; die feste Grenze von Regel 2 bekam er beim
        Bau.

        Victron ist für den Override gesperrt, wenn ``inputs.locks`` für ``victron`` Gründe nennt,
        und steuerbar nach ``charge.controllable``. Ein offener Schreibvorgang für den Override oder
        eine Übergabe von Victron (``charge.blocked``) hält nur seinen Befehl dieses Zyklus zurück.
        """
        charge = inputs.charge
        commands = self.charger.step(charge, self._peak)
        guest = (GuestIntent() if charge.intent is None else charge.intent.guest).mode
        views = self.charger.cars()
        cars = battery_cars(self._cfg, views, charge.wallboxes, guest)
        battery = self.battery
        need = battery.assess(charge.now, charge.grid.soc_pct, inputs.pv_w, inputs.house_w, cars)
        controllable = charge.controllable.get(VICTRON) is True
        locked = bool(inputs.locks.get(VICTRON))
        base_w = inputs.base_setpoint_w
        acting = controllable and not locked
        self._peak = self.peak_guard.step(
            charge.wall_now,
            inputs.quarter,
            peak_cars(views, need),
            wanted_w=peak_wanted_w(need, acting=acting),
            base_w=base_w,
            soc_pct=charge.grid.soc_pct,
        )
        override = battery.decide(
            charge.now,
            need,
            base_w=base_w,
            budget_w=self._peak.budget_w,
            present=charge.present,
            controllable=controllable,
            locked=locked,
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
        """Ein Zyklus ohne Regelung für alle drei Bausteine.

        ``now`` ist die Zeit der Safety-Schicht. Für den Wächter ist die laufende Viertelstunde
        danach lückenhaft, und seine Vorgaben verfallen (``NO_LIMITS``): Solange die Regelung ruht,
        gibt es kein Budget.
        """
        self.charger.idle(now)
        self.battery.idle()
        self.peak_guard.idle()
        self._peak = NO_LIMITS

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox aus der Zuordnung der Laderegelung, für die Safety-Schicht."""
        return self.charger.vehicle_wallbox()

    def boost_wallboxes(self) -> frozenset[str]:
        """Die Wallboxen der Autos in Boost im letzten ``step`` (``ChargeController.cars``), für
        den Boost-Anteil der Viertelstunde des Wächters (Präzisierung 9); vor dem ersten ``step``
        und nach ``idle`` keine. Eine Wallbox ohne Fahrzeug ist nie in Boost: Der Modus des Gastes
        kennt ihn nicht."""
        return frozenset(car.wallbox for car in self.charger.cars() if car.mode is Effective.BOOST)

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten Zyklus: ``vehicles`` je Fahrzeug (``ChargeController.health``),
        ``battery`` (``BatteryController.health``), ``peak`` (``PeakGuard.health``) und
        ``wallboxes`` je Wallbox (``ChargeController.wallbox_health``, Plan 2c-2b)."""
        return {
            "vehicles": self.charger.health(),
            "battery": self.battery.health(),
            "peak": self.peak_guard.health(),
            "wallboxes": self.charger.wallbox_health(),
        }

    def alarms(self, wall_now: datetime) -> list[Alarm]:
        """Die Alarme der Regelung zur Wanduhr ``wall_now``: die des Wächters (``peak.exceeded``),
        solange ``wall_now`` in der Viertelstunde ihres ``since`` liegt.

        Der Wächter kennt die Zeit nur aus seinen Schritten. Ruht die Regelung, etwa in OBSERVE
        oder bei ausgeschaltetem Regelbetrieb, hielte er den Alarm über das Ende seiner
        Viertelstunde hinaus; ``RegulationLink.finish`` fragt deshalb in jedem Zyklus.
        """
        return [alarm for alarm in self.peak_guard.alarms() if wall_now < slot_end(alarm.since)]

    def grid_budget_w(self) -> float | None:
        """Das 15-min-Budget für den ``SafetyContext`` (Regel 2): ``budget_w`` des Wächters aus dem
        letzten ``step`` (Präzisierung 12); ``None`` ohne Budget und solange die Regelung ruht."""
        return self._peak.budget_w


def battery_cars(
    cfg: Config,
    cars: Sequence[CarView],
    wallboxes: Mapping[str, WallboxObs],
    guest: ChargeMode,
) -> list[BatteryCar]:
    """Die Autos für den Akku-Sollwert in der Reihenfolge von Präzisierung 4.

    Erst je Ladepunkt sein Auto (``cars``, aus ``ChargeController.cars`` in der Reihenfolge der
    Priorität; ein Gast dort mit dem Modus des Gastes), dann jede Wallbox ohne Ladepunkt in der
    Reihenfolge von ``config.yaml``: mit dem Modus des Gastes (``guest``), der Leistung der Wallbox
    und ob sie lädt. Eine Wallbox, die ``wallboxes`` nicht nennt, ist ganz unbekannt.
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


def peak_wanted_w(need: BatteryNeed | None, *, acting: bool) -> float | None:
    """Der gewollte Netzbezug ``W`` für den 15-min-Wächter in W (Präzisierung 10); ``None`` heißt
    unbekannt, dann zählt für die verlangte Senkung nur das Mittel.

    - **Victron kann nicht handeln** (``acting`` falsch: gesperrt oder nicht steuerbar): ``None``.
      Kein Override setzt dann ``Basis + W`` durch.
    - **Ohne Bedarf** (``need`` ist ``None``): ``None``.
    - **Sonst** ``W`` vor dem Wächter (``BatteryNeed.wanted_w``), ungekappt. Was davon über der
      festen Grenze ``C`` von Regel 2 zählt, entscheidet der Wächter: mit Budget für den Akku
      nichts, denn darüber liefert der Akku (Design 2c-2, Entscheidung 3 und 5.4). Ohne Budget
      hält Regel 2 den Override bei ``C``: Liegt ``P_zul`` darunter, zählt alles, denn nur die
      ganze Senkung bringt den Netzbezug unter ``P_zul``; sonst ebenfalls nichts.
    """
    if not acting or need is None:
        return None
    return need.wanted_w


def peak_cars(cars: Sequence[CarView], need: BatteryNeed | None) -> list[PeakCar]:
    """Die Autos für den 15-min-Wächter aus ``cars``, in dieser Reihenfolge: jedes, dessen
    Ladepunkt die Laderegelung regelt (``CarView.regulated``), und jedes in Boost.

    ``cars`` kommt aus ``ChargeController.cars``, in der Reihenfolge der Priorität; der Rang ist
    die Stelle darin, 0 die höchste, auch wenn ein Auto davor fehlt. Der Netzanteil ist der von
    ``need`` für die Wallbox (``BatteryNeed.grid_w``, Präzisierung 11), ohne Bedarf 0 W.

    - **Ungeregelt, nicht in Boost:** Das Auto fehlt. Eine Grenze wirkte an seinem Ladepunkt nicht,
      und was es ihr nicht folgte, stünde bis zum Ende der Viertelstunde aus (``pending_w``) und
      schmälerte die Drosselung der anderen. Weil es fehlt, verliert es beim Wächter still auch
      eine Grenze aus der Zeit, als sein Ladepunkt noch geregelt war; Drosselung, ``pending_w`` und
      Start-Sperre übergehen es. Sein Netzanteil steht weiter im gewollten Netzbezug ``W``.
    - **In Boost:** Das Auto zählt immer, geregelt oder nicht: Seinen Netzanteil zieht der Wächter
      als Energie in Boost ab. Gedrosselt wird es nie.

    Ein Gast zählt wie jedes Auto; an einer EVCS kann der Wächter ihn drosseln (Plan 2c-2b,
    Präzisierung 6). Wallboxen ohne Ladepunkt sind keine Autos des Wächters: Er kann sie nicht
    drosseln, ihr Netzanteil steht aber in ``W``.
    """
    grid_w: Mapping[str, float] = {} if need is None else need.grid_w
    return [
        PeakCar(
            vehicle=car.vehicle,
            wallbox=car.wallbox,
            mode=car.mode,
            rank=rank,
            power_w=car.power_w,
            grid_w=grid_w.get(car.wallbox, 0.0),
            min_w=car.min_w,
        )
        for rank, car in enumerate(cars)
        if car.regulated or car.mode is Effective.BOOST
    ]
