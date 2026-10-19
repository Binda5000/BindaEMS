"""Laderegelung: der ``ChargeController`` verbindet die Bausteine je Zyklus (Design 4 bis 9).

Ein Zyklus (``step``) geht so (Präzisierungen 4 bis 12):

1. Zuordnung (``Assignment``) und der geltende Modus je Auto: für ein Fahrzeug bei aktivem
   ``boost_until`` Boost, für einen Gast ``intent.guest.mode``, nie Boost. Die Reihenfolge der
   Fahrzeuge ist ``intent.priority``, wenn sie jedes Fahrzeug der Konfiguration genau einmal nennt,
   sonst die von ``config.yaml``; Einträge für unbekannte Fahrzeuge zählen nicht. Dahinter folgen
   die Gäste in der Reihenfolge der Wallboxen (``fleet``).
2. Ladepunkte (``ChargePoint``): Aktor, Phasen, Strombereich, gemessener Strom und ob er geregelt
   wird (Präzisierungen 5 und 6). Daraus die Last je Phase samt Zuschlägen (``phase_loads``) und
   die Stufe der Überlast am echten Netzstrom (``OverloadGuard``).
3. Überschuss (``surplus_w``) und seine Verteilung (``share_surplus``), dann die Zuteilung des
   Wächters (``allocate``), beides in der Reihenfolge der Priorität.
4. Je geregeltem Ladepunkt der Modus (``ModeMachine``), Überlast, Strom und Befehle. Die Maschine
   eines Ladepunkts, der nicht geregelt wird, wartet; wird er wieder geregelt (auch im ersten
   Zyklus nach dem Start oder nach ``idle``), bekommt sie einmal ``resumed`` und übernimmt den
   beobachteten Zustand (Präzisierung 9). Begann in der Zwischenzeit ein Ansteckvorgang, bekommt
   sie stattdessen ``new_session``: PV und Aus beginnen dann ohne Laden, auch wenn das Auto selbst
   gestartet hat. So beim Tesla, den Tessie erst nach dem Wall Connector als angesteckt meldet.
   Der gemerkte Vorgang erreicht die Maschine im ersten Schritt mit bekanntem Ansteckzustand.

Die Bausteine stehen in eigenen Modulen: ``charge_points`` (Ladepunkte und ihre Verteilung),
``last_values`` (nur Änderungen schicken, „nicht übernommen“) und ``charging_tesla`` (Tesla,
Zuordnung und Wiederaufnahme).

Die Autos sind die Fahrzeuge der Konfiguration und je Wallbox ein Gast (``fleet``, Plan 2c-2b,
Präzisierung 6). Ein Gast ist ein Auto ohne Tessie, das auf allen Phasen lädt (``car_spec``): An
einer EVCS wird er wie jedes Auto geregelt, mit ihrem Strombereich; am Wall Connector ist er Last.

Nach jedem ``step`` zeigt ``cars`` je Ladepunkt das Auto mit Modus und Leistung (``CarView``): Damit
rechnen Akku-Sollwert und 15-min-Wächter mit Zuordnung und Modi desselben Zyklus (Plan 2c-2a,
Präzisierungen 3 und 4). Das Health nennt je Fahrzeug der Konfiguration seinen Stand (``health``)
und je Wallbox, wer dort steht, mit Art und Indizien (``wallbox_health``, Plan 2c-2b,
Präzisierung 11).

Dazu legt die Regelung fest:

- Am Überschuss nimmt ein geregelter Ladepunkt nur teil, wenn sein Auto laden kann: nicht
  abgesteckt, nicht „geladen“ und nicht in Aus. Nur die Leistung dieser Wallboxen zählt zu
  ``cars_w``, und nur diese Autos wünschen Strom. Sonst nähme ein abgestecktes Auto in Sofort den
  anderen ``max_a × U × Phasen`` weg, und die Leistung eines ungeregelt ladenden Autos gälte als
  Überschuss; sein Strom ist aber Last (Design 9).
- Überlast startet und weckt nichts: Ab ``MINIMUM`` gibt es kein „Laden an“, nur den
  Mindeststrom. Ein Auto, das lädt, obwohl sein Modus aus will (etwa selbst gestartet), stoppt
  sie dringend, außer solange die Zuordnung unklar ist.
- Ein Start („Laden an“, auch der Weckstart) folgt nur, wenn die Grenze des Wächters den
  Mindeststrom des Autos trägt (Design 5.3), in Min+PV die Grenze an der Sicherung, denn dort geht
  der Mindeststrom dem Importlimit vor (Design 6.3). Ist die Grenze unbekannt, unterbleibt er
  (Design 5.5). Trägt sie ihn wieder, startet das Auto nach den Regeln seines Modus. Ein Auto, das
  schon lädt, betrifft das nicht: Es geht auf den Mindeststrom.
- Grenzen und Start-Sperre des 15-min-Wächters (``PeakLimits`` aus dem vorigen Zyklus, Plan 2c-2a,
  Präzisierungen 3, 11 und 13) gelten für jedes Auto außer in Boost. Die Grenze eines Fahrzeugs in
  W wird zu ``W / (U · Phasen des Ladepunkts)`` A. Liegt sie unter dem freien Strom des Wächters,
  gilt sie an dessen Stelle mit dem Grund ``peak_guard``, schon für den Überschuss (``enough``) und
  das Fenster des Tesla: Ein Auto in PV stoppt dann nach den Regeln seines Modus, Min+PV und
  Sofort halten den Mindeststrom. Ein unbekannter freier Strom bleibt unbekannt. Eine Senkung wegen
  der Grenze ist nie dringend; was die Sicherung allein verlangt, bleibt es. Ein Fahrzeug in
  ``no_start`` startet nicht, wie bei einer Grenze, die den Mindeststrom nicht trägt; ein Auto, das
  lädt, betrifft das nicht. Als ``limit_a`` nennen Health und Entscheidungslog weiter den freien
  Strom des Phasenwächters.
- Ein Start wartet, solange sein Strom gesendet werden muss, aber blockiert ist.
- Der Stopp im Modus „Aus“ ist dringend (Plan 2d, Präzisierung 9): Die Safety-Schicht lässt ihn ohne
  Mindestabstand und Stundenlimits durch.
- Reserve im Stundenbudget des Tesla (Plan 2d, Präzisierung 9): Hat der Tesla am Wall Connector
  ``per_hour − control.tesla_reserve_commands`` Befehle in der Stunde, geht ein Strombefehl nur
  noch hinaus, wenn er den Strom senkt und der Ist-Strom über der Grenze des Wächters liegt, oder
  wenn er dringend ist. Ist der Ist-Strom unbekannt, hält die Reserve nichts zurück. Starts samt
  ihrem Strom, Stopps und der Höchststrom an der EVCS sind davon frei. Ein zurückgehaltener
  Strombefehl erscheint nicht im Log.
- Selbststart (Plan 2d, Präzisierung 10): Lädt ein Fahrzeug mit Tessie laut Tessie (mit mindestens
  1 A) an einem geregelten Ladepunkt, obwohl sein Modus nicht laden will, und ist die Zuordnung
  klar, vergisst die Regelung nach ``control.tesla_against_s`` ohne Unterbrechung ihren letzten Wert
  für ``charging`` des Aktors: „Laden aus“ geht erneut hinaus, mit dem Grund ``self_start`` nach dem
  Modus, höchstens alle ``control.tesla_against_retry_s`` je Fahrzeug. Das gilt nur für Zyklen, in
  denen der Stopp hinausgehen kann (Aktor steuerbar, ``charging`` nicht blockiert); sonst läuft die
  Zeit weiter. Dringend ist es nur im Modus „Aus“ wie jeder Stopp dort. Ein neuer Vorgang und
  ``idle`` beginnen die Zeiten neu.
- Ist der Ansteckzustand unbekannt, sinkt der Strom nur unter den Ist-Strom, wenn der Wächter es
  verlangt; „Laden aus“ kommt dann nur wegen Überlast.
- Eine Wallbox ist höchstens ein Ladepunkt: Hängen nach der Zuordnung zwei Fahrzeuge an ihr, hat
  ihn das erste nach Priorität. So zählt ihr Strom nur einmal.
- Ein Fahrzeug mit Tessie, das laut Übersteuerung oder Erkennung an einer EVCS steht, bekommt mit
  jeder Ankunft dort einmal seinen Höchststrom; geregelt wird dann die EVCS mit seinen Phasen und
  Grenzen in seinem Modus (``EvcsOverrides``; Plan 2c-2b, Präzisierung 7).
- Ab einer neuen Erkennung (``VehicleState.fresh``) tragen die Befehle eines Autos zuletzt den
  Grund ``assignment_detected``, bis zum ersten Zyklus mit Befehlen für das Auto, den Höchststrom
  eingeschlossen. Ist es vorher nicht mehr erkannt, entfällt er (Plan 2c-2b, Präzisierung 8).
- „Geplant“ (Plan 4b, Präzisierung 7): Je Wallbox zählt die Regelung die Ansteckvorgänge
  (``_Plug``), je Fahrzeug führt sie den Ladestand (``SocTracker``) und den Stand des geplanten
  Ladens (``PlannedTracker``) an der Wallbox, an der es steht. Mit festem Bedarf (``plan``: die
  Leistung des gültigen Plan-Slots für das Auto, ``ActiveSlot.vehicles``; ``due``: die volle
  Leistung ab der spätesten Startzeit, wenn kein gültiger Slot gilt) gilt das Auto
  wie Sofort mit dem Ziel des Phasenwächters an der Sicherung, sein Bedarf geht vor den PV-Autos
  vom Überschuss ab, und Überschuss darüber kommt obendrauf; ohne festen Bedarf wie PV, auch mit
  einem gültigen Slot, der für das Auto 0 W nennt. Nennt der gültige Slot das Auto nicht (etwa
  nach dem Plan angesteckt), gilt für das Auto kein Slot (Ruling R22). Am Ziel
  (``reached``) stoppt es wie „geladen“ und lädt auch keinen Überschuss (``target_reached``).
  Boost geht vor.
- Ladelimit (Plan 4b, Präzisierung 9; ``ChargeLimits``): Mit ``charge_limit_sync`` bekommt ein
  Tesla in „Geplant“ an einem geregelten Ladepunkt einmal je (Ansteckvorgang, Ziel) das Ladelimit
  ``max(Ziel, 50)``, vor seinen anderen Befehlen, mit dem Grund ``charge_limit``. Er geht nur an
  ein steuerbares (waches) Auto, nie mit Weckerlaubnis, wartet bei blockierter Art und hält wie
  ein Strombefehl die Reserve des Stundenbudgets frei.
- An einer EVCS hält die Verteilung den zuletzt angeforderten Strom (Hysterese
  ``control.evcs_hysteresis_a``, Design 6.5): Er sinkt sofort mit dem Anteil, steigt aber erst,
  wenn der Anteil ihn um 1 A plus die Hysterese übersteigt. Ohne eigenen Wert (nach dem Start, nach
  ``idle`` und für die Aktoren von ``forget``) wird nur abgerundet.

Die Schicht ist rein: Die Zeit kommt mit den Eingaben, auf der Uhr der Safety-Schicht
(``ChargeInputs.now``); nur ``boost_until`` und der Kalendertag des Weckkontingents gelten nach
der Wanduhr. Nicht für mehrere Threads gedacht.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

from bindaems.core.control.assignment import Assignment, AssignmentKind, VehicleState, WallboxState
from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, ChargeInputs, VehicleObs
from bindaems.core.control.charge_modes import (
    FULL_POWER,
    LATEST_START,
    MODE_CODES,
    PLAN_POWER,
    PV_SURPLUS,
    TARGET_REACHED,
    Effective,
    ModeDecision,
    ModeInput,
    ModeMachine,
    ModeTimers,
    effective_of,
)
from bindaems.core.control.charge_points import (
    ChargePoint,
    can_charge,
    cars_w,
    charge_points,
    enough,
    holding_a,
    reserved_a,
    wanted_a,
)
from bindaems.core.control.charging_tesla import (
    ASSIGNMENT_DETECTED,
    ASSIGNMENT_UNCLEAR,
    SELF_START,
    WAKE,
    AgainstMode,
    ChargeLimits,
    EvcsOverrides,
    TeslaPolicy,
    maximum_command,
    wake_command,
)
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.fleet import GUEST, car_spec, fleet, guest_wallbox
from bindaems.core.control.last_values import ControlCommand, Key, LastValues
from bindaems.core.control.peak_guard import NO_LIMITS, PeakLimits
from bindaems.core.control.phase_guard import (
    Claim,
    Grant,
    GuardCause,
    GuardLimits,
    OverloadGuard,
    OverloadLevel,
    allocate,
    phase_loads,
    phase_target,
)
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.core.control.planned import PlannedState, PlannedTracker, full_power_w
from bindaems.core.control.surplus import SurplusCar, share_surplus, surplus_w
from bindaems.core.control.vehicle_soc import SocReading, SocTracker
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config
from bindaems.shared.control import (
    Command,
    CommandKind,
    CommandValue,
    GuestIntent,
    Intent,
    SurplusPriority,
    VehicleIntent,
    vehicle_actuator,
)
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import ensure_utc

OVERLOAD: Final = "overload"  # der Befehl folgt aus der Überlast
RESUME_AFTER_OUTAGE: Final = "resume_after_outage"  # die erste Startfolge nach einer Pause

ChargeCommand = ControlCommand  # der Name der Laderegelung für ihren Befehl

_CURRENT: Final = CommandKind.CURRENT
_CHARGING: Final = CommandKind.CHARGING
_LIMIT: Final = CommandKind.CHARGE_LIMIT
# Wunsch aus dem Anteil; „Geplant“ ohne festen Bedarf
_SURPLUS_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV, Effective.PLANNED})
_PV_LIKE: Final = frozenset({Effective.PV, Effective.PLANNED})  # ladend halten sie ``min_a``
_NOBODY: Final = WallboxState(None, None, ())  # an der Wallbox läuft kein Vorgang
_SELF_START_MIN_A: Final = 1.0  # ab diesem Strom in A lädt ein Fahrzeug laut Tessie
# Die Modi, deren offener Wunsch der Klimaanlage vorgeht (Plan 6a, Entscheidung 5.3)
_UNMET_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})
# Anlauf (Ruling R23 a): So lange nach einem eigenen Start oder einer Erhöhung des Stroms gilt ein
# Auto als seinem Befehl folgend, auch wenn es noch nicht zieht oder sein Aktor noch „aus“ meldet.
# Das ist mindestens die Frist der Bestätigung des langsamsten Ladepunkts: beim Tesla 85 s
# (``confirm_timeout_s`` in ``core.actuators.tesla``), an der EVCS 10 s. Tessie meldet die Leistung
# beim Laden ohnehin nur alle 30 s.
RAMP_UP_S: Final = 90.0
# Hysterese (Ruling R23 b): Das Urteil je Auto „folgt seinem Befehl“ oder „begrenzt sich selbst“
# wechselt erst, wenn das Gegenteil so lange ohne Unterbrechung gilt.
FOLLOW_HOLD_S: Final = 120.0


@dataclass(frozen=True)
class CarView:
    """Das Auto eines Ladepunkts im letzten ``step`` (Plan 2c-2a, Präzisierung 4); ``None`` heißt
    unbekannt.

    - ``vehicle``, ``wallbox``: das Auto (ein Schlüssel aus ``fleet``, auch ein Gast) und die
      Wallbox des Ladepunkts aus der Zuordnung
    - ``mode``: der geltende Modus des Autos, mit Boost; für einen Gast ``intent.guest.mode``
    - ``power_w``: die Ladeleistung in W, die die Wallbox misst. Fehlt sie oder ist sie keine
      endliche Zahl, bei einem Fahrzeug mit Tessie ``actual_current_a × phases × U``, wenn Tessie
      erreichbar ist und beides kennt.
    - ``charging``: ob das Fahrzeug lädt, aus der Zuordnung
    - ``min_w``: die Mindestleistung des Ladepunkts, ``min_a × U × Zahl seiner Phasen``
    - ``regulated``: ob die Laderegelung den Ladepunkt regelt (``ChargePoint.regulated``); nur
      dann wirkt eine Grenze des 15-min-Wächters

    ``U`` ist ``grid.voltage_nominal_v``.
    """

    vehicle: str
    wallbox: str
    mode: Effective
    power_w: float | None
    charging: bool | None
    min_w: float
    regulated: bool


@dataclass(frozen=True)
class _View:
    """Was ``health`` zu einem Fahrzeug aus dem letzten Zyklus zeigt.

    ``soc``, ``limit_pct`` und ``planned`` (Plan 4b, Präzisierung 7) nur für ein Fahrzeug der
    Konfiguration: der Ladestand, das Ladelimit im Auto und der Stand von „Geplant“ (nur in
    diesem Modus und an einer Wallbox).
    """

    mode: Effective
    state: VehicleState
    limit_a: float | None
    soc: SocReading | None = None
    limit_pct: float | None = None
    planned: PlannedState | None = None


@dataclass(frozen=True)
class _Plug:
    """Ein laufender Ansteckvorgang an einer Wallbox (Plan 4b, Präzisierungen 5 und 6).

    - ``number``: seine Nummer; eindeutig über alle Wallboxen und nie wiederverwendet
    - ``start``: sein Beginn auf der Wanduhr; begann er, ohne dass die Regelung „kein Fahrzeug“
      davor sah (etwa beim Start des core), der Zyklus, in dem sie ihn zuerst sah

    Abstecken und neu Anstecken während ``idle`` bleibt unbemerkt (die Regelung sieht die Wallbox
    dann nicht): Es zählt als derselbe Vorgang, wie bei ``assignment._Port``.
    """

    number: int
    start: datetime


@dataclass
class _Follow:
    """Das Urteil über ein Auto für ``unmet_surplus_w`` mit Hysterese (Ruling R23 b).

    - ``follows``: Das Auto folgt seinem Befehl; sonst begrenzt es sich selbst.
    - ``limit_a``: der befohlene Strom, bei dem es sich selbst begrenzte; ``None``, solange es folgt
    - ``since``: seit wann das Gegenteil ohne Unterbrechung gilt; ``None``, solange es nicht gilt

    ``judge`` nimmt die Beobachtung eines Zyklus und gibt das geltende Urteil: Es wechselt erst,
    wenn das Gegenteil ``FOLLOW_HOLD_S`` lang gilt.
    """

    follows: bool
    limit_a: float | None = None
    since: datetime | None = None

    def reference_a(self, commanded_a: float) -> float:
        """Der Strom, dem das Auto folgen muss: Begrenzt es sich selbst, folgt es erst wieder,
        wenn es (bis auf eine Stufe) den Strom zieht, bei dem es sich begrenzte. Senkt die Regelung
        den Strom, weil die Klimaanlage den Überschuss nimmt, folgt es sonst scheinbar wieder, und
        beide schwingen gegeneinander. „Laden aus“ (0 A) gilt für sich."""
        if commanded_a <= 0.0 or self.limit_a is None:
            return commanded_a
        return max(commanded_a, self.limit_a)

    def judge(self, follows: bool, now: datetime, commanded_a: float | None) -> bool:
        if follows == self.follows:
            self.since = None
        elif self.since is None:
            self.since = now
        elif (now - self.since).total_seconds() >= FOLLOW_HOLD_S:
            self.follows, self.since = follows, None
            self.limit_a = None if follows else commanded_a
        return self.follows


@dataclass(frozen=True)
class _Planned:
    """„Geplant“ eines Fahrzeugs in einem Zyklus: sein Stand und sein fester Bedarf in W.

    Der feste Bedarf ist die Leistung des Plan-Slots (``plan``) oder die volle Leistung (``due``),
    sonst 0 (Plan 4b, Präzisierung 7).
    """

    state: PlannedState
    reserved_w: float

    @property
    def code(self) -> str | None:
        """Der Grund des festen Bedarfs: ``plan_power`` oder ``latest_start``; ohne ``None``."""
        if self.state.state == "plan":
            return PLAN_POWER
        return LATEST_START if self.state.state == "due" else None

    @property
    def reached(self) -> bool:
        """Ob das Ziel erreicht ist; das Auto lädt dann nicht, auch keinen Überschuss."""
        return self.state.state == "reached"


@dataclass(frozen=True)
class _Cycle:
    """Was ein Zyklus für alle Fahrzeuge gemeinsam rechnet (Phasenwächter und Überschuss).

    Je Fahrzeug: ``shares`` der Anteil am Überschuss (nur wer am Überschuss teilnimmt),
    ``wanted`` der Wunsch in A (nur wer laden kann), ``targets`` Ziel und Grund
    (``phase_target``) und ``grants`` die Zuteilung des Wächters (nur geregelte Ladepunkte).
    """

    surplus: float | None
    shares: Mapping[str, int | None]
    wanted: Mapping[str, int]
    targets: Mapping[str, tuple[float, GuardCause]]
    grants: Mapping[str, Grant]
    level: OverloadLevel


@dataclass(frozen=True)
class _Situation:
    """Was ``_regulate`` über ein Fahrzeug im Zyklus wissen muss.

    - ``plugged``, ``charging``: angesteckt und lädt laut Zuordnung; ``None`` unbekannt
    - ``mode`` und ``decision``: der geltende Modus und das Urteil seiner ``ModeMachine``
    - ``cause``: woran sich die Grenze richtet: das Ziel des Wächters (``phase_target``) oder
      ``PEAK_GUARD``, wenn die Grenze des 15-min-Wächters kappt
    - ``fuse_a``: Kappt die Grenze des 15-min-Wächters und ist die Sicherung das Ziel, der freie
      Strom des Wächters abgerundet; sonst ``None``. Was die Sicherung allein verlangt, bleibt
      dringend (``_urgent``).
    - ``wanted``: der Wunsch in A, beim Tesla am Wall Connector das abgerundete Mittel
    - ``limit``: die Grenze in A, der freie Strom nach der Grenze des 15-min-Wächters, beim Tesla am
      Wall Connector der niedrigste Wert davon im Fenster; ``share``: der Anteil am Überschuss
      (``None`` ohne)
    - ``level``: die Überlast; ``unclear``: die Zuordnung ist unklar
    - ``resume``: die erste Startfolge nach einer Pause steht noch aus
    - ``self_start``: Das Fahrzeug lädt seit ``control.tesla_against_s`` gegen seinen Modus, und
      ein Neuversuch ist fällig; der Stopp trägt dann den Grund ``self_start``
    - ``firm``: in „Geplant“ der Grund des festen Bedarfs (``plan_power``, ``latest_start``),
      sonst ``None``; ``reached``: in „Geplant“ ist das Ziel erreicht, ein Stopp trägt dann
      ``target_reached`` (Plan 4b, Präzisierung 7)
    - ``carries``: Die Grenze ist bekannt und trägt den Mindeststrom, in Min+PV an der Sicherung,
      und das Fahrzeug steht nicht in der Start-Sperre des 15-min-Wächters; nur dann darf ein Auto,
      das nicht lädt, starten (Design 5.3, 6.4).
    """

    plugged: bool | None
    charging: bool | None
    mode: Effective
    decision: ModeDecision
    cause: GuardCause
    fuse_a: int | None
    wanted: int
    limit: int
    share: int | None
    level: OverloadLevel
    unclear: bool
    resume: bool
    carries: bool
    self_start: bool
    firm: str | None = None
    reached: bool = False

    @property
    def head(self) -> tuple[str, ...]:
        """Die ersten Gründe: der Modus, bei unklarer Zuordnung danach ``assignment_unclear``."""
        code = MODE_CODES[self.mode]
        return (code, ASSIGNMENT_UNCLEAR) if self.unclear else (code,)


class ChargeController:
    """Die Laderegelung: je Zyklus Befehle an EVCS und Tesla (Design 4 bis 9).

    Der Zustand lebt nur im Speicher: eine Zuordnung für die ganze Lebensdauer, je Auto aus
    ``fleet`` (Fahrzeuge und Gäste) eine ``ModeMachine``, je Fahrzeug der Konfiguration eine
    ``TeslaPolicy`` (Fenster und Weckkontingent), der Überlastwächter samt der Stufe des letzten
    Zyklus, die geregelten Ladepunkte des letzten Zyklus, die letzten eigenen Werte je Aktor und
    Art, die Autos, deren erste Startfolge nach einer Pause aussteht, die neu erkannten Autos ohne
    Befehle seither (``assignment_detected``), die Autos des letzten Zyklus (``cars``) und wer an
    jeder Wallbox stand (``wallbox_health``). ``started`` ist die Wanduhr beim Start der Regelung
    (``Assignment``).
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self._limits = limits
        self._guard = GuardLimits.from_config(cfg)
        self._assignment = Assignment(cfg, started=started)
        self._fleet = fleet(cfg)  # die Autos: die Fahrzeuge, dahinter je Wallbox ihr Gast
        self._guests = [key for key in self._fleet if key not in cfg.vehicles]
        timers = ModeTimers.from_config(cfg.control)
        self._machines = {key: ModeMachine(timers) for key in self._fleet}
        self._tesla = {key: TeslaPolicy(cfg.control, v.wake) for key, v in cfg.vehicles.items()}
        # Selbststart: je Fahrzeug mit Tessie, wie lange es gegen seinen Modus lädt
        self._against = AgainstMode(cfg.control.tesla_against_s, cfg.control.tesla_against_retry_s)
        self._vehicles = {vehicle_actuator(key): key for key in cfg.vehicles}  # Aktor → Fahrzeug
        self._overload = OverloadGuard(self._guard)
        self._level = OverloadLevel.NONE  # die Stufe der Überlast im letzten Zyklus
        # Fahrzeug und Aktor je Ladepunkt, der im letzten Zyklus geregelt wurde; leer nach dem
        # Start und nach ``idle``: Dann übernimmt jeder geregelte Ladepunkt (``resumed``).
        self._regulated: set[tuple[str, str]] = set()
        # Fahrzeuge, deren Ansteckvorgang begann, als ihr Ladepunkt nicht geregelt wurde: Ihre
        # Maschine bekommt ihn im ersten geregelten Schritt (``new_session`` statt ``resumed``).
        self._unseen_sessions: set[str] = set()
        self._overrides = EvcsOverrides(cfg)  # „Tesla an der EVCS“
        self._resume: set[str] = set()  # Autos, deren erste Startfolge nach ``idle`` aussteht
        # Neu erkannte Autos ohne Befehle seither: Ihre nächsten tragen ``assignment_detected``.
        self._fresh: set[str] = set()
        self._stepped = False  # seit dem Start schon geregelt: erst danach ist ``idle`` ein Ausfall
        self._last = LastValues()
        self._views: dict[str, _View] = {}
        self._cars: tuple[CarView, ...] = ()  # je Ladepunkt des letzten ``step`` (``cars``)
        self._unmet_w = 0.0  # der offene Wunsch der Autos in PV und Min+PV (``unmet_surplus_w``)
        # je Auto bei „Laden aus“ (eigener Wert): das Auto, Startleistung, offener Wunsch und eine
        # Stufe von 1 A auf seinen Phasen (die Hysterese), in W
        self._held: tuple[tuple[str, float, float, float], ...] = ()
        # die Autos bei „Laden aus“, die beim letzten ``unmet_surplus_w`` zählten (Ruling R28)
        self._spare_met: frozenset[str] = frozenset()
        self._ramp: dict[str, datetime] = {}  # je Aktor der letzte eigene Start oder Anstieg
        self._follow: dict[str, _Follow] = {}  # je Auto das Urteil „folgt seinem Befehl“
        # wer an welcher Wallbox stand, im letzten ``step`` (``wallbox_health``)
        self._wallboxes: Mapping[str, WallboxState] = {}
        self._reasons: dict[str, str] = {}  # der letzte Grund des letzten Befehls je Fahrzeug
        self._vehicle_wallbox: dict[str, str | None] = {}
        # Die Uhren des letzten ``step``: für das Weckkontingent in ``note_verdict`` und ``health``
        self._now = self._wall_now = started
        # die Befehle der letzten Stunde im letzten ``step``
        self._commands_hour: Mapping[str, int] = {}
        # die Version der Absicht im letzten ``step``; ``None`` ohne Absicht und nach ``idle``
        self._intent_version: int | None = None
        # „Geplant“ (Plan 4b): je Wallbox ihr laufender Ansteckvorgang, der Ladestand je Fahrzeug
        # und die Merker „späteste Startzeit erreicht“ und „Ziel erreicht“ je Fahrzeug
        self._plugs: dict[str, _Plug] = {}
        self._plug_numbers = itertools.count(1)
        self._soc = SocTracker()
        self._planned = PlannedTracker()
        self._charge_limits = ChargeLimits()  # das Ladelimit des Tesla je Vorgang und Ziel

    @property
    def intent_version(self) -> int | None:
        """Die Version der Absicht, mit der der letzte ``step`` regelte (Plan 2d).

        ``None`` ohne Absicht, vor dem ersten ``step`` und nach ``idle``: Dann ist der Stand der
        Regelung unbekannt.
        """
        return self._intent_version

    def step(
        self,
        inputs: ChargeInputs,
        peak: PeakLimits = NO_LIMITS,
        active: ActiveSlot | None = None,
    ) -> list[ChargeCommand]:
        """Ein Regelzyklus; die Befehle kommen in der Reihenfolge der Priorität.

        Befehle bekommt nur ein geregelter Ladepunkt, und nur für Arten, die nicht in
        ``inputs.blocked`` stehen; je Ladepunkt kommt der Strom vor „Laden an“. Ein Aktor, der
        nicht steuerbar ist, bekommt höchstens den Weckstart; ein Fahrzeug an der EVCS bekommt
        seinen Höchststrom vor den Befehlen an die EVCS. ``peak`` sind Grenzen und Start-Sperre des
        15-min-Wächters (Plan 2c-2a, Präzisierungen 3, 11 und 13); ohne sie gilt keine. ``active``
        ist der gültige Slot des Plans zur Wanduhr (``plan_exec.active_slot``): Seine Ladeleistung
        je Fahrzeug führt „Geplant“ (Plan 4b, Präzisierung 7); ``None`` heißt kein gültiger Slot.
        Ablauf und Regeln stehen im Docstring des Moduls.
        """
        now, intent = inputs.now, inputs.intent
        self._now, self._wall_now = now, inputs.wall_now
        self._commands_hour = dict(inputs.commands_hour)
        self._stepped = True
        state = self._assignment.update(
            now,
            inputs.wall_now,
            intent,
            inputs.accepted_at,
            inputs.wallboxes,
            inputs.vehicles,
            inputs.homes,
        )
        order = self._order(intent)
        modes = {key: _mode(key, state.vehicles[key], intent) for key in order}
        self._follow_plugs(inputs)
        socs = {key: self._soc_of(key, state.vehicles[key], inputs) for key in self._cfg.vehicles}
        planned: dict[str, _Planned] = {}
        for key, soc in socs.items():
            plan = None
            if modes[key] is Effective.PLANNED:
                plan = self._planned_of(key, state.vehicles[key], soc, inputs, active)
            if plan is not None:
                planned[key] = plan
        points = charge_points(self._cfg, self._limits, order, state.vehicles, inputs)
        cycle = self._allocate(order, modes, points, state.vehicles, inputs, planned)
        regulated = {
            key: point.actuator
            for key, point in points.items()
            if point.regulated and point.actuator is not None
        }
        if cycle.level > self._level:  # jede höhere Stufe bietet ihre Reaktion einmal an
            self._last.forget(set(regulated.values()))
        self._level = cycle.level
        # Kommt ein Fahrzeug an eine EVCS, vergisst die Regelung die Werte seines Aktors: So geht
        # sein Höchststrom einmal je Ankunft hinaus (``maximum_command``).
        at_the_evcs, arrived = self._overrides.update(state.vehicles)
        self._last.forget(arrived)
        # Neu erkannt (Plan 2c-2b, Präzisierung 8); nicht mehr erkannt, entfällt der Grund.
        self._fresh = {
            key
            for key, vehicle in state.vehicles.items()
            if vehicle.fresh or (key in self._fresh and vehicle.kind is AssignmentKind.DETECTED)
        }

        commands: list[ChargeCommand] = []
        views: dict[str, _View] = {}
        for key in order:
            vehicle, point = state.vehicles[key], points.get(key)
            grant, share = cycle.grants.get(key), cycle.shares.get(key)
            free = None if grant is None else grant.free_a
            plan = planned.get(key)
            obs = inputs.vehicles.get(key, NO_VEHICLE_DATA) if key in socs else NO_VEHICLE_DATA
            views[key] = _View(
                modes[key],
                vehicle,
                free,
                soc=socs.get(key),
                limit_pct=finite_number(obs.charge_limit_pct),
                planned=None if plan is None else plan.state,
            )
            actuator = regulated.get(key)
            if point is None or actuator is None:  # ungeregelt: Die Maschine wartet
                if vehicle.new_session:
                    self._unseen_sessions.add(key)
                self._against.due(key, now, False)  # ohne Regelung gilt es als unterbrochen
                continue
            mode, target = modes[key], cycle.targets[key][1]
            # Die Grenze des 15-min-Wächters: Liegt sie unter dem freien Strom, gilt sie an dessen
            # Stelle, schon für den Überschuss und das Fenster des Tesla.
            cause, free_a, fuse_a = target, free, None
            cap_a = self._cap_a(point, mode, peak.caps_w.get(key))
            if cap_a is not None and free is not None and cap_a < free:
                cause, free_a = GuardCause.PEAK_GUARD, cap_a
                if target is GuardCause.PHASE_LIMIT:
                    fuse_a = math.floor(free)
            decision = self._decide(
                now, key, actuator, vehicle, mode, enough(point, share, free_a), plan
            )
            if point.evcs:
                wanted = cycle.wanted.get(key, 0)
                limit = point.min_a if free_a is None else math.floor(free_a)
            else:  # der Tesla am Wall Connector: Fenster (Präzisierung 12)
                wanted, limit = self._tesla[key].limits(now, cycle.wanted.get(key), free_a)
            locked = mode is not Effective.BOOST and key in peak.no_start  # die Start-Sperre
            situation = _Situation(
                plugged=vehicle.plugged,
                charging=vehicle.charging,
                mode=mode,
                decision=decision,
                cause=cause,
                fuse_a=fuse_a,
                wanted=wanted,
                limit=limit,
                share=share,
                level=cycle.level,
                unclear=vehicle.kind is AssignmentKind.UNCLEAR,
                resume=key in self._resume,
                carries=not locked
                and free_a is not None
                and limit + self._extra_a(mode) >= point.min_a,
                self_start=self._self_start(now, key, actuator, vehicle, decision, inputs),
                firm=None if plan is None else plan.code,
                reached=plan is not None and plan.reached,
            )
            found = (
                maximum_command(self._cfg, self._last, key, mode, inputs)
                if key in at_the_evcs
                else []
            )
            found += self._regulate(now, point, actuator, situation, inputs)
            charge_limit = self._limit(key, vehicle, plan, obs, inputs) if key in socs else None
            if charge_limit is not None:  # das Ladelimit vor den anderen Befehlen des Autos
                found.insert(0, charge_limit)
            if found and key in self._fresh:  # der erste Zyklus mit Befehlen seit der Erkennung
                found = [replace(c, reasons=(*c.reasons, ASSIGNMENT_DETECTED)) for c in found]
                self._fresh.discard(key)
            if found:
                self._reasons[key] = found[-1].reasons[-1]
            for command in found:
                facts = _facts(command.actuator, cycle.surplus, free, inputs.present)
                commands.append(ChargeCommand(command, facts))

        self._regulated = set(regulated.items())
        # Version und Ansichten wechseln zusammen: ``health`` läuft in einem anderen Thread und darf
        # die neue Version nie mit den Ansichten des vorigen Schritts zeigen
        self._views = views
        self._intent_version = None if intent is None else intent.version
        self._wallboxes = state.wallboxes
        self._cars = tuple(
            _car_view(self._cfg, point, modes[key], state.vehicles[key], inputs)
            for key, point in points.items()
        )
        self._unmet(now, points, modes, state.vehicles, inputs.present)
        self._vehicle_wallbox = {
            vehicle_actuator(key): None if v.kind is AssignmentKind.UNCLEAR else v.wallbox
            for key, v in state.vehicles.items()
            if vehicle_actuator(key) in self._limits.actuators
        }
        return commands

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Merkt das Urteil der Safety-Schicht über einen eigenen Befehl (Präzisierung 10).

        Ein Urteil mit Wert (gesendet oder Dry-Run) und ``UNCHANGED`` setzen den letzten eigenen
        Wert; der Befehl trägt danach kein ``not_taken_retry`` mehr. Ist es ein Start
        (``charging=True``), ist damit auch die erste Startfolge nach einer Pause erledigt.
        ``DEFERRED``, ``REJECTED`` und ``None`` (der Ausführer hat den Befehl nicht betrachtet)
        setzen nichts: Die Regelung bietet ihn im nächsten Zyklus wieder an. ``not_sent`` (ein Wert
        stand fest, ging aber nicht hinaus) löscht den letzten Wert.

        Ein Weckstart (``Command.wake``) mit einem Urteil mit Wert zählt im Weckkontingent, live wie
        im Dry-Run und auch mit ``not_sent`` (Präzisierung 12); die Zeit ist die des letzten
        ``step``. Ein Ladelimit, das den letzten eigenen Wert setzt, ist für seinen Ansteckvorgang
        und sein Ziel erledigt (``ChargeLimits``). Setzt das Urteil einen Start oder einen höheren
        Strom als den letzten eigenen, beginnt mit der Zeit des letzten ``step`` der Anlauf des
        Aktors (``RAMP_UP_S``, ``unmet_surplus_w``).
        """
        started = command.kind is _CHARGING and command.value is True
        before = self._last.requested((command.actuator, command.kind))
        judged = self._last.judged(command, verdict, not_sent=not_sent)
        if judged and _raises(command, before):  # der Anlauf beginnt (Ruling R23 a)
            self._ramp[command.actuator] = self._now
        if judged and started:
            self._resume -= {key for key, actor in self._regulated if actor == command.actuator}
        if judged and command.kind is _LIMIT:
            self._charge_limits.judged(command.actuator, command.value)
        if command.wake and verdict is not None and verdict.sendable:
            self._tesla[self._vehicles[command.actuator]].note_wake(self._now, self._wall_now)

    def note_result(self, result: WriteResult) -> None:
        """Merkt das Ergebnis eines gesendeten Befehls (Präzisierung 10).

        ``ERROR``, ``TIMEOUT`` und ``NOT_SENT`` löschen den letzten eigenen Wert dieser Art, aber
        nur, wenn er dem Ergebnis entspricht: Befehle des Keepers mit anderem Wert bleiben ohne
        Wirkung. Trägt das Ergebnis ``not_taken``, nennt der nächste Befehl dieser Art zusätzlich
        ``not_taken_retry`` und ist normal, nie dringend (Design 7.2). Ein gescheitertes Ladelimit
        ist danach wieder offen (``ChargeLimits``).
        """
        self._last.finished(result)
        self._charge_limits.finished(result)

    def idle(self, now: datetime) -> None:
        """Ein Zyklus ohne Regelung (Präzisierung 9): Die Regelung vergisst ihre letzten Befehle.

        Im nächsten ``step`` gilt jeder geregelte Ladepunkt als wieder geregelt: Seine Maschine
        bekommt ``resumed``. Lief die Regelung seit dem Start schon, trägt danach die erste
        Startfolge je Auto, auch eines Gastes, ``resume_after_outage``; Pausen vor dem ersten
        ``step`` (START, OBSERVE) sind kein Ausfall. Die Fenster des Tesla beginnen neu
        (Präzisierung 12), die Überlast mit einem neuen Wächter und der Stufe ``NONE``; die alten
        werteten die Lücke sonst als durchgehend. Die Zuordnung bleibt, damit ein beendeter Boost
        nicht wieder auflebt; das Weckkontingent bleibt ebenso und ein neu erkanntes Auto ohne
        Befehle seither (``assignment_detected``). ``health``, ``wallbox_health``,
        ``vehicle_wallbox`` und ``cars`` sind bis zum nächsten ``step`` unbekannt; der letzte Grund
        je Fahrzeug bleibt. ``now`` ist die Zeit der Safety-Schicht; das Vergessen braucht sie
        nicht.
        """
        self._last.forget()
        self._regulated = set()
        if self._stepped:
            self._resume = set(self._fleet)
        for tesla in self._tesla.values():
            tesla.restart()
        for key in self._tesla:
            self._against.clear(key)
        self._overload = OverloadGuard(self._guard)
        self._level = OverloadLevel.NONE
        # Erst die Version, dann die Ansichten: Ein Health, das die Version liest, nachdem die
        # Ansichten leer sind, bekommt ``None``. Liest es sie davor und die Ansichten danach, zeigt
        # es je Fahrzeug ``mode`` null; das nimmt die app als unbekannten Stand (Präzisierung 2).
        self._intent_version = None
        self._views = {}
        self._wallboxes = {}
        self._cars = ()
        self._unmet_w, self._held, self._spare_met = 0.0, (), frozenset()
        self._ramp, self._follow = {}, {}
        self._vehicle_wallbox = {}
        self._commands_hour = {}

    def forget(self, actuators: Collection[str]) -> None:
        """Vergisst die letzten eigenen Werte und die Merker ``not_taken_retry`` dieser Aktoren.

        Der nächste ``step`` bietet ihnen den Wunsch wieder an; hat das Gerät den Wert schon,
        antwortet die Safety-Schicht ``UNCHANGED``. Die Laufzeit ruft das, wo der letzte eigene Wert
        nicht mehr gilt: beim Wechsel der wirksamen Betriebsart (Dry-Run ↔ live), denn ein
        Dry-Run-Urteil zählte sonst als gesendet, und wenn sich das Gerät ohne die Regelung ändert
        (ein Wert des Keepers, ein anderes System übernimmt den Aktor). Auch das Ladelimit dieser
        Aktoren gilt dann als offen (``ChargeLimits``); ``idle`` lässt es erledigt.
        """
        self._last.forget(actuators)
        self._charge_limits.forget(actuators)

    def cars(self) -> tuple[CarView, ...]:
        """Je Ladepunkt des letzten ``step`` sein Auto, in der Reihenfolge der Priorität.

        Für Akku-Sollwert und 15-min-Wächter (Plan 2c-2a, Präzisierungen 3 und 4). Jeder
        Ladepunkt zählt, auch einer, der nicht geregelt wird (``CarView.regulated``) oder an dem
        das Auto nicht angesteckt ist: Er bleibt der Ladepunkt seines Autos, und auch solange evcc
        lädt, zählt sein Auto. Auch ein Gast hat einen, mit dem Modus des Gastes: an einer EVCS
        geregelt, am Wall Connector Last (Plan 2c-2b, Präzisierung 6). Vor dem ersten ``step`` und
        nach ``idle`` leer.
        """
        return self._cars

    def unmet_surplus_w(self, spare_w: float = 0.0) -> float:
        """Was die Autos im letzten ``step`` noch aufnehmen könnten, in W (Plan 6a, Entscheidung
        5.3, Rulings R18 und R23): der Vorrang der Autos vor der Klimaanlage.

        Es zählt jeder Ladepunkt, den die Laderegelung regelt, dessen Auto laut Zuordnung angesteckt
        ist, laden kann (``can_charge``) und im Modus PV oder Min+PV steht, mit
        ``max(0, Höchstleistung − Leistung)``; die Höchstleistung ist ``max_a × U × Phasen``. Das
        nur, solange das Auto seinem Befehl folgt: Seine Leistung ist bekannt und liegt höchstens
        eine Stufe von 1 A (auf seinen Phasen) unter dem befohlenen Strom. Zieht ein Auto deutlich
        weniger als befohlen, begrenzt es sich selbst (Abregeln, Ladelimit im Auto, Timer) und
        zählt nicht, ebenso bei unbekannter Leistung oder unbekanntem Befehl. Befohlen ist der
        Ist-Wert des Aktors (``present``), ohne ihn der letzte eigene Wert.

        - Anlauf (R23 a): Bis ``RAMP_UP_S`` nach einem eigenen Start oder einem höheren eigenen
          Strom folgt ein Auto seinem Befehl, auch wenn es noch nicht zieht oder sein Aktor noch
          „aus“ meldet.
        - Hysterese (R23 b): Das Urteil „folgt“ oder „begrenzt sich selbst“ wechselt erst, wenn das
          Gegenteil ``FOLLOW_HOLD_S`` lang ohne Unterbrechung gilt; das erste Urteil gilt sofort.
          Ein Auto, das sich selbst begrenzt, folgt erst wieder, wenn es bis auf eine Stufe den
          Strom zieht, bei dem es sich begrenzte, auch wenn die Regelung den Strom inzwischen
          senkte (``_Follow.reference_a``); „Laden aus“ gilt für sich. Bei unbekannter Leistung
          oder unbekanntem Befehl bleibt das Urteil stehen, und es fällt weg, sobald das Auto
          nicht mehr zählen kann (abgesteckt, anderer Modus, nicht geregelt).
        - „Laden aus“ (R23 c): Die eigene „Laden aus“ befiehlt 0 A. Ein Auto, das die Regelung so
          aushält, zählt nur, wenn ``spare_w`` seine Startleistung erreicht (``min_a × U ×
          Phasen``); sonst könnte es mit dem Überschuss nie starten. ``spare_w`` ist, was ein
          Start nutzen könnte: die Einspeisung und die Leistung, die die Klimaanlagen freigeben
          könnten (Ruling R28, ``Regulation.thermal_step``). Mit Hysterese (Ruling R28): Ein Auto,
          das beim letzten Aufruf zählte, zählt weiter, bis ``spare_w`` eine Stufe von 1 A auf
          seinen Phasen unter seine Startleistung fällt; danach wieder erst ab ihr. Die Regelung
          ruft das deshalb einmal je Zyklus.

        Anders als ein Wunsch aus dem Überschuss hängt das nicht davon ab, was die Klimaanlage
        gerade aufnimmt. Vor dem ersten ``step`` und nach ``idle`` ist es 0.
        """
        met = frozenset(
            vehicle
            for vehicle, start_w, _, band_w in self._held
            if spare_w >= (start_w - band_w if vehicle in self._spare_met else start_w)
        )
        self._spare_met = met
        held = sum(unmet_w for vehicle, _, unmet_w, _ in self._held if vehicle in met)
        return self._unmet_w + held

    def overload_level(self) -> OverloadLevel:
        """Die Stufe der Überlast im letzten ``step``; vor dem ersten und nach ``idle`` ``NONE``."""
        return self._level

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox für die Phasenvorhersage der Safety-Schicht.

        Für jedes Fahrzeug mit Tessie die Wallbox aus der Zuordnung des letzten ``step``; ``None``,
        solange sie unklar ist oder das Fahrzeug keine hat. Vor dem ersten ``step`` und nach
        ``idle`` leer: Die Wallbox ist dann unbekannt.
        """
        return dict(self._vehicle_wallbox)

    def health(self) -> list[dict[str, Any]]:
        """Je Fahrzeug, in der Reihenfolge von ``config.yaml``, der Stand des letzten ``step``; die
        Gäste nennt es nicht (Plan 2c-2b, Präzisierung 6).

        Die Felder: ``id``, ``name``, ``mode`` (Wert von ``Effective``), ``boost_until`` (ISO in
        UTC), ``wallbox``, ``assignment`` (Wert von ``AssignmentKind``), ``limit_a`` (freier Strom
        des Phasenwächters, nur für einen geregelten Ladepunkt), ``plugged``, ``charging``,
        ``wakes_today`` (Weckstarts am Kalendertag des letzten ``step``, Wiener Zeit),
        ``commands_hour`` (die Befehle der letzten Stunde, nur für ein Fahrzeug mit Tessie, sonst
        ``None``), ``boost_pending`` und ``override_pending`` (Boost bzw. Übersteuerung der Absicht
        gelten noch, warten aber auf einen Ansteckvorgang, der noch nicht begonnen hat; die Felder
        ``boost_until`` und ``assignment`` zeigen nur, was aktiv ist) und ``reason``, der
        letzte Grund des letzten Befehls für das Fahrzeug. Dazu (Plan 4b, Präzisierung 7)
        ``soc_pct`` und ``soc_source`` (``measured``, ``estimated``, ``assumed``; angenommen heißt
        0 %), ``charge_limit_pct`` (das Ladelimit im Auto, nur mit Tessie) und ``planned``: in
        „Geplant“ an einer Wallbox ``state`` (``plan``, ``surplus``, ``due``, ``reached``),
        ``target_soc_pct`` (das wirksame Ziel), ``departure_at`` (ISO in UTC), ``need_kwh``,
        ``latest_start`` (ISO in UTC, ``None`` ohne Bedarf) und ``plan_w``; sonst ``None``. Ohne
        ``step`` seit dem Start oder seit ``idle`` ist alles außer ``id``, ``name``,
        ``wakes_today`` und ``reason`` unbekannt (``None``); ``boost_pending`` und
        ``override_pending`` sind dann falsch.
        """
        entries: list[dict[str, Any]] = []
        for key, vehicle in self._cfg.vehicles.items():
            view = self._views.get(key)
            state = None if view is None else view.state
            boost = None if state is None or state.boost_until is None else state.boost_until
            soc = None if view is None else view.soc
            entries.append(
                {
                    "id": key,
                    "name": vehicle.name,
                    "mode": None if view is None else view.mode.value,
                    "boost_until": None if boost is None else ensure_utc(boost).isoformat(),
                    "wallbox": None if state is None else state.wallbox,
                    "assignment": None if state is None else state.kind.value,
                    "limit_a": None if view is None else view.limit_a,
                    "plugged": None if state is None else state.plugged,
                    "charging": None if state is None else state.charging,
                    "wakes_today": self._tesla[key].wakes_today(self._wall_now),
                    "commands_hour": self._commands_hour.get(vehicle_actuator(key)),
                    "boost_pending": state is not None and state.boost_pending,
                    "override_pending": state is not None and state.override_pending,
                    "reason": self._reasons.get(key),
                    "soc_pct": None if soc is None else soc.pct,
                    "soc_source": None if soc is None else soc.source,
                    "charge_limit_pct": None if view is None else view.limit_pct,
                    "planned": None if view is None else _planned_health(view.planned),
                }
            )
        return entries

    def wallbox_health(self) -> list[dict[str, Any]]:
        """Je Wallbox, in der Reihenfolge von ``config.yaml``, wer dort im letzten ``step`` stand
        (Plan 2c-2b, Präzisierung 11).

        Die Felder: ``id``, ``occupant`` (der Schlüssel des Fahrzeugs, ``guest`` für einen Gast,
        ``None`` ohne Vorgang), ``name`` (der Name des Fahrzeugs, ``Gast``, ``None``),
        ``assignment`` (Wert von ``AssignmentKind``, ``None`` ohne Vorgang) und ``indicators``, die
        Indizien des Zyklus an der Wallbox, je ``vehicle`` (Schlüssel aus ``config.yaml``), ``clue``
        (Wert von ``Clue``) und ``supports`` (stützt das Fahrzeug an der Wallbox, sonst schließt das
        Indiz es dort aus). Steht an einer Wallbox mit Vorgang niemand, was nur bei unklarer
        Zuordnung vorkommt, sind ``occupant`` und ``name`` ``None`` und ``assignment`` ``unclear``.
        Vor dem ersten ``step`` und nach ``idle`` sind ``occupant``, ``name`` und ``assignment``
        ``None``, und ``indicators`` ist leer.
        """
        entries: list[dict[str, Any]] = []
        for key in self._cfg.wallboxes:
            state = self._wallboxes.get(key, _NOBODY)
            car = state.occupant
            entries.append(
                {
                    "id": key,
                    "occupant": None if car is None else _occupant(car),
                    "name": None if car is None else car_spec(self._cfg, car).name,
                    "assignment": None if state.kind is None else state.kind.value,
                    "indicators": [
                        {"vehicle": e.vehicle, "clue": e.clue.value, "supports": e.supports}
                        for e in state.evidence
                    ],
                }
            )
        return entries

    def _unmet(
        self,
        now: datetime,
        points: Mapping[str, ChargePoint],
        modes: Mapping[str, Effective],
        vehicles: Mapping[str, VehicleState],
        present: Mapping[Key, CommandValue],
    ) -> None:
        """Was die Autos dieses Zyklus noch aufnehmen könnten (``unmet_surplus_w``): der Wunsch
        der Autos, die laden, und je Auto bei „Laden aus“ Startleistung und Wunsch."""
        volts = self._limits.voltage_v
        total, held = 0.0, []
        follow: dict[str, _Follow] = {}
        for car in self._cars:
            point, vehicle, mode = points[car.vehicle], vehicles[car.vehicle], modes[car.vehicle]
            if (
                not point.regulated
                or point.actuator is None
                or vehicle.plugged is not True
                or mode not in _UNMET_MODES
                or not can_charge(vehicle, mode)
            ):
                continue  # das Auto zählt nicht; sein Urteil fällt weg
            judgment = self._follow.get(car.vehicle)
            if judgment is not None:
                follow[car.vehicle] = judgment
            commanded_a = self._commanded_a(point.actuator, present)
            ramp = self._ramp.get(point.actuator)
            ramping = (
                commanded_a != 0.0 and ramp is not None and (now - ramp).total_seconds() < RAMP_UP_S
            )
            if car.power_w is None or (commanded_a is None and not ramping):
                continue  # Leistung oder Befehl unbekannt: Das Urteil bleibt stehen.
            step_w = volts * len(point.phases)  # eine Stufe von 1 A auf den Phasen des Autos
            seen = ramping
            if commanded_a is not None and not ramping:
                reference_a = commanded_a if judgment is None else judgment.reference_a(commanded_a)
                seen = car.power_w >= (reference_a - 1.0) * step_w
            if judgment is None:
                judgment = follow[car.vehicle] = _Follow(seen, None if seen else commanded_a)
            if not judgment.judge(seen, now, commanded_a):
                continue  # das Auto begrenzt sich selbst
            unmet_w = max(0.0, point.max_a * step_w - car.power_w)
            if commanded_a == 0.0:
                held.append((car.vehicle, point.min_a * step_w, unmet_w, step_w))
            else:
                total += unmet_w
        self._unmet_w, self._held, self._follow = total, tuple(held), follow

    def _commanded_a(self, actuator: str, present: Mapping[Key, CommandValue]) -> float | None:
        """Der befohlene Strom eines Aktors in A: 0 bei „Laden aus“; ``None``, wenn unbekannt.

        „Laden aus“ ist nur der eigene letzte Wert der Regelung: Beim Tesla meldet der Aktor als
        „Laden an“ den Ladezustand des Autos, keinen Befehl. Meldet der Aktor „aus“, ohne dass die
        Regelung es wollte, hat das Auto von selbst aufgehört: unbekannt. Sonst gilt der Strom des
        Aktors, ohne ihn der letzte eigene.
        """
        wanted = self._last.requested((actuator, _CHARGING))
        if wanted is False:
            return 0.0
        on = present.get((actuator, _CHARGING), wanted)
        if on is not True:
            return None
        current = present.get((actuator, _CURRENT))
        return finite_number(
            self._last.requested((actuator, _CURRENT)) if current is None else current
        )

    def _order(self, intent: Intent | None) -> list[str]:
        """Die Autos in der Reihenfolge ihrer Priorität: die Fahrzeuge, dahinter die Gäste.

        Die Fahrzeuge stehen in der Reihenfolge von ``intent.priority``, wenn sie jedes Fahrzeug der
        Konfiguration genau einmal nennt und kein anderes; sonst in der von ``config.yaml``. Die
        Gäste folgen in der Reihenfolge von ``fleet`` (Plan 2c-2b, Präzisierung 6).
        """
        configured = list(self._cfg.vehicles)
        priority = None if intent is None else intent.priority
        if priority is None or sorted(priority) != sorted(configured):
            priority = configured
        return [*priority, *self._guests]

    def _follow_plugs(self, inputs: ChargeInputs) -> None:
        """Die Ansteckvorgänge je Wallbox, wie die Zuordnung sie zählt (Plan 4b, Präzisierung 5).

        „Fahrzeug“ ohne laufenden Vorgang beginnt einen mit neuer Nummer und dem Beginn jetzt,
        „kein Fahrzeug“ beendet ihn, ein unbekannter Wert ändert nichts. Auch ein Vorgang, dessen
        wahrer Beginn unbekannt ist (die Wallbox meldete vorher nie „kein Fahrzeug“), beginnt jetzt:
        So hat jeder Vorgang einen Beginn, und ein Messwert von vor dem Start des core zählt nur,
        wenn er höchstens ``SOC_ANCHOR_BEFORE`` vor dem ersten Zyklus liegt.
        """
        for key, seen in inputs.wallboxes.items():
            if seen.connected is True and key not in self._plugs:
                self._plugs[key] = _Plug(next(self._plug_numbers), inputs.wall_now)
            elif seen.connected is False:
                self._plugs.pop(key, None)

    def _soc_of(self, key: str, vehicle: VehicleState, inputs: ChargeInputs) -> SocReading:
        """Der Ladestand des Fahrzeugs ``key`` (``SocTracker``, Plan 4b, Präzisierung 5).

        Zähler und Ansteckvorgang sind die der Wallbox, an der das Fahrzeug laut Zuordnung steht;
        steht es nirgends, gibt es beide nicht. Ein Wert von Tessie kommt aus der Abfrage
        (``polled``).
        """
        car = self._cfg.vehicles[key]
        obs = inputs.vehicles.get(key, NO_VEHICLE_DATA)
        plug = None if vehicle.wallbox is None else self._plugs.get(vehicle.wallbox)
        counter = None if vehicle.wallbox is None else inputs.wallboxes[vehicle.wallbox].total_kwh
        return self._soc.update(
            key,
            wall_now=inputs.wall_now,
            measured=finite_number(obs.soc_pct),
            measured_at=obs.soc_at,
            counter_kwh=finite_number(counter),
            session_start=None if plug is None else plug.start,
            efficiency=car.charge_efficiency_pct / 100.0,
            usable_kwh=car.usable_kwh,
            polled=car.tessie is not None,
        )

    def _planned_of(
        self,
        key: str,
        vehicle: VehicleState,
        soc: SocReading,
        inputs: ChargeInputs,
        active: ActiveSlot | None,
    ) -> _Planned | None:
        """Der Stand von „Geplant“ für das Fahrzeug ``key`` (``PlannedTracker``, Präzisierung 6).

        Nur an einer Wallbox: Die volle Leistung ist die des Fahrzeugs dort (``full_power_w``).
        Ziel und Abfahrt kommen aus der Absicht, das Ladelimit aus Tessie, der Ansteckvorgang von
        der Wallbox. Die Leistung des Plans (``slot_w``) ist die des gültigen Slots ``active`` für
        das Fahrzeug; ohne gültigen Slot oder wenn der Slot das Fahrzeug nicht nennt ``None``
        (Ruling R22).
        """
        if vehicle.wallbox is None:
            return None
        car = self._cfg.vehicles[key]
        intent = inputs.intent
        entry = None if intent is None else intent.vehicles.get(key)
        plug = self._plugs.get(vehicle.wallbox)
        full_w = full_power_w(
            car, self._cfg.wallboxes[vehicle.wallbox], self._cfg.grid.voltage_nominal_v
        )
        obs = inputs.vehicles.get(key, NO_VEHICLE_DATA)
        state = self._planned.step(
            key,
            wall_now=inputs.wall_now,
            session=None if plug is None else plug.number,
            intent=VehicleIntent() if entry is None else entry,
            soc=soc,
            limit_pct=finite_number(obs.charge_limit_pct),
            sync=car.charge_limit_sync,
            full_w=full_w,
            efficiency=car.charge_efficiency_pct / 100.0,
            usable_kwh=car.usable_kwh,
            slot_w=_slot_w(active, key),
        )
        reserved_w = 0.0
        if state.state == "plan":
            reserved_w = state.plan_w or 0.0
        elif state.state == "due":
            reserved_w = full_w
        return _Planned(state, reserved_w)

    def _limit(
        self,
        key: str,
        vehicle: VehicleState,
        plan: _Planned | None,
        obs: VehicleObs,
        inputs: ChargeInputs,
    ) -> Command | None:
        """Das Ladelimit des Fahrzeugs ``key`` (``ChargeLimits``, Plan 4b, Präzisierung 9).

        Der Ansteckvorgang ist der der Wallbox, an der das Fahrzeug steht. Zurückgehalten wird es,
        solange der Aktor nicht steuerbar ist (es weckt nicht), die Art blockiert ist oder der
        Tesla die Reserve seines Stundenbudgets erreicht hat (wie ``_reserved``).
        """
        actuator = vehicle_actuator(key)
        plug = None if vehicle.wallbox is None else self._plugs.get(vehicle.wallbox)
        commands = self._limits.vehicle_commands.get(actuator)
        used = inputs.commands_hour.get(actuator)
        reserve = (
            commands is not None
            and used is not None
            and used >= commands.per_hour - self._cfg.control.tesla_reserve_commands
        )
        blocked = (
            inputs.controllable.get(actuator) is not True
            or (actuator, _LIMIT) in inputs.blocked
            or reserve
        )
        return self._charge_limits.limit_command(
            self._cfg,
            self._last,
            key,
            None if plan is None else plan.state,
            obs,
            None if plug is None else plug.number,
            blocked,
        )

    def _allocate(
        self,
        order: Sequence[str],
        modes: Mapping[str, Effective],
        points: Mapping[str, ChargePoint],
        vehicles: Mapping[str, VehicleState],
        inputs: ChargeInputs,
        planned: Mapping[str, _Planned],
    ) -> _Cycle:
        """Last je Phase, Überlast, Überschuss und Zuteilung des Wächters (Präzisierungen 6 und 7).

        Abgezogen wird nur der Strom geregelter Ladepunkte; dazu kommen die Zuschläge bei
        Ausfällen. Den Überschuss teilen sich die geregelten Ladepunkte, deren Auto laden kann, in
        der Reihenfolge ``order``; in derselben Reihenfolge teilt der Wächter zu.

        „Geplant“ (``planned``, Plan 4b, Präzisierung 7): Ein Auto am Ziel nimmt nicht teil. Der
        feste Bedarf geht vor den PV-Autos vom Überschuss ab und bestimmt den Wunsch mit; mit ihm
        gilt das Ziel des Phasenwächters wie bei voller Leistung.
        """
        grid = inputs.grid
        regulated = [points[key] for key in order if key in points and points[key].regulated]
        loads = phase_loads(
            grid.phase_import_a,
            grid.battery_feed_a,
            [(point.phases, point.measured) for point in regulated],
            reserved_a(self._cfg, list(points.values()), inputs.wallboxes),
        )
        level = self._overload.update(inputs.now, grid.phase_import_a)
        reserved = {key: plan.reserved_w for key, plan in planned.items()}
        reached = {key for key, plan in planned.items() if plan.reached}
        able = [
            p
            for p in regulated
            if can_charge(vehicles[p.vehicle], modes[p.vehicle], reached=p.vehicle in reached)
        ]
        intent = inputs.intent
        surplus = surplus_w(
            cars_w=cars_w(able, inputs.wallboxes),
            grid_w=grid.grid_w,
            battery_w=grid.battery_w,
            soc_pct=grid.soc_pct,
            priority=SurplusPriority() if intent is None else intent.surplus_priority,
            margin_w=self._cfg.control.surplus_margin_w,
        )
        volts = self._limits.voltage_v
        cars = [
            self._surplus_car(point, modes[point.vehicle], reserved.get(point.vehicle, 0.0))
            for point in able
        ]
        shares = share_surplus(surplus, cars, volts)
        wanted = {
            p.vehicle: wanted_a(
                p,
                modes[p.vehicle],
                shares.get(p.vehicle),
                reserved_w=reserved.get(p.vehicle, 0.0),
                voltage_v=volts,
            )
            for p in able
        }
        targets = {
            key: phase_target(
                self._guard,
                full_power=modes[key] in FULL_POWER or reserved.get(key, 0.0) > 0,
                soc_pct=grid.soc_pct,
            )
            for key in order
        }
        claims = [
            Claim(
                p.vehicle, p.phases, targets[p.vehicle][0], wanted.get(p.vehicle, 0), holding_a(p)
            )
            for p in regulated
        ]
        return _Cycle(surplus, shares, wanted, targets, allocate(loads, claims), level)

    def _surplus_car(self, point: ChargePoint, mode: Effective, reserved_w: float) -> SurplusCar:
        """Das Auto des Ladepunkts in der Verteilung; ein ladendes PV-Auto hält ``min_a``.

        An einer EVCS hält die Verteilung den zuletzt angeforderten Strom mit der Hysterese
        ``control.evcs_hysteresis_a`` (Design 6.5); ohne eigenen Wert rundet sie nur ab.
        ``reserved_w`` ist der feste Bedarf eines Autos in „Geplant“, sonst 0; ohne ihn hält es
        ladend ``min_a`` wie PV.
        """
        return SurplusCar(
            key=point.vehicle,
            full_power=mode in FULL_POWER,
            minimum=mode is Effective.MIN_PV,
            phases=len(point.phases),
            min_a=point.min_a,
            max_a=point.max_a,
            holding_min=mode in _PV_LIKE and self._machines[point.vehicle].charging,
            held_a=self._held_a(point),
            hysteresis_a=self._cfg.control.evcs_hysteresis_a,
            reserved_w=reserved_w,
        )

    def _held_a(self, point: ChargePoint) -> int | None:
        """Der zuletzt angeforderte Strom einer EVCS in ganzen A; sonst ``None``."""
        if not point.evcs or point.actuator is None:
            return None
        held = finite_number(self._last.requested((point.actuator, _CURRENT)))
        return None if held is None else math.floor(held)

    def _decide(
        self,
        now: datetime,
        key: str,
        actuator: str,
        vehicle: VehicleState,
        mode: Effective,
        enough: bool | None,
        plan: _Planned | None,
    ) -> ModeDecision:
        """Die Maschine eines geregelten Ladepunkts; beginnt er, vergisst die Regelung ihre Werte.

        Der Ladepunkt beginnt, wenn er wieder geregelt wird (``back``), mit einem neuen
        Ansteckvorgang und wenn ein gemerkter Vorgang die Maschine erreicht, also beim ersten
        bekannten Ansteckzustand. Jedes davon geschieht einmal: Dann vergisst die Regelung die
        letzten Werte des Aktors. Mit einem Ansteckvorgang beginnen auch die Fenster eines
        Fahrzeugs neu (``TeslaPolicy``); ein Gast hat keine.

        In „Geplant“ (``plan``) gilt ein erreichtes Ziel wie „geladen“, und der feste Bedarf geht
        als ``firm`` an die Maschine (Plan 4b, Präzisierung 7).
        """
        back = (key, actuator) not in self._regulated  # wieder geregelt: Übernahme
        remembered = key in self._unseen_sessions
        reached = remembered and vehicle.plugged is not None  # der gemerkte Vorgang kommt an
        if (vehicle.new_session or reached) and key in self._tesla:
            self._tesla[key].restart()
            self._against.clear(key)
        if back or vehicle.new_session or reached:
            self._last.forget({actuator})
        if vehicle.plugged is not None:  # bei unbekanntem bleibt er für den nächsten Schritt
            self._unseen_sessions.discard(key)
        session = vehicle.new_session or remembered
        return self._machines[key].step(
            now,
            ModeInput(
                mode=mode,
                plugged=vehicle.plugged,
                complete=vehicle.complete or (plan is not None and plan.reached),
                enough=enough,
                observed_charging=vehicle.charging,
                new_session=session,
                resumed=back and not session,  # ein neuer Ansteckvorgang geht vor
                firm=plan is not None and plan.code is not None,
            ),
        )

    def _regulate(
        self,
        now: datetime,
        point: ChargePoint,
        actuator: str,
        situation: _Situation,
        inputs: ChargeInputs,
    ) -> list[Command]:
        """Überlast, Strom und Befehle für einen geregelten Ladepunkt (Präzisierungen 8 bis 12).

        Stoppt die Überlast den Ladepunkt (``ALL_STOP``, bei einer EVCS schon ``EVCS_STOP``),
        stoppt sie auch seinen Modus (``force_stop``), damit danach die Mindestpause gilt. Ab
        ``MINIMUM`` stoppt sie dringend auch ein Auto, das lädt, obwohl sein Modus aus will (etwa
        selbst gestartet), außer solange die Zuordnung unklar ist. Beides gilt auch bei unbekanntem
        Ansteckzustand. Dringend sind die Befehle wegen Überlast und eine Senkung unter den
        Ist-Strom, wenn das Ziel die Sicherung ist (Präzisierung 11; kappt die Grenze des
        15-min-Wächters, nur was die Sicherung allein verlangt, ``_urgent``), außer als erneuter
        Befehl nach „nicht übernommen“ (``LastValues.command``). Ein Aktor, der nicht steuerbar ist
        (geregelt ist er dann nur, weil er weckbar ist), bekommt höchstens den Weckstart. Starten
        darf ein Auto, das nicht lädt, nur, wenn die Grenze seinen Mindeststrom trägt
        (``carries``).
        """
        level = situation.level
        stop = level >= OverloadLevel.ALL_STOP or (point.evcs and level >= OverloadLevel.EVCS_STOP)
        charge = situation.decision.charge
        if stop and charge:
            self._machines[point.vehicle].force_stop(now)
            charge = False
        blocked = inputs.blocked
        minimum = level >= OverloadLevel.MINIMUM
        may_start = not minimum and (situation.carries or situation.charging is True)
        if inputs.controllable.get(actuator) is not True:
            if charge and may_start and situation.plugged is True:
                return self._wake(point.vehicle, actuator, situation, blocked)
            return []
        head = situation.head
        against = minimum and not charge and situation.charging is True and not situation.unclear
        if stop or against:
            return self._switch_off(actuator, (*head, OVERLOAD), blocked, urgent=True)
        present_a = finite_number(inputs.present.get((actuator, _CURRENT)))
        if situation.plugged is None or situation.unclear:
            return self._hold(point, actuator, situation, present_a, blocked)
        if not charge:
            off = (*head, PV_SURPLUS) if situation.decision.reason == PV_SURPLUS else head
            if situation.reached:
                off = (*off, TARGET_REACHED)
            if situation.self_start:
                off = (*off, SELF_START)
            return self._switch_off(actuator, off, blocked, urgent=situation.mode is Effective.OFF)
        reasons: tuple[str, ...]
        if minimum:
            value, reasons = point.min_a, (*head, OVERLOAD)
        else:
            value = _current_a(point, situation.wanted, situation.limit)
            reasons = (*head, *_why(situation))
        urgent = minimum or _urgent(situation, point, value, present_a, situation.wanted)
        held = not urgent and self._reserved(
            point, actuator, inputs, value=value, present_a=present_a, limit=situation.limit
        )
        return self._charge(
            actuator,
            float(value),
            reasons,
            blocked,
            urgent=urgent,
            held=held,
            start=may_start,
            resume=situation.resume,
        )

    def _self_start(
        self,
        now: datetime,
        key: str,
        actuator: str,
        vehicle: VehicleState,
        decision: ModeDecision,
        inputs: ChargeInputs,
    ) -> bool:
        """Ob das Fahrzeug lange genug gegen seinen Modus lädt, damit der Stopp erneut hinausgeht.

        Gegen den Modus lädt ein Fahrzeug mit Tessie, wenn Tessie es mit mindestens 1 A ladend
        meldet, der Modus nicht laden will (``decision.charge`` falsch), der Ansteckzustand bekannt
        und die Zuordnung klar ist. ``AgainstMode`` wird nur befragt, wenn der Stopp in diesem
        Zyklus hinausgehen kann (Aktor steuerbar, ``charging`` nicht blockiert); sonst läuft die
        Zeit weiter und der Neuversuch bleibt unverbraucht. Ist ``due`` wahr, vergisst die Regelung
        den letzten Wert für ``charging`` des Aktors (nur diese Art, nicht den Strom und dessen
        ``not_taken_retry``), damit „Laden aus“ erneut hinausgeht.
        """
        if key not in self._tesla or self._cfg.vehicles[key].tessie is None:
            return False
        tessie = inputs.vehicles.get(key, NO_VEHICLE_DATA)
        current_a = finite_number(tessie.actual_current_a)
        against = (
            tessie.reachable
            and tessie.charging is True
            and current_a is not None
            and current_a >= _SELF_START_MIN_A
            and not decision.charge
            and vehicle.plugged is not None
            and vehicle.kind is not AssignmentKind.UNCLEAR
        )
        if not against:
            self._against.due(key, now, False)  # unterbricht die Zeit
            return False
        sendable = inputs.controllable.get(actuator) is True and (
            (actuator, _CHARGING) not in inputs.blocked
        )
        if not sendable:  # der Stopp kann diesen Zyklus nicht hinaus: die Zeit läuft weiter
            return False
        due = self._against.due(key, now, True)
        if due:
            self._last.forget_key((actuator, _CHARGING))
        return due

    def _extra_a(self, mode: Effective) -> float:
        """Was die Grenze für einen Start mehr trägt: in Min+PV den Abstand vom Importziel zum Ziel
        an der Sicherung, denn dort geht der Mindeststrom dem Importlimit vor (Design 6.3)."""
        if mode is Effective.MIN_PV:
            return self._guard.fuse_target_a - self._guard.import_target_a
        return 0.0

    def _cap_a(self, point: ChargePoint, mode: Effective, cap_w: float | None) -> float | None:
        """Die Grenze ``cap_w`` des 15-min-Wächters für den Ladepunkt in A: ``W / (U · Phasen)``.

        Auf 1e-9 A gerundet: Eine Grenze genau an der Mindestleistung ``min_a × U × Phasen`` ergibt
        so wieder ``min_a``, auch wenn die Gleitkommarechnung knapp darunter landet. ``None`` ohne
        Grenze, in Boost und bei einer Grenze, die keine endliche Zahl ist.
        """
        watts = finite_number(cap_w)
        if mode is Effective.BOOST or watts is None:
            return None
        return round(watts / (self._limits.voltage_v * len(point.phases)), 9)

    def _wake(
        self, key: str, actuator: str, situation: _Situation, blocked: frozenset[Key]
    ) -> list[Command]:
        """Der Weckstart (Design 7.1): Die Gründe sind die eines Starts, danach ``wake``.

        ``wake_command`` entscheidet, ob und wann er kommt; die Gründe baut die Regelung.
        """
        resumed = (RESUME_AFTER_OUTAGE,) if situation.resume else ()
        reasons = (*situation.head, *_why(situation), *resumed, WAKE)
        return wake_command(
            self._last, self._tesla[key], self._now, self._wall_now, actuator, reasons, blocked
        )

    def _hold(
        self,
        point: ChargePoint,
        actuator: str,
        situation: _Situation,
        present_a: float | None,
        blocked: frozenset[Key],
    ) -> list[Command]:
        """Kein „Laden an/aus“, nur eine Senkung (Präzisierungen 4 und 5).

        So bei unbekanntem Ansteckzustand und solange die Zuordnung unklar ist: Der Strom sinkt
        nur, wenn der Wächter, die Grenze des 15-min-Wächters oder die Überlast weniger als den
        bekannten Ist-Strom erlaubt, nie unter ``min_a``.
        """
        if present_a is None or (actuator, _CURRENT) in blocked:
            return []
        reasons: tuple[str, ...]
        if situation.level >= OverloadLevel.MINIMUM:
            value, reasons, urgent = point.min_a, (*situation.head, OVERLOAD), True
        else:
            value = _current_a(point, point.max_a, situation.limit)  # ohne Wunsch
            reasons = (*situation.head, situation.cause.value)
            urgent = _urgent(situation, point, value, present_a, point.max_a)  # ohne Wunsch
        if value >= present_a or not self._changed((actuator, _CURRENT), float(value)):
            return []
        return [self._last.command(actuator, _CURRENT, float(value), reasons, urgent=urgent)]

    def _charge(
        self,
        actuator: str,
        value: float,
        reasons: tuple[str, ...],
        blocked: frozenset[Key],
        *,
        urgent: bool,
        held: bool,
        start: bool,
        resume: bool,
    ) -> list[Command]:
        """Laden: der Strom, wenn er sich ändert; für einen Start danach ``charging=True``.

        ``held`` hält den Strombefehl zurück (Reserve des Stundenbudgets, ``_reserved``); er
        erscheint dann nicht im Log. Gehört er zu einem Start, hält ihn die Reserve nicht zurück:
        Der Strom geht vor dem Start hinaus.

        Ein Start (``charging=True`` ändert sich) folgt erst auf den Strom: Muss der gesendet
        werden und ist er blockiert, wartet der Start. ``start`` ist falsch bei Überlast und bei
        einem Auto, das nicht lädt, auch dann, wenn die Grenze des Wächters seinen Mindeststrom
        nicht trägt oder unbekannt ist (Design 5.3, 5.5; ``_Situation.carries``). Steht ein Start
        an und ist es die erste Startfolge nach einer Pause (``resume``), tragen Strom und Start
        zusätzlich ``resume_after_outage``.
        """
        current, on = (actuator, _CURRENT), (actuator, _CHARGING)
        starting = start and self._changed(on, True)
        send = self._changed(current, value) and not (held and not starting)
        if send and current in blocked:
            return []
        if starting and resume:
            reasons = (*reasons, RESUME_AFTER_OUTAGE)
        found: list[Command] = []
        if send:
            found.append(self._last.command(actuator, _CURRENT, value, reasons, urgent=urgent))
        if starting and on not in blocked:
            found.append(self._last.command(actuator, _CHARGING, True, reasons))
        return found

    def _reserved(
        self,
        point: ChargePoint,
        actuator: str,
        inputs: ChargeInputs,
        *,
        value: int,
        present_a: float | None,
        limit: int,
    ) -> bool:
        """Hält die Reserve des Stundenbudgets den Strombefehl zurück (Präzisierung 9)?

        Nur beim Tesla am Wall Connector, dessen Aktor ``ChargeInputs.commands_hour`` nennt: Hat er
        ``per_hour − tesla_reserve_commands`` Befehle in der Stunde erreicht, geht ein Strombefehl
        nur noch hinaus, wenn er den Ist-Strom senkt und der Ist-Strom über der Grenze des Wächters
        (``limit``) liegt. Ist der Ist-Strom unbekannt, hält die Reserve nichts zurück. Starts samt
        ihrem Strom, Stopps, der Höchststrom an der EVCS und dringende Befehle prüft sie nicht
        (``_charge`` nimmt den Strom eines Starts aus).
        """
        used = inputs.commands_hour.get(actuator)
        if point.evcs or used is None:
            return False
        per_hour = self._limits.vehicle_commands[actuator].per_hour
        if used < per_hour - self._cfg.control.tesla_reserve_commands:
            return False
        if present_a is None:
            return False
        return not (value < present_a and present_a > limit)

    def _switch_off(
        self, actuator: str, reasons: tuple[str, ...], blocked: frozenset[Key], *, urgent: bool
    ) -> list[Command]:
        """Nicht laden: ``charging=False``, wenn es sich ändert, ohne Strom."""
        off = (actuator, _CHARGING)
        if off in blocked or not self._changed(off, False):
            return []
        return [self._last.command(actuator, _CHARGING, False, reasons, urgent=urgent)]

    def _changed(self, key: Key, value: CommandValue) -> bool:
        """Ob ``value`` vom letzten eigenen Wert dieser Art abweicht; ohne einen immer."""
        return self._last.changed(key, value)


