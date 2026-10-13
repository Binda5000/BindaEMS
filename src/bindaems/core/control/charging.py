"""Laderegelung: der ``ChargeController`` verbindet die Bausteine je Zyklus (Design 4 bis 9).

Ein Zyklus (``step``) geht so (Präzisierungen 4 bis 12):

1. Zuordnung (``Assignment``) und der geltende Modus je Fahrzeug, bei aktivem ``boost_until``
   Boost. Die Reihenfolge ist ``intent.priority``, wenn sie jedes Fahrzeug der Konfiguration genau
   einmal nennt, sonst die von ``config.yaml``; Einträge für unbekannte Fahrzeuge zählen nicht.
2. Ladepunkte (``_Point``): Aktor, Phasen, Strombereich, gemessener Strom und ob er geregelt wird
   (Präzisierungen 5 und 6). Daraus die Last je Phase samt Zuschlägen (``phase_loads``) und die
   Stufe der Überlast am echten Netzstrom (``OverloadGuard``).
3. Überschuss (``surplus_w``) und seine Verteilung (``share_surplus``), dann die Zuteilung des
   Wächters (``allocate``), beides in der Reihenfolge der Priorität.
4. Je geregeltem Ladepunkt der Modus (``ModeMachine``), Überlast, Strom und Befehle. Die Maschine
   eines Ladepunkts, der nicht geregelt wird, wartet; wird er wieder geregelt (auch im ersten
   Zyklus nach dem Start oder nach ``idle``), bekommt sie einmal ``resumed`` und übernimmt den
   beobachteten Zustand (Präzisierung 9). Begann in der Zwischenzeit ein Ansteckvorgang, bekommt
   sie stattdessen ``new_session``: PV und Aus beginnen dann ohne Laden, auch wenn das Auto selbst
   gestartet hat. So beim Tesla, den Tessie erst nach dem Wall Connector als angesteckt meldet.
   Der gemerkte Vorgang erreicht die Maschine im ersten Schritt mit bekanntem Ansteckzustand.

Die Regelung schickt nur Änderungen (Präzisierung 10): Der letzte eigene Wert je Aktor und Art
folgt den Urteilen (``note_verdict``) und den Ergebnissen (``note_result``). Sie vergisst ihn, wo
sich das Gerät ohne sie geändert haben kann; die Safety-Schicht antwortet ``UNCHANGED``, wenn es
den Wert schon hat:

- nach einer Pause (``idle``) für alle Aktoren,
- für den Aktor eines Ladepunkts, wenn er wieder geregelt wird, mit einem neuen Ansteckvorgang und
  wenn ein gemerkter Vorgang die Maschine erreicht; jedes davon geschieht einmal,
- mit jeder höheren Stufe der Überlast für alle geregelten Aktoren: Jede Stufe bietet ihre
  Reaktion einmal an.

Tesla, Zuordnung und Wiederaufnahme (Design 7 bis 9; die Bausteine stehen in ``charging_tesla``):

- Ein Fahrzeug mit Tessie am Wall Connector bekommt ``floor(min(Mittel des Wunsches, niedrigster
  freier Wert))`` über ``control.tesla_average_s`` bzw. ``control.tesla_caution_s``, in seinem
  Strombereich (Präzisierung 12). Der Wunsch zählt nur, solange das Auto laden kann. Beide
  Fenster beginnen mit jedem neuen Ansteckvorgang und nach ``idle`` neu; wird der Ladepunkt nur
  wieder geregelt, behalten sie ihre Werte.
- Will der Modus starten und ist der Aktor nicht steuerbar, aber weckbar, kommt ein Start mit
  Weckerlaubnis (``wake``) ohne Strom: bei bekanntem Ansteckzustand, ohne Überlast und im
  Weckkontingent. Das Kontingent zählt jeden Weckstart mit einem Urteil mit Wert.
- „Tesla an der EVCS“: Hängt ein Fahrzeug mit Tessie laut Übersteuerung an einer EVCS, bekommt es
  einmal je Ansteckvorgang seinen Höchststrom mit dem Modus als Grund, sobald es steuerbar ist und
  die EVCS geregelt wird; geregelt wird dann die EVCS. Erledigt ist das wie jeder Befehl
  (Präzisierung 10); nach ``idle`` geht es noch einmal hinaus.
- Solange die Zuordnung unklar ist (Präzisierung 5), gibt es kein „Laden an/aus“ nach dem Modus;
  der Strom sinkt nur, wenn der Wächter weniger als den Ist-Strom erlaubt, nie unter den
  Mindeststrom. Die Gründe sind ``(Modus, "assignment_unclear", Ursache)``. Die Überlast setzt
  weiter den Mindeststrom und stoppt ab ``EVCS_STOP``. Fahrzeuge mit Tessie pausieren
  (Präzisierung 6).
- Nach ``idle`` trägt die erste Startfolge (Strom und ``charging=True``) je Fahrzeug zusätzlich
  ``resume_after_outage``, bis die Safety-Schicht ihren Start mit Wert oder ``UNCHANGED``
  beurteilt. Das gilt erst, wenn seit dem Start des core schon geregelt wurde.

Dazu legt die Regelung fest:

- Am Überschuss nimmt ein geregelter Ladepunkt nur teil, wenn sein Auto laden kann: nicht
  abgesteckt, nicht „geladen“ und nicht in Aus. Nur die Leistung dieser Wallboxen zählt zu
  ``cars_w``, und nur diese Autos wünschen Strom. Sonst nähme ein abgestecktes Auto in Sofort den
  anderen ``max_a × U × Phasen`` weg, und die Leistung eines ungeregelt ladenden Autos gälte als
  Überschuss; sein Strom ist aber Last (Design 9).
- Überlast startet und weckt nichts: Ab ``MINIMUM`` gibt es kein „Laden an“, nur den
  Mindeststrom. Ein Auto, das lädt, obwohl sein Modus aus will (etwa selbst gestartet), stoppt
  sie dringend, außer solange die Zuordnung unklar ist.
- Ein Start wartet, solange sein Strom gesendet werden muss, aber blockiert ist.
- Ist der Ansteckzustand unbekannt, sinkt der Strom nur unter den Ist-Strom, wenn der Wächter es
  verlangt; „Laden aus“ kommt dann nur wegen Überlast.
- Eine Wallbox ist höchstens ein Ladepunkt: Hängen nach der Zuordnung zwei Fahrzeuge an ihr, hat
  ihn das erste nach Priorität. So zählt ihr Strom nur einmal.

Die Schicht ist rein: Die Zeit kommt mit den Eingaben, auf der Uhr der Safety-Schicht
(``ChargeInputs.now``); nur ``boost_until`` und der Kalendertag des Weckkontingents gelten nach
der Wanduhr. Nicht für mehrere Threads gedacht.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from bindaems.core.control.assignment import Assignment, AssignmentKind, VehicleState
from bindaems.core.control.charge_inputs import (
    NO_VEHICLE_DATA,
    ChargeInputs,
    VehicleObs,
    WallboxObs,
)
from bindaems.core.control.charge_modes import (
    FULL_POWER,
    MODE_CODES,
    Effective,
    ModeDecision,
    ModeInput,
    ModeMachine,
    ModeTimers,
)
from bindaems.core.control.charging_tesla import (
    ASSIGNMENT_UNCLEAR,
    WAKE,
    EvcsOverrides,
    TeslaPolicy,
)
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.phase_guard import (
    PHASES,
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
from bindaems.core.control.surplus import SurplusCar, share_surplus, surplus_w
from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.config import Config, EvcsConfig, Phase, PhaseMap
from bindaems.shared.control import (
    Command,
    CommandKind,
    CommandValue,
    Intent,
    SurplusPriority,
    VehicleIntent,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.numbers import finite_number
from bindaems.shared.timeutil import ensure_utc

PV_SURPLUS: Final = "pv_surplus"  # der Wert kommt aus dem Anteil am Überschuss
OVERLOAD: Final = "overload"  # der Befehl folgt aus der Überlast
NOT_TAKEN_RETRY: Final = "not_taken_retry"  # erneut nach „nicht übernommen“
RESUME_AFTER_OUTAGE: Final = "resume_after_outage"  # die erste Startfolge nach einer Pause

_Key = tuple[str, CommandKind]  # Aktor und Art

_CURRENT: Final = CommandKind.CURRENT
_CHARGING: Final = CommandKind.CHARGING
_SURPLUS_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})  # Wunsch aus dem Anteil
# Diese Ergebnisse löschen den letzten eigenen Wert: Das Gerät hat ihn vielleicht nicht.
_FAILED: Final = frozenset({DecisionResult.ERROR, DecisionResult.TIMEOUT, DecisionResult.NOT_SENT})


@dataclass(frozen=True)
class ChargeCommand:
    """Ein Befehl der Laderegelung und seine Eingaben für das Entscheidungslog (Präzisierung 13).

    ``inputs`` nennt ``surplus_w`` (Überschuss in W), ``limit_a`` (freier Strom des Phasenwächters
    für den Ladepunkt), ``actual_current_a`` (Ist-Sollstrom des Aktors) und ``actual_charging``
    (1.0 lädt, 0.0 lädt nicht); ``None`` heißt unbekannt.
    """

    command: Command
    inputs: Mapping[str, float | None]


@dataclass(frozen=True)
class _Point:
    """Der Ladepunkt eines Fahrzeugs an seiner Wallbox in einem Zyklus (Präzisierungen 5 und 6).

    - ``actuator``: ``wallbox.<w>`` an einer EVCS, ``vehicle.<v>`` für ein Fahrzeug mit Tessie am
      Wall Connector; ``None`` für eins ohne Tessie dort, das nur Last ist
    - ``evcs``: der Ladepunkt ist eine EVCS (Überlast ``EVCS_STOP``)
    - ``phases``, ``min_a``, ``max_a``: die Netzphasen und der Strombereich
    - ``measured``: der gemessene Strom je Netzphase; ``None`` heißt unbekannt
    - ``regulated``: Der Ladepunkt wird geregelt; sonst bleibt sein Strom Last.
    """

    vehicle: str
    wallbox: str
    actuator: str | None
    evcs: bool
    phases: tuple[Phase, ...]
    min_a: int
    max_a: int
    measured: Mapping[Phase, float] | None
    regulated: bool


@dataclass(frozen=True)
class _Sent:
    """Der letzte eigene Wert: angefordert (``requested``) und nach der Safety-Schicht.

    Mit ``requested`` vergleicht die Regelung, ob sich ein Wert ändert; ``applied`` ist der Wert,
    den ein Ergebnis des Ausführers nennt (``WriteResult.value``).
    """

    requested: CommandValue
    applied: CommandValue


class _LastValues:
    """Die letzten eigenen Werte je Aktor und Art und die Merker ``not_taken_retry`` (Präz. 10).

    Gesetzt wird ein Wert durch ein Urteil mit Wert oder ``UNCHANGED`` (``judged``), gelöscht
    durch ein gescheitertes Ergebnis mit passendem Wert (``finished``) oder durch ``forget``: dort,
    wo sich das Gerät ohne die Regelung geändert haben kann.
    """

    def __init__(self) -> None:
        self._sent: dict[_Key, _Sent] = {}
        self._retry: set[_Key] = set()  # der nächste Befehl dieser Art trägt ``not_taken_retry``

    def changed(self, key: _Key, value: CommandValue) -> bool:
        """Ob ``value`` vom letzten eigenen Wert dieser Art abweicht; ohne einen immer."""
        last = self._sent.get(key)
        return last is None or last.requested != value

    def retry(self, key: _Key) -> bool:
        """Ob der nächste Befehl dieser Art ``not_taken_retry`` trägt."""
        return key in self._retry

    def judged(self, command: Command, verdict: Verdict | None, *, not_sent: bool) -> bool:
        """Das Urteil über einen eigenen Befehl (``ChargeController.note_verdict``).

        Wahr, wenn es den Wert gesetzt hat: ein Urteil mit Wert oder ``UNCHANGED``, gesendet.
        """
        key = (command.actuator, command.kind)
        if not_sent:
            self._sent.pop(key, None)
            return False
        if verdict is None or not (verdict.sendable or verdict.outcome == Outcome.UNCHANGED):
            return False
        self._sent[key] = _Sent(command.value, verdict.value)
        self._retry.discard(key)
        return True

    def finished(self, result: WriteResult) -> None:
        """Das Ergebnis eines gesendeten Befehls (``ChargeController.note_result``)."""
        key = (result.actuator, result.kind)
        last = self._sent.get(key)
        if result.result not in _FAILED or last is None or last.applied != result.value:
            return
        del self._sent[key]
        if result.not_taken:
            self._retry.add(key)

    def forget(self, actuators: Collection[str] | None = None) -> None:
        """Vergisst Werte und Merker dieser Aktoren, ohne Angabe die aller Aktoren."""
        if actuators is None:
            self._sent.clear()
            self._retry.clear()
            return
        self._sent = {key: last for key, last in self._sent.items() if key[0] not in actuators}
        self._retry = {key for key in self._retry if key[0] not in actuators}


@dataclass(frozen=True)
class _View:
    """Was ``health`` zu einem Fahrzeug aus dem letzten Zyklus zeigt."""

    mode: Effective
    state: VehicleState
    limit_a: float | None


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
    - ``cause``: woran sich das Ziel des Wächters richtet (``phase_target``)
    - ``wanted``: der Wunsch in A, beim Tesla am Wall Connector das abgerundete Mittel
    - ``limit``: die Grenze des Wächters in A, beim Tesla am Wall Connector der niedrigste freie
      Wert im Fenster; ``share``: der Anteil am Überschuss (``None`` ohne)
    - ``level``: die Überlast; ``unclear``: die Zuordnung ist unklar
    - ``resume``: die erste Startfolge nach einer Pause steht noch aus
    """

    plugged: bool | None
    charging: bool | None
    mode: Effective
    decision: ModeDecision
    cause: GuardCause
    wanted: int
    limit: int
    share: int | None
    level: OverloadLevel
    unclear: bool
    resume: bool

    @property
    def head(self) -> tuple[str, ...]:
        """Die ersten Gründe: der Modus, bei unklarer Zuordnung danach ``assignment_unclear``."""
        code = MODE_CODES[self.mode]
        return (code, ASSIGNMENT_UNCLEAR) if self.unclear else (code,)


