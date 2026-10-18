"""Lademodi je Fahrzeug mit Start- und Stopp-Zeit und Mindestzeiten (Design 6.3, 6.4).

- ``Effective``, ``MODE_CODES``, ``FULL_POWER`` und ``PV_SURPLUS``: der geltende Modus, sein
  Grund-Code im Entscheidungslog, die Modi mit vollem Strom und der Grund-Code ``pv_surplus``
- ``PLAN_POWER``, ``LATEST_START`` und ``TARGET_REACHED``: die Grund-Codes von „Geplant“
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

from bindaems.core.control.timing import held_since, lasted
from bindaems.shared.config import ControlConfig
from bindaems.shared.control import ChargeMode

# Grund-Code: Der Wert kommt aus dem Anteil am Überschuss, oder die Zeiten von PV-Überschuss
# haben gerade einen Start oder Stopp ergeben.
PV_SURPLUS: Final = "pv_surplus"
# Grund-Codes von „Geplant“ (Plan 4b, Präzisierung 7): Der Strom folgt der Leistung des Plan-Slots,
# die späteste Startzeit ist erreicht, der Ziel-SOC ist erreicht.
PLAN_POWER: Final = "plan_power"
LATEST_START: Final = "latest_start"
TARGET_REACHED: Final = "target_reached"


class Effective(StrEnum):
    """Der geltende Modus eines Fahrzeugs: der eingestellte, bei aktivem Boost ``BOOST``."""

    PV = "pv"
    MIN_PV = "min_pv"
    NOW = "now"
    OFF = "off"
    PLANNED = "planned"
    BOOST = "boost"


def effective_of(mode: ChargeMode) -> Effective:
    """Der geltende Modus zu einem eingestellten Lademodus (ohne Boost)."""
    return Effective(mode.value)


MODE_CODES: Final[Mapping[Effective, str]] = MappingProxyType(
    {
        Effective.PV: "mode_pv",
        Effective.MIN_PV: "mode_min_pv",
        Effective.NOW: "mode_now",
        Effective.OFF: "mode_off",
        Effective.PLANNED: "mode_planned",
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
    - ``firm``: In „Geplant“ hat das Auto einen festen Bedarf (Leistung des Plan-Slots oder
      späteste Startzeit erreicht); dann gilt es wie Sofort, sonst wie PV (Plan 4b,
      Präzisierung 7). Andere Modi lesen es nicht.
    """

    mode: Effective
    plugged: bool | None
    complete: bool
    enough: bool | None
    observed_charging: bool | None
    new_session: bool = False
    resumed: bool = False
    firm: bool = False


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
        # Der letzte Stopp kam von ``force_stop``: Nur dann hält seine Pause auch Sofort, Boost,
        # Min+PV und „Geplant“ mit festem Bedarf zurück.
        self._forced = False

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

        „Geplant“ gilt in den Regeln 4 bis 7 ohne festen Bedarf (``firm``) wie PV, mit festem
        Bedarf wie Sofort (Plan 4b, Präzisierung 7). Ein Wechsel von ``firm`` ist kein
        Moduswechsel: Kommt der feste Bedarf, startet das Auto sofort, auch in der Pause nach
        einem Stopp nach den Zeiten von PV, nur nicht in der nach ``force_stop`` (Ruling R6); der
        Start zählt für die Mindestlaufzeit. Fällt er weg, stoppt es nach den Zeiten von PV.
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
        self._enough_since = held_since(self._enough_since, enough, now)
        self._short_since = held_since(self._short_since, not enough, now)
        if inp.complete:
            self._charging = False
            return ModeDecision(False, None)

        rule = _rule_mode(inp)
        if inp.new_session:
            return self._begin(now, inp, rule not in (Effective.PV, Effective.OFF))
        if take_over:
            return self._begin(now, inp, _takes_over(inp), taken_over=True)
        if inp.mode is not self._mode:
            return self._begin(now, inp, _follows_mode(inp))
        return self._continue(now, rule)

    def force_stop(self, now: datetime) -> None:
        """Stoppt von außen, etwa bei Überlast: aus, und der letzte Stopp ist ``now``.

        Die Mindestpause ``min_off_s`` gilt danach auch für Sofort, Boost und Min+PV (Präzisierung
        8), bis ein Moduswechsel oder das Abstecken sie aufhebt. Der Stopp ist unbedingt: Er hebt
        die Mindestlaufzeit auf.
        """
        self._charging = False
        self._started_at = None
        self._stopped_at = now
        self._forced = True

    def _begin(
        self, now: datetime, inp: ModeInput, charge: bool, *, taken_over: bool = False
    ) -> ModeDecision:
        """Beginnt frisch (Regeln 4 bis 6): ``inp.mode`` wird der letzte Modus, ``charge`` ist das
        Urteil. Startet „Geplant“ hier mit festem Bedarf, zählt der Start für die Mindestlaufzeit,
        außer bei der Übernahme (Regel 5)."""
        self._reset(inp.mode, now, charging=charge)
        if charge and inp.mode is Effective.PLANNED and inp.firm and not taken_over:
            self._started_at = now
        return ModeDecision(charge, None)

    def _reset(self, mode: Effective | None, since: datetime | None, *, charging: bool) -> None:
        """Vergisst Start und Stopp samt Pause; beide Zeitpunkte stehen danach auf ``since``."""
        self._mode = mode
        self._enough_since = self._short_since = since
        self._started_at = self._stopped_at = None
        self._forced = False
        self._charging = charging

    def _continue(self, now: datetime, mode: Effective) -> ModeDecision:
        """Regel 7: Der Modus ist derselbe wie im letzten Schritt; ``mode`` nach ``_rule_mode``."""
        if mode is Effective.PV:
            return self._pv(now)
        # Aus ist aus; die übrigen laden, außer innerhalb der Pause nach ``force_stop``. Jeder
        # Moduswechsel vergisst den Stopp. Ein Stopp nach den Zeiten von PV kann hier nur aus
        # „Geplant“ ohne festen Bedarf stammen: Setzt der feste Bedarf ein, gilt das wie ein
        # Wechsel auf Sofort, und seine Pause verfällt (Ruling R6).
        paused = self._forced and not _passed(self._stopped_at, now, self._timers.min_off_s)
        charging = mode is not Effective.OFF and not paused
        if charging and not self._charging:
            self._started_at = now  # für die Mindestlaufzeit, wenn „Geplant“ wieder wie PV gilt
        self._charging = charging
        return ModeDecision(self._charging, None)

    def _pv(self, now: datetime) -> ModeDecision:
        """Start und Stopp von PV-Überschuss nach den Zeiten (Regel 7)."""
        if not self._charging and self._may_start(now):
            self._charging = True
            self._started_at = now
            return ModeDecision(True, PV_SURPLUS)
        if self._charging and self._may_stop(now):
            self._charging = False
            self._stopped_at = now
            self._forced = False
            return ModeDecision(False, PV_SURPLUS)
        return ModeDecision(self._charging, None)

    def _may_start(self, now: datetime) -> bool:
        """Der Überschuss reicht seit ``start_after_s``, und die Mindestpause ist um."""
        timers = self._timers
        reached = lasted(self._enough_since, now, timers.start_after_s)
        return reached and _passed(self._stopped_at, now, timers.min_off_s)

    def _may_stop(self, now: datetime) -> bool:
        """Der Überschuss fehlt seit ``stop_after_s``, und die Mindestlaufzeit ist um."""
        timers = self._timers
        reached = lasted(self._short_since, now, timers.stop_after_s)
        return reached and _passed(self._started_at, now, timers.min_on_s)


def _rule_mode(inp: ModeInput) -> Effective:
    """Der Modus, nach dem die Regeln 4 bis 7 entscheiden: „Geplant“ ohne festen Bedarf wie PV,
    mit festem Bedarf wie Sofort; jeder andere Modus nach sich selbst."""
    if inp.mode is Effective.PLANNED:
        return Effective.NOW if inp.firm else Effective.PV
    return inp.mode


def _takes_over(inp: ModeInput) -> bool:
    """Regel 5: PV übernimmt den beobachteten Zustand, Aus ist aus, alles andere an."""
    mode = _rule_mode(inp)
    if mode is Effective.PV:
        return inp.observed_charging is True
    return mode is not Effective.OFF


def _follows_mode(inp: ModeInput) -> bool:
    """Regel 6: Aus ist aus, PV lädt bei genug Überschuss, alles andere an."""
    mode = _rule_mode(inp)
    if mode is Effective.PV:
        return inp.enough is True
    return mode is not Effective.OFF


def _passed(since: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob seit ``since`` mindestens ``limit_s`` Sekunden vergangen sind; ohne Zeitpunkt ja."""
    return since is None or (now - since).total_seconds() >= limit_s