def _slot_w(active: ActiveSlot | None, key: str) -> float | None:
    """Die Leistung des gültigen Slots ``active`` für das Fahrzeug ``key`` in W; ``None`` ohne
    gültigen Slot und wenn der Slot das Fahrzeug nicht nennt (Ruling R22: dann gilt die späteste
    Startzeit, etwa für ein Auto, das nach dem Plan angesteckt wurde, oder einen Plan aus 4a)."""
    if active is None or key not in active.vehicles:
        return None
    return float(active.vehicles[key])


def _planned_health(planned: PlannedState | None) -> dict[str, Any] | None:
    """Der Stand von „Geplant“ im Health (``ChargeController.health``); ``None`` ohne."""
    if planned is None:
        return None
    start = planned.latest_start
    return {
        "state": planned.state,
        "target_soc_pct": planned.target_soc_pct,
        "departure_at": ensure_utc(planned.departure_at).isoformat(),
        "need_kwh": planned.need_kwh,
        "latest_start": None if start is None else ensure_utc(start).isoformat(),
        "plan_w": planned.plan_w,
    }


def _raises(command: Command, before: CommandValue | None) -> bool:
    """Ob der Befehl gegenüber dem letzten eigenen Wert ``before`` startet oder den Strom erhöht."""
    if command.kind is _CHARGING:
        return command.value is True and before is not True
    if command.kind is not _CURRENT:
        return False
    new, old = finite_number(command.value), finite_number(before)
    return new is not None and old is not None and new > old