class ChargeController:
    """Die Laderegelung: je Zyklus Befehle an EVCS und Tesla (Design 4 bis 9).

    Der Zustand lebt nur im Speicher: eine Zuordnung für die ganze Lebensdauer, je Fahrzeug eine
    ``ModeMachine`` und eine ``TeslaPolicy`` (Fenster und Weckkontingent), der Überlastwächter samt
    der Stufe des letzten Zyklus, die geregelten Ladepunkte des letzten Zyklus, die letzten eigenen
    Werte je Aktor und Art und die Fahrzeuge, deren erste Startfolge nach einer Pause aussteht.
    ``started`` ist die Wanduhr beim Start der Regelung (``Assignment``).
    """

    def __init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None:
        self._cfg = cfg
        self._limits = limits
        self._guard = GuardLimits.from_config(cfg)
        self._assignment = Assignment(cfg, started=started)
        timers = ModeTimers.from_config(cfg.control)
        self._machines = {key: ModeMachine(timers) for key in cfg.vehicles}
        self._tesla = {key: TeslaPolicy(cfg.control, v.wake) for key, v in cfg.vehicles.items()}
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
        self._resume: set[str] = set()  # Fahrzeuge, deren erste Startfolge nach ``idle`` aussteht
        self._stepped = False  # seit dem Start schon geregelt: erst danach ist ``idle`` ein Ausfall
        self._last = _LastValues()
        self._views: dict[str, _View] = {}
        self._reasons: dict[str, str] = {}  # der letzte Grund des letzten Befehls je Fahrzeug
        self._vehicle_wallbox: dict[str, str | None] = {}
        # Die Uhren des letzten ``step``: für das Weckkontingent in ``note_verdict`` und ``health``
        self._now = self._wall_now = started

    def step(self, inputs: ChargeInputs) -> list[ChargeCommand]:
        """Ein Regelzyklus; die Befehle kommen in der Reihenfolge der Priorität.

        Befehle bekommt nur ein geregelter Ladepunkt, und nur für Arten, die nicht in
        ``inputs.blocked`` stehen; je Ladepunkt kommt der Strom vor „Laden an“. Ein Aktor, der
        nicht steuerbar ist, bekommt höchstens den Weckstart; ein Fahrzeug an der EVCS bekommt
        seinen Höchststrom vor den Befehlen an die EVCS. Ablauf und Regeln stehen im Docstring des
        Moduls.
        """
        now, intent = inputs.now, inputs.intent
        self._now, self._wall_now = now, inputs.wall_now
        self._stepped = True
        state = self._assignment.update(
            now, inputs.wall_now, intent, inputs.accepted_at, inputs.wallboxes, inputs.vehicles
        )
        order = self._order(intent)
        modes = {key: _mode(key, state.vehicles[key], intent) for key in order}
        points = self._points(order, state.vehicles, inputs)
        cycle = self._allocate(order, modes, points, state.vehicles, inputs)
        regulated = {
            key: point.actuator
            for key, point in points.items()
            if point.regulated and point.actuator is not None
        }
        if cycle.level > self._level:  # jede höhere Stufe bietet ihre Reaktion einmal an
            self._last.forget(set(regulated.values()))
        self._level = cycle.level
        # Kommt ein Fahrzeug an eine EVCS, vergisst die Regelung die Werte seines Aktors: So geht
        # sein Höchststrom einmal je Ansteckvorgang hinaus (``_maximum``).
        at_the_evcs, arrived = self._overrides.update(state.vehicles)
        self._last.forget(arrived)

        commands: list[ChargeCommand] = []
        views: dict[str, _View] = {}
        for key in order:
            vehicle, point = state.vehicles[key], points.get(key)
            grant, share = cycle.grants.get(key), cycle.shares.get(key)
            free = None if grant is None else grant.free_a
            views[key] = _View(modes[key], vehicle, free)
            actuator = regulated.get(key)
            if point is None or actuator is None:  # ungeregelt: Die Maschine wartet
                if vehicle.new_session:
                    self._unseen_sessions.add(key)
                continue
            enough = _enough(point, share, free)
            decision = self._decide(now, key, actuator, vehicle, modes[key], enough)
            if point.evcs:
                wanted = cycle.wanted.get(key, 0)
                limit = point.min_a if free is None else math.floor(free)
            else:  # der Tesla am Wall Connector: Fenster (Präzisierung 12)
                wanted, limit = self._tesla[key].limits(now, cycle.wanted.get(key), free)
            situation = _Situation(
                plugged=vehicle.plugged,
                charging=vehicle.charging,
                mode=modes[key],
                decision=decision,
                cause=cycle.targets[key][1],
                wanted=wanted,
                limit=limit,
                share=share,
                level=cycle.level,
                unclear=vehicle.kind is AssignmentKind.UNCLEAR,
                resume=key in self._resume,
            )
            found = self._maximum(key, modes[key], inputs) if key in at_the_evcs else []
            found += self._regulate(now, point, actuator, situation, inputs)
            if found:
                self._reasons[key] = found[-1].reasons[-1]
            for command in found:
                facts = _facts(command.actuator, cycle.surplus, free, inputs.present)
                commands.append(ChargeCommand(command, facts))

        self._regulated = set(regulated.items())
        self._views = views
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
        ``step``.
        """
        started = command.kind is _CHARGING and command.value is True
        if self._last.judged(command, verdict, not_sent=not_sent) and started:
            self._resume -= {key for key, actor in self._regulated if actor == command.actuator}
        if command.wake and verdict is not None and verdict.sendable:
            self._tesla[self._vehicles[command.actuator]].note_wake(self._now, self._wall_now)

    def note_result(self, result: WriteResult) -> None:
        """Merkt das Ergebnis eines gesendeten Befehls (Präzisierung 10).

        ``ERROR``, ``TIMEOUT`` und ``NOT_SENT`` löschen den letzten eigenen Wert dieser Art, aber
        nur, wenn er dem Ergebnis entspricht: Befehle des Keepers mit anderem Wert bleiben ohne
        Wirkung. Trägt das Ergebnis ``not_taken``, nennt der nächste Befehl dieser Art zusätzlich
        ``not_taken_retry``.
        """
        self._last.finished(result)

    def idle(self, now: datetime) -> None:
        """Ein Zyklus ohne Regelung (Präzisierung 9): Die Regelung vergisst ihre letzten Befehle.

        Im nächsten ``step`` gilt jeder geregelte Ladepunkt als wieder geregelt: Seine Maschine
        bekommt ``resumed``. Lief die Regelung seit dem Start schon, trägt danach die erste
        Startfolge je Fahrzeug ``resume_after_outage``; Pausen vor dem ersten ``step`` (START,
        OBSERVE) sind kein Ausfall. Die Fenster des Tesla beginnen neu (Präzisierung 12), die
        Überlast mit einem neuen Wächter und der Stufe ``NONE``; die alten werteten die Lücke sonst
        als durchgehend. Die Zuordnung bleibt, damit ein beendeter Boost nicht wieder auflebt; das
        Weckkontingent bleibt ebenso. ``health`` und ``vehicle_wallbox`` sind bis zum nächsten
        ``step`` unbekannt; der letzte Grund je Fahrzeug bleibt. ``now`` ist die Zeit der
        Safety-Schicht; das Vergessen braucht sie nicht.
        """
        self._last.forget()
        self._regulated = set()
        if self._stepped:
            self._resume = set(self._cfg.vehicles)
        for tesla in self._tesla.values():
            tesla.restart()
        self._overload = OverloadGuard(self._guard)
        self._level = OverloadLevel.NONE
        self._views = {}
        self._vehicle_wallbox = {}

    def vehicle_wallbox(self) -> dict[str, str | None]:
        """Fahrzeug-Aktor → Wallbox für die Phasenvorhersage der Safety-Schicht.

        Für jedes Fahrzeug mit Tessie die Wallbox aus der Zuordnung des letzten ``step``; ``None``,
        solange sie unklar ist oder das Fahrzeug keine hat. Vor dem ersten ``step`` und nach
        ``idle`` leer: Die Wallbox ist dann unbekannt.
        """
        return dict(self._vehicle_wallbox)

    def health(self) -> list[dict[str, Any]]:
        """Je Fahrzeug, in der Reihenfolge von ``config.yaml``, der Stand des letzten ``step``.

        Die Felder: ``id``, ``name``, ``mode`` (Wert von ``Effective``), ``boost_until`` (ISO in
        UTC), ``wallbox``, ``assignment`` (Wert von ``AssignmentKind``), ``limit_a`` (freier Strom
        des Phasenwächters, nur für einen geregelten Ladepunkt), ``plugged``, ``charging``,
        ``wakes_today`` (Weckstarts am Kalendertag des letzten ``step``, Wiener Zeit) und
        ``reason``, der letzte Grund des letzten Befehls für das Fahrzeug. Ohne ``step`` seit dem
        Start oder seit ``idle`` ist alles außer ``id``, ``name``, ``wakes_today`` und ``reason``
        unbekannt (``None``).
        """
        entries: list[dict[str, Any]] = []
        for key, vehicle in self._cfg.vehicles.items():
            view = self._views.get(key)
            state = None if view is None else view.state
            boost = None if state is None or state.boost_until is None else state.boost_until
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
                    "reason": self._reasons.get(key),
                }
            )
        return entries

    def _order(self, intent: Intent | None) -> list[str]:
        """Die Fahrzeuge in der Reihenfolge ihrer Priorität.

        Das ist ``intent.priority``, wenn sie jedes Fahrzeug der Konfiguration genau einmal nennt
        und kein anderes; sonst die Reihenfolge von ``config.yaml``.
        """
        configured = list(self._cfg.vehicles)
        priority = None if intent is None else intent.priority
        if priority is not None and sorted(priority) == sorted(configured):
            return list(priority)
        return configured

    def _points(
        self, order: Sequence[str], vehicles: Mapping[str, VehicleState], inputs: ChargeInputs
    ) -> dict[str, _Point]:
        """Die Ladepunkte je Fahrzeug mit Wallbox; eine Wallbox hat das erste nach Priorität."""
        points: dict[str, _Point] = {}
        taken: set[str] = set()
        for key in order:
            wallbox = vehicles[key].wallbox
            if wallbox is None or wallbox in taken:
                continue
            taken.add(wallbox)
            points[key] = self._point(key, wallbox, vehicles[key].kind, inputs)
        return points

    def _point(
        self, key: str, wallbox_key: str, kind: AssignmentKind, inputs: ChargeInputs
    ) -> _Point:
        """Der Ladepunkt des Fahrzeugs ``key`` an der Wallbox ``wallbox_key`` (Präzisierung 6).

        Geregelt ist er, wenn sein Aktor steuerbar ist (am Wall Connector auch nur weckbar), er
        nicht pausiert (ein Fahrzeug mit Tessie, solange die Zuordnung unklar ist) und seine
        Wallbox erreichbar ist oder Tessie seinen Strom kennt.
        """
        vehicle = self._cfg.vehicles[key]
        wallbox = self._cfg.wallboxes[wallbox_key]
        seen = inputs.wallboxes[wallbox_key]
        tessie = NO_VEHICLE_DATA
        if vehicle.tessie is not None:
            tessie = inputs.vehicles.get(key, NO_VEHICLE_DATA)
        measured = seen.phase_a if seen.reachable else _tessie_current(tessie, wallbox.phase_map)
        if isinstance(wallbox, EvcsConfig):
            evcs_actuator = wallbox_actuator(wallbox_key)
            actuator: str | None = evcs_actuator
            unclear = kind is AssignmentKind.UNCLEAR  # Präzisierung 5: die ganze EVCS
            phases = wallbox.phase_map if unclear else wallbox.phase_map[: vehicle.phases]
            min_a = wallbox.min_a if unclear else max(wallbox.min_a, vehicle.min_a)
            max_a = wallbox.max_a if unclear else min(wallbox.max_a, vehicle.max_a)
            steerable = inputs.controllable.get(evcs_actuator) is True
            paused = False
        else:
            candidate = vehicle_actuator(key)
            actuator = candidate if candidate in self._limits.actuators else None
            phases = wallbox.phase_map[: vehicle.phases]
            min_a, max_a = vehicle.min_a, min(vehicle.max_a, wallbox.max_a)
            steerable = actuator is not None and (
                inputs.controllable.get(actuator) is True or inputs.wakeable.get(actuator) is True
            )
            paused = kind is AssignmentKind.UNCLEAR
        return _Point(
            vehicle=key,
            wallbox=wallbox_key,
            actuator=actuator,
            evcs=isinstance(wallbox, EvcsConfig),
            phases=phases,
            min_a=min_a,
            max_a=max_a,
            measured=measured,
            regulated=steerable and not paused and (seen.reachable or measured is not None),
        )

    def _allocate(
        self,
        order: Sequence[str],
        modes: Mapping[str, Effective],
        points: Mapping[str, _Point],
        vehicles: Mapping[str, VehicleState],
        inputs: ChargeInputs,
    ) -> _Cycle:
        """Last je Phase, Überlast, Überschuss und Zuteilung des Wächters (Präzisierungen 6 und 7).

        Abgezogen wird nur der Strom geregelter Ladepunkte; dazu kommen die Zuschläge bei
        Ausfällen. Den Überschuss teilen sich die geregelten Ladepunkte, deren Auto laden kann, in
        der Reihenfolge ``order``; in derselben Reihenfolge teilt der Wächter zu.
        """
        grid = inputs.grid
        regulated = [points[key] for key in order if key in points and points[key].regulated]
        loads = phase_loads(
            grid.phase_import_a,
            grid.battery_feed_a,
            [(point.phases, point.measured) for point in regulated],
            self._reserved(list(points.values()), inputs.wallboxes),
        )
        level = self._overload.update(inputs.now, grid.phase_import_a)
        able = [p for p in regulated if _can_charge(vehicles[p.vehicle], modes[p.vehicle])]
        intent = inputs.intent
        surplus = surplus_w(
            cars_w=_cars_w(able, inputs.wallboxes),
            grid_w=grid.grid_w,
            battery_w=grid.battery_w,
            soc_pct=grid.soc_pct,
            priority=SurplusPriority() if intent is None else intent.surplus_priority,
            margin_w=self._cfg.control.surplus_margin_w,
        )
        cars = [self._surplus_car(point, modes[point.vehicle]) for point in able]
        shares = share_surplus(surplus, cars, self._limits.voltage_v)
        wanted = {p.vehicle: _wanted(p, modes[p.vehicle], shares.get(p.vehicle)) for p in able}
        targets = {
            key: phase_target(
                self._guard, full_power=modes[key] in FULL_POWER, soc_pct=grid.soc_pct
            )
            for key in order
        }
        claims = [
            Claim(p.vehicle, p.phases, targets[p.vehicle][0], wanted.get(p.vehicle, 0), _holding(p))
            for p in regulated
        ]
        return _Cycle(surplus, shares, wanted, targets, allocate(loads, claims), level)

    def _reserved(
        self, points: Sequence[_Point], wallboxes: Mapping[str, WallboxObs]
    ) -> dict[Phase, float]:
        """Die Zuschläge bei Ausfällen je Phase, nur ohne geregelten Ladepunkt (Präzisierung 6).

        - Eine EVCS, die nicht erreichbar ist: ``safe_a`` auf allen Phasen ihrer ``phase_map``,
          auch ohne Fahrzeug an ihr.
        - Ein Fahrzeug am Wall Connector, der nicht erreichbar ist, ohne Strom aus Tessie: das
          ``max_a`` seines Ladepunkts auf allen drei Phasen. Geregelt ist dieser Ladepunkt nie.
        """
        regulated = {point.wallbox for point in points if point.regulated}
        reserved = dict.fromkeys(PHASES, 0.0)
        for key, wallbox in self._cfg.wallboxes.items():
            out = not wallboxes[key].reachable
            if out and isinstance(wallbox, EvcsConfig) and key not in regulated:
                for phase in wallbox.phase_map:
                    reserved[phase] += wallbox.safe_a
        for point in points:
            if not point.evcs and point.measured is None and not wallboxes[point.wallbox].reachable:
                for phase in PHASES:
                    reserved[phase] += point.max_a
        return reserved

    def _surplus_car(self, point: _Point, mode: Effective) -> SurplusCar:
        """Das Auto des Ladepunkts in der Verteilung; ein ladendes PV-Auto hält ``min_a``."""
        return SurplusCar(
            key=point.vehicle,
            full_power=mode in FULL_POWER,
            minimum=mode is Effective.MIN_PV,
            phases=len(point.phases),
            min_a=point.min_a,
            max_a=point.max_a,
            holding_min=mode is Effective.PV and self._machines[point.vehicle].charging,
        )

    def _decide(
        self,
        now: datetime,
        key: str,
        actuator: str,
        vehicle: VehicleState,
        mode: Effective,
        enough: bool | None,
    ) -> ModeDecision:
        """Die Maschine eines geregelten Ladepunkts; beginnt er, vergisst die Regelung ihre Werte.

        Der Ladepunkt beginnt, wenn er wieder geregelt wird (``back``), mit einem neuen
        Ansteckvorgang und wenn ein gemerkter Vorgang die Maschine erreicht, also beim ersten
        bekannten Ansteckzustand. Jedes davon geschieht einmal: Dann vergisst die Regelung die
        letzten Werte des Aktors. Mit einem Ansteckvorgang beginnen auch die Fenster des Tesla neu.
        """
        back = (key, actuator) not in self._regulated  # wieder geregelt: Übernahme
        remembered = key in self._unseen_sessions
        reached = remembered and vehicle.plugged is not None  # der gemerkte Vorgang kommt an
        if vehicle.new_session or reached:
            self._tesla[key].restart()
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
                complete=vehicle.complete,
                enough=enough,
                observed_charging=vehicle.charging,
                new_session=session,
                resumed=back and not session,  # ein neuer Ansteckvorgang geht vor
            ),
        )

    def _maximum(self, key: str, mode: Effective, inputs: ChargeInputs) -> list[Command]:
        """„Tesla an der EVCS“: der Höchststrom an das Fahrzeug selbst, sobald es steuerbar ist.

        Er geht hinaus, solange er sich gegenüber dem letzten eigenen Wert ändert; ``step``
        vergisst den je Ansteckvorgang. Grund ist der Modus.
        """
        actuator = vehicle_actuator(key)
        value = float(self._cfg.vehicles[key].max_a)
        steerable = inputs.controllable.get(actuator) is True
        if not steerable or (actuator, _CURRENT) in inputs.blocked:
            return []
        if not self._changed((actuator, _CURRENT), value):
            return []
        return [self._command(actuator, _CURRENT, value, (MODE_CODES[mode],))]

    def _regulate(
        self,
        now: datetime,
        point: _Point,
        actuator: str,
        situation: _Situation,
        inputs: ChargeInputs,
    ) -> list[Command]:
        """Überlast, Strom und Befehle für einen geregelten Ladepunkt (Präzisierungen 8 bis 12).

        Stoppt die Überlast den Ladepunkt (``ALL_STOP``, bei einer EVCS schon ``EVCS_STOP``),
        stoppt sie auch seinen Modus (``force_stop``), damit danach die Mindestpause gilt. Ab
        ``MINIMUM`` stoppt sie dringend auch ein Auto, das lädt, obwohl sein Modus aus will (etwa
        selbst gestartet), außer solange die Zuordnung unklar ist. Beides gilt auch bei unbekanntem
        Ansteckzustand. Ein Aktor, der nicht steuerbar ist (geregelt ist er dann nur, weil er
        weckbar ist), bekommt höchstens den Weckstart.
        """
        level = situation.level
        stop = level >= OverloadLevel.ALL_STOP or (point.evcs and level >= OverloadLevel.EVCS_STOP)
        charge = situation.decision.charge
        if stop and charge:
            self._machines[point.vehicle].force_stop(now)
            charge = False
        blocked = inputs.blocked
        minimum = level >= OverloadLevel.MINIMUM
        if inputs.controllable.get(actuator) is not True:
            if charge and not minimum and situation.plugged is True:
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
            return self._switch_off(actuator, off, blocked, urgent=False)
        reasons: tuple[str, ...]
        if minimum:
            value, reasons = point.min_a, (*head, OVERLOAD)
        else:
            value = min(max(min(situation.wanted, situation.limit), point.min_a), point.max_a)
            reasons = (*head, *_why(situation))
        urgent = minimum or (
            situation.cause is GuardCause.PHASE_LIMIT
            and present_a is not None
            and value < present_a
        )
        return self._charge(
            actuator,
            float(value),
            reasons,
            blocked,
            urgent=urgent,
            start=not minimum,
            resume=situation.resume,
        )

    def _wake(
        self, key: str, actuator: str, situation: _Situation, blocked: frozenset[_Key]
    ) -> list[Command]:
        """Der Start mit Weckerlaubnis (Design 7.1): ``charging=True`` mit ``wake``, ohne Strom.

        Er kommt, wenn ``charging=True`` sich ändert, nicht blockiert ist und das Weckkontingent ihn
        erlaubt. Die Gründe sind die eines Starts, danach ``wake``.
        """
        on = (actuator, _CHARGING)
        if on in blocked or not self._changed(on, True):
            return []
        if not self._tesla[key].may_wake(self._now, self._wall_now):
            return []
        resumed = (RESUME_AFTER_OUTAGE,) if situation.resume else ()
        reasons = (*situation.head, *_why(situation), *resumed, WAKE)
        return [self._command(actuator, _CHARGING, True, reasons, wake=True)]

    def _hold(
        self,
        point: _Point,
        actuator: str,
        situation: _Situation,
        present_a: float | None,
        blocked: frozenset[_Key],
    ) -> list[Command]:
        """Kein „Laden an/aus“, nur eine Senkung (Präzisierungen 4 und 5).

        So bei unbekanntem Ansteckzustand und solange die Zuordnung unklar ist: Der Strom sinkt
        nur, wenn der Wächter (oder die Überlast) weniger als den bekannten Ist-Strom erlaubt, nie
        unter ``min_a``.
        """
        if present_a is None or (actuator, _CURRENT) in blocked:
            return []
        reasons: tuple[str, ...]
        if situation.level >= OverloadLevel.MINIMUM:
            value, reasons, urgent = point.min_a, (*situation.head, OVERLOAD), True
        else:
            value = min(max(situation.limit, point.min_a), point.max_a)
            reasons = (*situation.head, situation.cause.value)
            urgent = situation.cause is GuardCause.PHASE_LIMIT
        if value >= present_a or not self._changed((actuator, _CURRENT), float(value)):
            return []
        return [self._command(actuator, _CURRENT, float(value), reasons, urgent=urgent)]

    def _charge(
        self,
        actuator: str,
        value: float,
        reasons: tuple[str, ...],
        blocked: frozenset[_Key],
        *,
        urgent: bool,
        start: bool,
        resume: bool,
    ) -> list[Command]:
        """Laden: der Strom, wenn er sich ändert; für einen Start danach ``charging=True``.

        Ein Start (``charging=True`` ändert sich) folgt erst auf den Strom: Muss der gesendet
        werden und ist er blockiert, wartet der Start. ``start`` ist falsch bei Überlast. Steht ein
        Start an und ist es die erste Startfolge nach einer Pause (``resume``), tragen Strom und
        Start zusätzlich ``resume_after_outage``.
        """
        current, on = (actuator, _CURRENT), (actuator, _CHARGING)
        send = self._changed(current, value)
        if send and current in blocked:
            return []
        starting = start and self._changed(on, True)
        if starting and resume:
            reasons = (*reasons, RESUME_AFTER_OUTAGE)
        found: list[Command] = []
        if send:
            found.append(self._command(actuator, _CURRENT, value, reasons, urgent=urgent))
        if starting and on not in blocked:
            found.append(self._command(actuator, _CHARGING, True, reasons))
        return found

    def _switch_off(
        self, actuator: str, reasons: tuple[str, ...], blocked: frozenset[_Key], *, urgent: bool
    ) -> list[Command]:
        """Nicht laden: ``charging=False``, wenn es sich ändert, ohne Strom."""
        off = (actuator, _CHARGING)
        if off in blocked or not self._changed(off, False):
            return []
        return [self._command(actuator, _CHARGING, False, reasons, urgent=urgent)]

    def _changed(self, key: _Key, value: CommandValue) -> bool:
        """Ob ``value`` vom letzten eigenen Wert dieser Art abweicht; ohne einen immer."""
        return self._last.changed(key, value)

    def _command(
        self,
        actuator: str,
        kind: CommandKind,
        value: CommandValue,
        reasons: tuple[str, ...],
        *,
        urgent: bool = False,
        wake: bool = False,
    ) -> Command:
        """Der Befehl samt ``not_taken_retry``, wenn die Art zuletzt nicht übernommen wurde."""
        retry = (NOT_TAKEN_RETRY,) if self._last.retry((actuator, kind)) else ()
        return Command(actuator, kind, value, urgent=urgent, reasons=(*reasons, *retry), wake=wake)


def _mode(key: str, vehicle: VehicleState, intent: Intent | None) -> Effective:
    """Der geltende Modus: Boost, solange er gilt; sonst der Modus der Absicht, ohne Eintrag PV."""
    if vehicle.boost_until is not None:
        return Effective.BOOST
    entry = None if intent is None else intent.vehicles.get(key)
    return Effective((VehicleIntent() if entry is None else entry).mode.value)


def _can_charge(vehicle: VehicleState, mode: Effective) -> bool:
    """Ob das Auto am Überschuss teilnimmt: nicht abgesteckt, nicht „geladen“, nicht Aus."""
    return vehicle.plugged is not False and not vehicle.complete and mode is not Effective.OFF


def _cars_w(points: Sequence[_Point], wallboxes: Mapping[str, WallboxObs]) -> float | None:
    """Die Ladeleistung an den Wallboxen dieser Ladepunkte; ``None``, wenn eine fehlt."""
    total = 0.0
    for point in points:
        power = wallboxes[point.wallbox].power_w
        if power is None:
            return None
        total += power
    return total


def _wanted(point: _Point, mode: Effective, share: int | None) -> int:
    """Der Wunsch eines Autos, das laden kann: Sofort und Boost ``max_a``, sonst der Anteil.

    PV wünscht den Anteil, ohne Wert 0; Min+PV den Anteil, mindestens ``min_a``.
    """
    if mode in FULL_POWER:
        return point.max_a
    if mode is Effective.MIN_PV:
        return max(share or 0, point.min_a)
    return share or 0


def _holding(point: _Point) -> float:
    """Der größte gemessene Strom des Ladepunkts; er bleibt für die folgenden Autos belegt."""
    return max(point.measured.values(), default=0.0) if point.measured is not None else 0.0


def _enough(point: _Point | None, share: int | None, free: float | None) -> bool | None:
    """Ob Anteil und freier Strom den Mindeststrom tragen; ``None``, wenn eins davon fehlt."""
    if point is None or share is None or free is None:
        return None
    return share >= point.min_a and free >= point.min_a


def _why(situation: _Situation) -> tuple[str, ...]:
    """Der Grund nach dem Modus-Code, wenn das Auto lädt und keine Überlast herrscht.

    ``pv_surplus``, wenn der Wert aus dem Anteil kommt (in PV oder Min+PV liegt der Wunsch nicht
    über der Grenze des Wächters) oder die Zeiten von PV-Überschuss gerade gestartet haben; sonst
    der Grund des Ziels (``GuardCause``), wenn der Wächter kappt; sonst keiner.
    """
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


def _tessie_current(tessie: VehicleObs, phase_map: PhaseMap) -> dict[Phase, float] | None:
    """Der Strom aus Tessie auf den ersten ``phases`` Phasen der ``phase_map``; sonst ``None``."""
    if not tessie.reachable or tessie.actual_current_a is None or tessie.phases is None:
        return None
    return {phase: tessie.actual_current_a for phase in phase_map[: tessie.phases]}


def _facts(
    actuator: str,
    surplus: float | None,
    free: float | None,
    present: Mapping[_Key, CommandValue],
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
