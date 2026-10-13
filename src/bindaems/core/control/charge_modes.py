"""Lademodi je Fahrzeug mit Start- und Stopp-Zeit und Mindestzeiten (Design 6.3, 6.4).

- ``Effective``, ``MODE_CODES`` und ``FULL_POWER``: der geltende Modus, sein Grund-Code im
  Entscheidungslog und die Modi mit vollem Strom
- ``ModeTimers``: die Zeiten aus ``control`` der Konfiguration
- ``ModeInput`` und ``ModeDecision``: Eingaben und Urteil eines Schritts
- ``ModeMachine``: der Zustand eines Fahrzeugs; sie entscheidet nur, ob geladen werden soll,
  nicht mit welchem Strom

Die Schicht ist rein: Die Zeit kommt als Argument, auf der Uhr der Safety-Schicht, und steigt von
Aufruf zu Aufruf.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from bindaems.shared.config import ControlConfig

_PV_SURPLUS: Final = "pv_surplus"  # Grund, wenn die Zeiten von PV-Überschuss ein Urteil ergaben


class Effective(StrEnum):
    """Der geltende Modus eines Fahrzeugs: der eingestellte, bei aktivem Boost ``BOOST``."""

    PV = "pv"
    MIN_PV = "min_pv"
    NOW = "now"
    OFF = "off"
    BOOST = "boost"


MODE_CODES: Final[Mapping[Effective, str]] = MappingProxyType(
    {
        Effective.PV: "mode_pv",
        Effective.MIN_PV: "mode_min_pv",
        Effective.NOW: "mode_now",
        Effective.OFF: "mode_off",
        Effective.BOOST: "boost",
    }
)

FULL_POWER: Final = frozenset({Effective.NOW, Effective.BOOST})  # laden mit ihrem vollen Strom


@dataclass(frozen=True)
class ModeTimers:
    """Die Zeiten der Lademodi in Sekunden (Design 6.3, 6.4).

    - ``start_after_s``: so lange muss der Überschuss für einen Start reichen
    - ``stop_after_s``: so lange liegt er darunter bis zum Stopp
    - ``min_on_s``: Mindestlaufzeit nach einem Start
    - ``min_off_s``: Mindestpause nach einem Stopp
    """

    start_after_s: float
    stop_after_s: float
    min_on_s: float
    min_off_s: float

    @classmethod
    def from_config(cls, control: ControlConfig) -> ModeTimers:
        """Liest die Zeiten aus ``control`` der Konfiguration."""
        return cls(
            start_after_s=control.start_after_s,
            stop_after_s=control.stop_after_s,
            min_on_s=control.min_on_s,
            min_off_s=control.min_off_s,
        )


@dataclass(frozen=True)
class ModeInput:
    """Die Eingaben eines Schritts für ein Fahrzeug; ``None`` heißt unbekannt.

    - ``mode``: der geltende Modus
    - ``plugged``: ob ein Fahrzeug angesteckt ist
    - ``complete``: das Auto meldet „geladen“; die Maschine merkt sich das nicht, der Aufrufer hält
      es bis zum Abstecken
    - ``enough``: Anteil und Grenze tragen den Mindeststrom
    - ``observed_charging``: ob das Auto lädt; nur der erste Schritt und ``resumed`` lesen es
    - ``new_session``: der Schritt beginnt einen neuen Ansteckvorgang
    - ``resumed``: der erste Regelzyklus nach dem Start des core oder nach einer Pause, ob des
      ganzen core oder nur dieses Ladepunkts; ``new_session`` geht ihm vor
    """

    mode: Effective
    plugged: bool | None
    complete: bool
    enough: bool | None
    observed_charging: bool | None
    new_session: bool = False
    resumed: bool = False


@dataclass(frozen=True)
class ModeDecision:
    """Das Urteil eines Schritts: ob das Fahrzeug laden soll.

    ``reason`` ist ``"pv_surplus"``, wenn die Zeiten von PV-Überschuss in diesem Schritt einen
    Start oder Stopp ergaben, sonst ``None``.
    """

    charge: bool
    reason: str | None


class ModeMachine:
    """Der Zustand eines Fahrzeugs: ob es laden soll, mit Zeiten gegen das Flattern.

    Sie merkt sich zwischen den Schritten das letzte Urteil und den Modus des letzten Schritts,
    seit wann der Überschuss reicht bzw. nicht reicht, und den Start und den letzten Stopp für
    Mindestlaufzeit und Mindestpause. Die Pause nach einem Stopp von außen (``force_stop``) gilt
    auch für Sofort, Boost und Min+PV. Der Zustand lebt nur im Speicher. Nicht für mehrere
    Threads gedacht.
    """

    def __init__(self, timers: ModeTimers) -> None:
        self._timers = timers
        self._charging = False
        self._take_over = True  # der nächste Schritt mit bekanntem Ansteckzustand ist der erste
        self._mode: Effective | None = None
        self._enough_since: datetime | None = None  # seit wann ``enough is True`` gilt
        self._short_since: datetime | None = None  # seit wann ``enough is True`` nicht gilt
        # Start und letzter Stopp: Ohne Zeitpunkt (unbekannt oder keiner gewesen) gilt die
        # Mindestlaufzeit bzw. Mindestpause als erfüllt.
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None

    @property
    def charging(self) -> bool:
        """Ob die Maschine laden will: das letzte Urteil, auch nach ``force_stop``."""
        return self._charging

    def step(self, now: datetime, inp: ModeInput) -> ModeDecision:
        """Entscheidet zum Zeitpunkt ``now``, ob das Fahrzeug laden soll (Design 6.3, 6.4).

        In jedem Schritt mit ``plugged is True`` merkt sich die Maschine zuerst, seit wann
        ``enough is True`` gilt bzw. nicht gilt, auch im ersten Schritt. Dann entscheidet die erste
        zutreffende Regel:

        1. ``plugged is False``: aus. Zeiten, letzter Modus und Pause sind vergessen; der nächste
           Ansteckvorgang beginnt frisch. Auch dieser Schritt zählt: Der nächste ist kein erster
           Schritt. Ein neuer Ansteckvorgang kommt mit ``new_session`` über Regel 4; ohne sie fehlt
           der letzte Modus, und der Schritt gilt über Regel 6 als Moduswechsel.
        2. ``plugged is None``: die letzte Entscheidung halten, vor der ersten: aus. Der Schritt
           schreibt nichts fort, auch den Modus nicht, und zählt nicht als erster: Der erste
           Schritt ist der erste mit bekanntem Ansteckzustand, und ein ``resumed`` im unbekannten
           gilt für den nächsten bekannten.
        3. ``complete``: aus.
        4. ``new_session``: PV und Aus aus, sonst an. Ein neuer Ansteckvorgang geht der Übernahme
           vor, auch im ersten Schritt und mit ``resumed``: Ein Auto, das darin selbst gestartet
           hat, beginnt in PV und Aus ohne Laden. Die Übernahme ist damit verbraucht.
        5. Erster Schritt oder ``resumed``: PV übernimmt den beobachteten Zustand
           (``observed_charging is True``), eine Mindestlaufzeit gilt als erfüllt. Aus ist aus,
           alles andere an.
        6. Moduswechsel gegenüber dem letzten Schritt, auch ohne einen letzten Modus: sofort, ohne
           Zeiten und ohne Mindestzeiten. Aus ist aus, Sofort, Boost und Min+PV sind an, PV ist an
           genau dann, wenn ``enough is True``.
        7. Gleicher Modus: Aus ist aus. Sofort, Boost und Min+PV sind an, außer innerhalb von
           ``min_off_s`` nach ``force_stop``. PV startet, wenn ``enough is True`` seit
           ``start_after_s`` ohne Unterbrechung gilt und seit dem letzten Stopp ``min_off_s``
           vergangen sind (oder es keinen gab). PV stoppt, wenn ``enough is not True`` seit
           ``stop_after_s`` gilt und seit dem Start ``min_on_s`` vergangen sind (oder er unbekannt
           ist). Nur ein solcher Start oder Stopp trägt den Grund ``"pv_surplus"``.

        Die Regeln 4 bis 6 beginnen frisch: Beide Zeitpunkte stehen auf ``now``, und Start und
        letzter Stopp sind vergessen, damit auch die Pause nach ``force_stop``.
        """
        if inp.resumed:
            self._take_over = True
        if inp.plugged is None:
            return ModeDecision(self._charging, None)
        take_over, self._take_over = self._take_over, False
        if inp.plugged is False:
            self._reset(None, None, charging=False)
            return ModeDecision(False, None)

        enough = inp.enough is True
        self._enough_since = _since(self._enough_since, enough, now)
        self._short_since = _since(self._short_since, not enough, now)
        if inp.complete:
            self._charging = False
            return ModeDecision(False, None)

        if inp.new_session:
            return self._begin(now, inp.mode, inp.mode not in (Effective.PV, Effective.OFF))
        if take_over:
            return self._begin(now, inp.mode, _takes_over(inp))
        if inp.mode is not self._mode:
            return self._begin(now, inp.mode, _follows_mode(inp))
        return self._continue(now, inp.mode)

    def force_stop(self, now: datetime) -> None:
        """Stoppt von außen, etwa bei Überlast: aus, und der letzte Stopp ist ``now``.

        Die Mindestpause ``min_off_s`` gilt danach auch für Sofort, Boost und Min+PV (Präzisierung
        8), bis ein Moduswechsel oder das Abstecken sie aufhebt. Der Stopp ist unbedingt: Er hebt
        die Mindestlaufzeit auf.
        """
        self._charging = False
        self._started_at = None
        self._stopped_at = now

    def _begin(self, now: datetime, mode: Effective, charge: bool) -> ModeDecision:
        """Beginnt frisch (Regeln 4 bis 6): ``mode`` ist der letzte Modus, ``charge`` das Urteil."""
        self._reset(mode, now, charging=charge)
        return ModeDecision(charge, None)

    def _reset(self, mode: Effective | None, since: datetime | None, *, charging: bool) -> None:
        """Vergisst Start und Stopp samt Pause; beide Zeitpunkte stehen danach auf ``since``."""
        self._mode = mode
        self._enough_since = self._short_since = since
        self._started_at = self._stopped_at = None
        self._charging = charging

    def _continue(self, now: datetime, mode: Effective) -> ModeDecision:
        """Regel 7: Der Modus ist derselbe wie im letzten Schritt."""
        if mode is Effective.PV:
            return self._pv(now)
        # Aus ist aus; die übrigen laden, außer innerhalb der Pause nach ``force_stop``. Nur PV
        # stoppt durch die Zeiten, und jeder Moduswechsel vergisst den Stopp: Hier stammt er
        # immer von ``force_stop``.
        paused = not _passed(self._stopped_at, now, self._timers.min_off_s)
        self._charging = mode is not Effective.OFF and not paused
        return ModeDecision(self._charging, None)

    def _pv(self, now: datetime) -> ModeDecision:
        """Start und Stopp von PV-Überschuss nach den Zeiten (Regel 7)."""
        if not self._charging and self._may_start(now):
            self._charging = True
            self._started_at = now
            return ModeDecision(True, _PV_SURPLUS)
        if self._charging and self._may_stop(now):
            self._charging = False
            self._stopped_at = now
            return ModeDecision(False, _PV_SURPLUS)
        return ModeDecision(self._charging, None)

    def _may_start(self, now: datetime) -> bool:
        """Der Überschuss reicht seit ``start_after_s``, und die Mindestpause ist um."""
        timers = self._timers
        reached = _lasted(self._enough_since, now, timers.start_after_s)
        return reached and _passed(self._stopped_at, now, timers.min_off_s)

    def _may_stop(self, now: datetime) -> bool:
        """Der Überschuss fehlt seit ``stop_after_s``, und die Mindestlaufzeit ist um."""
        timers = self._timers
        reached = _lasted(self._short_since, now, timers.stop_after_s)
        return reached and _passed(self._started_at, now, timers.min_on_s)


def _takes_over(inp: ModeInput) -> bool:
    """Regel 5: PV übernimmt den beobachteten Zustand, Aus ist aus, alles andere an."""
    if inp.mode is Effective.PV:
        return inp.observed_charging is True
    return inp.mode is not Effective.OFF


def _follows_mode(inp: ModeInput) -> bool:
    """Regel 6: Aus ist aus, PV lädt bei genug Überschuss, alles andere an."""
    if inp.mode is Effective.PV:
        return inp.enough is True
    return inp.mode is not Effective.OFF


def _since(since: datetime | None, holds: bool, now: datetime) -> datetime | None:
    """Seit wann eine Bedingung ununterbrochen gilt: ``since`` bleibt, solange sie gilt."""
    if not holds:
        return None
    return now if since is None else since


def _lasted(since: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob etwas seit ``since`` mindestens ``limit_s`` Sekunden gilt; ohne Zeitpunkt nein."""
    return since is not None and (now - since).total_seconds() >= limit_s


def _passed(since: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob seit ``since`` mindestens ``limit_s`` Sekunden vergangen sind; ohne Zeitpunkt ja."""
    return since is None or (now - since).total_seconds() >= limit_s