def _occupant(car: str) -> str:
    """Der Insasse im Health: der Schlüssel des Fahrzeugs, für einen Gast ``guest`` ohne Wallbox."""
    return car if guest_wallbox(car) is None else GUEST


def _mode(key: str, vehicle: VehicleState, intent: Intent | None) -> Effective:
    """Der geltende Modus: Boost, solange er gilt; sonst der Modus der Absicht, ohne Eintrag PV.

    Für einen Gast gilt ``intent.guest.mode``, ohne Absicht der von ``GuestIntent()``, nie Boost
    (Plan 2c-2b, Präzisierung 6).
    """
    if guest_wallbox(key) is not None:
        return effective_of((GuestIntent() if intent is None else intent.guest).mode)
    if vehicle.boost_until is not None:
        return Effective.BOOST
    entry = None if intent is None else intent.vehicles.get(key)
    return effective_of((VehicleIntent() if entry is None else entry).mode)


def _car_view(
    cfg: Config, point: ChargePoint, mode: Effective, vehicle: VehicleState, inputs: ChargeInputs
) -> CarView:
    """Das Auto des Ladepunkts für ``cars`` (Plan 2c-2a, Präzisierung 4; Regeln: ``CarView``)."""
    volts = cfg.grid.voltage_nominal_v
    power_w = finite_number(inputs.wallboxes[point.wallbox].power_w)
    if power_w is None and car_spec(cfg, point.vehicle).tessie:
        power_w = _tessie_w(inputs.vehicles.get(point.vehicle, NO_VEHICLE_DATA), volts)
    min_w = point.min_a * volts * len(point.phases)
    return CarView(
        point.vehicle, point.wallbox, mode, power_w, vehicle.charging, min_w, point.regulated
    )


def _tessie_w(tessie: VehicleObs, volts: float) -> float | None:
    """Die Ladeleistung laut Tessie, ``actual_current_a × phases × volts``; ``None``, wenn Tessie
    nicht erreichbar ist oder eins davon fehlt."""
    if not tessie.reachable or tessie.actual_current_a is None or tessie.phases is None:
        return None
    return finite_number(tessie.actual_current_a * tessie.phases * volts)


def _why(situation: _Situation) -> tuple[str, ...]:
    """Der Grund nach dem Modus-Code, wenn das Auto lädt und keine Überlast herrscht.

    ``pv_surplus``, wenn der Wert aus dem Anteil kommt (in PV, Min+PV oder „Geplant“ ohne festen
    Bedarf liegt der Wunsch nicht über der Grenze) oder die Zeiten von PV-Überschuss gerade
    gestartet haben; sonst der Grund der Grenze (``GuardCause``, auch ``peak_guard``), wenn sie
    kappt; sonst keiner.

    Mit festem Bedarf in „Geplant“ steht zuerst sein Grund (``plan_power``, ``latest_start``),
    danach der Grund der Grenze, wenn sie kappt, sonst ``pv_surplus``, wenn der Anteil den Wunsch
    trägt (Plan 4b, Präzisierung 7).
    """
    if situation.firm is not None:
        if situation.limit < situation.wanted:
            return (situation.firm, situation.cause.value)
        if situation.share is not None and situation.share >= situation.wanted:
            return (situation.firm, PV_SURPLUS)
        return (situation.firm,)
    from_share = (
        situation.share is not None
        and situation.mode in _SURPLUS_MODES
        and situation.wanted <= situation.limit
    )
    if from_share or situation.decision.reason == PV_SURPLUS:
        return (PV_SURPLUS,)
    if situation.limit < situation.wanted:
        return (situation.cause.value,)
    return ()


def _current_a(point: ChargePoint, wanted: int, limit: int) -> int:
    """Der Strom aus Wunsch und Grenze im Strombereich des Ladepunkts.

    ``min(max(min(wanted, limit), min_a), max_a)``: nicht über Wunsch und Grenze, aber nie unter
    ``min_a`` und nie über ``max_a``. Ohne Wunsch (``_hold``) steht dafür ``max_a``.
    """
    return min(max(min(wanted, limit), point.min_a), point.max_a)


def _urgent(
    situation: _Situation, point: ChargePoint, value: int, present_a: float | None, wanted: int
) -> bool:
    """Ob eine Senkung auf ``value`` dringend ist (Präzisierung 11).

    Dringend ist eine Senkung unter den Ist-Strom, wenn die Grenze sich nach der Sicherung richtet.
    Kappt die Grenze des 15-min-Wächters (``fuse_a``), nur dann, wenn schon die Sicherung allein mit
    dem Wunsch ``wanted`` unter den Ist-Strom senkte: Eine Senkung wegen dieser Grenze ist nie
    dringend (Plan 2c-2a, Präzisierung 11), sie nimmt aber auch der Sicherung nichts.

    Mit festem Bedarf in „Geplant“ zählt nur, was die Sicherung allein verlangt, ohne den Wunsch:
    Die Leistung des Plans liegt oft unter dem Ist-Strom, und eine Senkung auf sie ist nie dringend
    (Plan 4b, Präzisierung 7).
    """
    if present_a is None or value >= present_a:
        return False
    wish = point.max_a if situation.firm is not None else wanted
    if situation.cause is GuardCause.PHASE_LIMIT:
        return _current_a(point, wish, situation.limit) < present_a
    fuse_a = situation.fuse_a
    if fuse_a is None:
        return False
    return _current_a(point, wish, fuse_a) < present_a


def _facts(
    actuator: str,
    surplus: float | None,
    free: float | None,
    present: Mapping[Key, CommandValue],
) -> dict[str, float | None]:
    """Die Eingaben eines Befehls an ``actuator`` für das Entscheidungslog (Präzisierung 13).

    Die Ist-Werte kommen aus ``present``; im Dry-Run sind das die Werte des fremden Reglers.
    ``free`` ist der freie Strom des Ladepunkts, zu dem der Befehl gehört.
    """
    charging = present.get((actuator, _CHARGING))
    return {
        "surplus_w": surplus,
        "limit_a": free,
        "actual_current_a": finite_number(present.get((actuator, _CURRENT))),
        "actual_charging": float(charging) if isinstance(charging, bool) else None,
    }
