"""Akku-Sollwert: Fehlbetrag der Autos, Unterstützung, erlaubter Akku-Anteil und Override.

Plan 2c-2a, Design 5.2 bis 5.6, Präzisierungen 4 bis 8 und 12. Rechnungen mit Leistungen in W:

- ``BatteryCar``: ein Auto für den Akku-Sollwert mit Wallbox, geltendem Modus und Leistung
- ``deficits``: der Fehlbetrag je Auto, nachdem die PV das Haus und die Autos davor gedeckt hat
- ``Support``: der Stand der Unterstützung mit SOC-Hysterese und Dauer der Überbrückung
- ``allowed``: der Anteil, den der Akku je Auto liefern darf
- ``BatteryNeed``: der Bedarf eines Zyklus und daraus der gewollte Netzbezug ``W``
- ``BatteryController``: der Override an Victron (``grid_setpoint``) aus dem Bedarf, mit Totband,
  Auffrischen, Sperren und Health

Der Fehlbetrag eines Autos ist die Leistung, die es aus Akku oder Netz bezieht. Der Akku liefert
davon den erlaubten Anteil; was darüber hinausgeht, ``max(0, Fehlbetrag − erlaubt)``, kommt aus dem
Netz. Die Hauslast deckt der Akku immer.

Ein fehlender Wert ist ``None`` (unbekannt), ebenso ein Wert, der keine endliche Zahl ist
(``finite_number``). Die Schicht ist rein: Die Zeit kommt als Argument, auf der Uhr der
Safety-Schicht, und steigt von Aufruf zu Aufruf.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.executor import WriteResult
from bindaems.core.control.last_values import ControlCommand, Key, LastValues
from bindaems.core.control.phase_guard import GuardCause
from bindaems.core.control.timing import held_since, lasted
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config, ControlConfig
from bindaems.shared.control import VICTRON, Command, CommandKind, CommandValue
from bindaems.shared.numbers import finite_number

BRIDGE_END_S: Final = 60.0  # so lange ohne Fehlbetrag, dann ist die Überbrückung beendet

# Gründe des Overrides (Design 9); hinter ihnen ``peak_guard``, wenn das Budget des Wächters senkt
BATTERY_COMP: Final = "battery_comp"  # der Fehlbetrag der Autos geht aufs Netz statt aus dem Akku
BATTERY_SUPPORT: Final = "battery_support"  # nur der Teil über dem erlaubten Anteil geht aufs Netz

SupportReason = Literal["on", "soc", "time", "mode"]  # der Stand der Unterstützung (Health)

# Diese Modi bekommen Akkuleistung, wenn die Unterstützung gilt (Design 5.2).
SUPPORT_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})

_SETPOINT: Final[Key] = (VICTRON, CommandKind.GRID_SETPOINT)  # der Override

_State = Literal["ok", "unknown", "locked"]  # der Stand eines Zyklus mit ``decide`` (Health)


@dataclass(frozen=True)
class BatteryCar:
    """Ein Auto für den Akku-Sollwert (Präzisierung 4); ``None`` heißt unbekannt.

    - ``wallbox``: die Wallbox, an der das Auto lädt; ihr Name ist der Schlüssel von Fehlbetrag
      und erlaubtem Anteil und in einer Liste von Autos eindeutig
    - ``mode``: der geltende Modus des Fahrzeugs, ohne Fahrzeug der des Gastes
    - ``power_w``: die Ladeleistung des Autos in W
    - ``charging``: ob das Auto lädt; ohne Leistung entscheidet das, ob der Bedarf unbekannt ist
    """

    wallbox: str
    mode: Effective
    power_w: float | None
    charging: bool | None


def deficits(
    pv_w: float | None, house_w: float | None, cars: Sequence[BatteryCar]
) -> dict[str, float] | None:
    """Der Fehlbetrag je Wallbox in der Reihenfolge von ``cars`` (Präzisierung 4).

    Die PV deckt zuerst die Hauslast, dann die Autos in der Reihenfolge von ``cars`` (der
    Priorität), wie ``allocate`` der Abrechnung. Was einem Auto danach fehlt, ist sein Fehlbetrag.
    Negative PV, Hauslast und Leistung zählen als 0.

    Der gewollte Netzbezug ist unbekannt, und das Ergebnis ist ``None``,

    - wenn PV oder Hauslast fehlen oder keine endliche Zahl sind, auch ohne Autos,
    - wenn die Leistung eines Autos fehlt und es nicht sicher nicht lädt (``charging`` ist nicht
      ``False``): Seine Leistung bestimmt auch, was für die Autos hinter ihm von der PV bleibt.

    Ein Auto, das sicher nicht lädt, zählt ohne Leistung mit 0 W.
    """
    pv, house = finite_number(pv_w), finite_number(house_w)
    if pv is None or house is None:
        return None
    rest_w = max(max(pv, 0.0) - max(house, 0.0), 0.0)  # was nach dem Haus von der PV bleibt
    found: dict[str, float] = {}
    for car in cars:
        power_w = _power_w(car)
        if power_w is None:
            return None
        covered_w = min(rest_w, power_w)
        found[car.wallbox] = power_w - covered_w
        rest_w -= covered_w
    return found


def _power_w(car: BatteryCar) -> float | None:
    """Die Leistung des Autos in W, nie negativ; ``None``, wenn sie unbekannt ist."""
    power_w = finite_number(car.power_w)
    if power_w is None:
        return 0.0 if car.charging is False else None
    return max(power_w, 0.0)


class Support:
    """Der Stand der Unterstützung: SOC-Hysterese und Überbrückung (Präzisierung 5).

    Die Unterstützung ist die Akkuleistung für Autos in PV-Überschuss und Min+PV
    (``SUPPORT_MODES``). Sie gilt, wenn ein Auto in einem dieser Modi ist und

    - der SOC sie erlaubt: Sie wird möglich, wenn er ``support_soc_pct`` erreicht, und gesperrt,
      wenn er unter ``support_soc_pct − support_hysteresis_pct`` fällt; dazwischen bleibt der
      Stand. Ein unbekannter SOC sperrt sie im Zyklus, ändert den Stand aber nicht.
    - die Überbrückung noch keine ``support_max_s`` dauert. Sie beginnt mit dem ersten Zyklus mit
      Fehlbetrag und endet, wenn ``BRIDGE_END_S`` lang keiner mehr da war; ab ``support_max_s``
      bleibt die Unterstützung gesperrt, bis sie endet. Einen Fehlbetrag haben die Autos in
      PV-Überschuss und Min+PV, wenn er zusammen mindestens ``setpoint_deadband_w / 2`` ist (und
      über 0): Darunter entsteht ohnehin kein Override, und das Rauschen der Hauslast soll keine
      Überbrückung beginnen oder verlängern.

    Der Stand für Health ist ``"on"`` oder der Grund der Sperre in dieser Reihenfolge: ``"mode"``
    (kein Auto in PV-Überschuss oder Min+PV), ``"soc"``, ``"time"``. SOC-Stand und Überbrückung
    laufen in jedem Zyklus mit, auch wenn ``"mode"`` der Grund ist. Nach einer Pause beginnt der
    Stand mit ``reset`` neu. Der Zustand lebt nur im Speicher. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, control: ControlConfig) -> None:
        self._start_pct = control.support_soc_pct
        self._stop_pct = control.support_soc_pct - control.support_hysteresis_pct
        self._max_s = control.support_max_s
        self._threshold_w = control.setpoint_deadband_w / 2
        self.reset()

    def reset(self) -> None:
        """Beginnt neu, etwa nach einer Pause: Der SOC muss die Schwelle wieder erreichen."""
        self._soc_ok = False  # der SOC hat die Schwelle erreicht, die Hysterese nicht verlassen
        self._bridging_since: datetime | None = None  # seit wann die Überbrückung läuft
        self._clear_since: datetime | None = None  # seit wann es keinen Fehlbetrag gibt

    def update(
        self,
        now: datetime,
        soc_pct: float | None,
        cars: Sequence[BatteryCar],
        deficit: Mapping[str, float],
    ) -> tuple[SupportReason, float | None]:
        """Rechnet einen Zyklus zum Zeitpunkt ``now``.

        Das Ergebnis ist der Stand der Unterstützung und die Sekunden der laufenden Überbrückung
        (``None`` ohne). ``deficit`` ist der Fehlbetrag je Wallbox; ein Auto ohne Eintrag hat
        keinen. ``now`` steigt von Aufruf zu Aufruf (die Uhr der Safety-Schicht).
        """
        soc = finite_number(soc_pct)
        self._follow_soc(soc)
        supported = [car for car in cars if car.mode in SUPPORT_MODES]
        deficit_w = sum(deficit.get(car.wallbox, 0.0) for car in supported)
        self._follow_bridging(now, deficit_w > 0.0 and deficit_w >= self._threshold_w)
        since = self._bridging_since
        bridging_s = None if since is None else (now - since).total_seconds()

        if not supported:
            return "mode", bridging_s
        if soc is None or not self._soc_ok:
            return "soc", bridging_s
        if bridging_s is not None and bridging_s >= self._max_s:
            return "time", bridging_s
        return "on", bridging_s

    def _follow_soc(self, soc: float | None) -> None:
        """Die Hysterese: Der Stand wechselt nur an den Schwellen; unbekannt ändert nichts."""
        if soc is None:
            return
        if soc >= self._start_pct:
            self._soc_ok = True
        elif soc < self._stop_pct:
            self._soc_ok = False

    def _follow_bridging(self, now: datetime, has_deficit: bool) -> None:
        """Beginnt die Überbrückung mit dem ersten Fehlbetrag und beendet sie nach 60 s ohne."""
        self._clear_since = held_since(self._clear_since, not has_deficit, now)
        if self._bridging_since is not None and lasted(self._clear_since, now, BRIDGE_END_S):
            self._bridging_since = None
        if has_deficit and self._bridging_since is None:
            self._bridging_since = now


def allowed(
    cars: Sequence[BatteryCar],
    deficit: Mapping[str, float],
    *,
    support_on: bool,
    support_max_w: float,
) -> dict[str, float]:
    """Der Anteil des Fehlbetrags, den der Akku je Wallbox liefern darf (Präzisierung 5).

    In der Reihenfolge von ``cars`` (der Priorität) bekommt

    - ein Auto in PV-Überschuss oder Min+PV bei ``support_on`` ``min(Fehlbetrag, Rest)``, wobei der
      Rest ``support_max_w`` ist, vermindert um das, was die Autos davor in diesen Modi bekamen;
      ohne ``support_on`` nichts,
    - ein Auto in Boost seinen ganzen Fehlbetrag; er geht nicht vom Rest ab,
    - ein Auto in Sofort oder Aus nichts.

    Das Ergebnis nennt jede Wallbox von ``cars``; ein Auto ohne Eintrag in ``deficit`` hat keinen
    Fehlbetrag. Fehlbeträge und ``support_max_w`` sind nicht negativ.
    """
    rest_w = support_max_w
    shares: dict[str, float] = {}
    for car in cars:
        wanted_w = deficit.get(car.wallbox, 0.0)
        if car.mode is Effective.BOOST:
            share_w = wanted_w
        elif car.mode in SUPPORT_MODES and support_on:
            share_w = min(wanted_w, rest_w)
            rest_w -= share_w
        else:
            share_w = 0.0
        shares[car.wallbox] = share_w
    return shares


@dataclass(frozen=True)
class BatteryNeed:
    """Der Bedarf eines Zyklus für den Akku-Sollwert (Design 5.2, 5.3); Leistungen in W.

    - ``pv_w``, ``house_w``: PV und Hauslast, wie gemessen
    - ``cars_w``: die Ladeleistung der Autos zusammen, wie der Fehlbetrag sie zählt
    - ``deficit_w``: der Fehlbetrag je Wallbox (``deficits``)
    - ``allowed_w``: der Anteil, den der Akku je Wallbox liefern darf (``allowed``); eine Wallbox
      ohne Eintrag bekommt nichts
    - ``support``: der Stand der Unterstützung, ``bridging_s`` die Sekunden der laufenden
      Überbrückung (``Support``)
    """

    pv_w: float
    house_w: float
    cars_w: float
    deficit_w: Mapping[str, float]
    allowed_w: Mapping[str, float]
    support: SupportReason
    bridging_s: float | None

    @property
    def grid_w(self) -> dict[str, float]:
        """Was je Wallbox aus dem Netz kommen soll: ``max(0, Fehlbetrag − erlaubt)``."""
        return {
            wallbox: max(0.0, deficit_w - self.allowed_w.get(wallbox, 0.0))
            for wallbox, deficit_w in self.deficit_w.items()
        }

    @property
    def wanted_w(self) -> float:
        """Der gewollte Netzbezug ``W``: die Summe von ``grid_w`` (Design 5.3, Punkt 1)."""
        return sum(self.grid_w.values(), 0.0)


@dataclass(frozen=True)
class _Cycle:
    """Der Stand des letzten ``decide``.

    - ``now``: die Zeit des Zyklus auf der Uhr der Safety-Schicht; sie gilt auch für die Urteile
      über seine Befehle
    - ``need``: der Bedarf; ``None``, wenn er unbekannt ist
    - ``target_w``: der gewollte Netzbezug nach dem Wächter (``W'``); ``None``, wenn er
      unbekannt ist
    - ``state``: der Stand für Health
    """

    now: datetime
    need: BatteryNeed | None
    target_w: float | None
    state: _State


class BatteryController:
    """Der Akku-Sollwert: je Zyklus höchstens ein Befehl an den Override von Victron.

    Der Override ist der gewollte Netzbezug (Design 5.1): ESS regelt den Netzbezug darauf, alles
    andere gleicht der Akku aus. ``assess`` rechnet den Bedarf eines Zyklus, ``decide`` daraus den
    Befehl (Präzisierungen 6 bis 8 und 12):

    - **Ziel:** ``T = round10(Basis + W')`` mit ``round10(x) = 10 · floor(x / 10 + 0,5)`` und der
      Basis ``ess.grid_setpoint_w``. ``W'`` ist ``W`` nach dem Budget des 15-min-Wächters:
      ``max(0, min(W, Budget − Basis))``, ohne Budget ``W``.
    - **An, aus und Änderung** gegenüber dem letzten eigenen Wert: Ist er eine Zahl, heißt ``W'``
      unter ``setpoint_deadband_w / 2`` „kein Override“ (``None``); sonst gilt ``T``, wenn es
      mindestens ``setpoint_deadband_w`` von ihm abweicht. Ist er „kein Override“ oder gibt es
      keinen, gilt ``T`` erst ab ``W' ≥ setpoint_deadband_w``.
    - **„Kein Override“** geht nur hinaus, wenn der letzte eigene Wert eine Zahl ist oder, ohne
      eigenen Wert, wenn der Cerbo einen Override meldet. Sonst gäbe es nach jedem Start und jedem
      ``forget`` eine Entscheidung „aufheben“ für nichts.
    - **Auffrischen:** Ist der letzte eigene Wert eine Zahl und steht kein neuer Wert an, geht er
      ``override_refresh_s`` nach dem letzten Urteil mit Wert über einen eigenen Override erneut
      hinaus, mit ``refresh`` und den Gründen dieses Zyklus. Fehlt ein solches Urteil, ist es
      sofort fällig: Kam der Wert nur mit ``UNCHANGED`` zurück, etwa nach einem Neustart, nach
      ``forget`` oder nach einem gescheiterten Schreiben, ist unbekannt, wann der Cerbo ihn
      zuletzt bekam.
    - **Unbekanntes Ziel** (Bedarf, Basis oder ``W'`` unbekannt): kein Befehl, auch kein
      Auffrischen. Ein Override verfällt so nach 300 s (Design 5.2).
    - **Gesperrt oder nicht steuerbar** (Victron): kein Befehl, und der Akku-Sollwert vergisst seine
      Werte; Bedarf, Unterstützung und Ziel rechnet er für Health weiter. Ein offener
      Schreibvorgang oder eine Übergabe (``blocked``) hält nur den Befehl dieses Zyklus zurück.
    - **Gründe:** ``battery_support``, wenn der erlaubte Anteil zusammen über 0 liegt, sonst
      ``battery_comp``; dahinter ``peak_guard``, wenn das Budget ``W`` gesenkt hat.

    Kein Befehl ist dringend. Der Zustand lebt nur im Speicher: die letzten eigenen Werte, die
    Unterstützung, die Zeit des letzten Urteils mit Wert über einen eigenen Override und der Stand
    des letzten ``decide``. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, cfg: Config) -> None:
        control = cfg.control
        self._deadband_w = control.setpoint_deadband_w
        self._refresh_s = control.override_refresh_s
        self._support_max_w = control.support_max_w
        self._support = Support(control)
        self._last = LastValues()
        # das letzte Urteil mit Wert über den eigenen Override; ``None`` ohne einen oder ohne Urteil
        self._refreshed: datetime | None = None
        self._cycle: _Cycle | None = None  # ``None`` vor dem ersten ``decide`` und nach ``idle``

    def assess(
        self,
        now: datetime,
        soc_pct: float | None,
        pv_w: float | None,
        house_w: float | None,
        cars: Sequence[BatteryCar],
    ) -> BatteryNeed | None:
        """Der Bedarf dieses Zyklus: Fehlbetrag, Unterstützung und erlaubter Anteil.

        ``cars`` steht in der Reihenfolge von Präzisierung 4. ``None``, wenn PV oder Hauslast
        unbekannt sind oder ``deficits`` es ist; die Unterstützung bleibt dann unberührt. ``now``
        steigt von Aufruf zu Aufruf (die Uhr der Safety-Schicht).
        """
        pv, house = finite_number(pv_w), finite_number(house_w)
        if pv is None or house is None:
            return None
        deficit = deficits(pv, house, cars)
        if deficit is None:
            return None
        support, bridging_s = self._support.update(now, soc_pct, cars, deficit)
        shares = allowed(
            cars, deficit, support_on=support == "on", support_max_w=self._support_max_w
        )
        # Jede Leistung ist bekannt oder, weil das Auto sicher nicht lädt, 0 W (``deficits``).
        cars_w = sum((_power_w(car) or 0.0 for car in cars), 0.0)
        return BatteryNeed(pv, house, cars_w, deficit, shares, support, bridging_s)

    def decide(
        self,
        now: datetime,
        need: BatteryNeed | None,
        *,
        base_w: float | None,
        budget_w: float | None,
        present: Mapping[Key, CommandValue],
        controllable: bool,
        locked: bool,
        blocked: bool,
    ) -> list[ControlCommand]:
        """Der Befehl dieses Zyklus an den Override, höchstens einer (Regeln: Docstring der Klasse).

        - ``need``: der Bedarf aus ``assess``; ``None``, wenn er unbekannt ist
        - ``base_w``: der Basis-Sollwert ``ess.grid_setpoint_w``; ``None`` heißt unbekannt
        - ``budget_w``: das Budget des 15-min-Wächters (``PeakLimits.budget_w``); ``None`` heißt
          keins
        - ``present``: der Ist-Wert je Aktor und Art; beim Override heißt ``None`` „kein
          Override“, ein fehlender Eintrag unbekannt
        - ``controllable``: Victron ist steuerbar; ``locked``: Victron ist gesperrt
        - ``blocked``: Für den Override ist ein Schreibvorgang offen, oder Victron ist in Übergabe.

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht. Die Eingaben jedes Befehls für das
        Entscheidungslog nennt ``_inputs``.
        """
        base = finite_number(base_w)
        wanted_w = None if need is None else finite_number(need.wanted_w)
        guarded = None if wanted_w is None else _guarded(wanted_w, base, budget_w)
        shown_w = None if guarded is None else guarded[0]  # ``W'`` für Health
        if locked or not controllable:  # nichts senden und die Werte vergessen (Präzisierung 8)
            self._cycle = _Cycle(now, need, shown_w, "locked")
            self._last.forget()
            self._refreshed = None
            return []
        if need is None or base is None or guarded is None:  # das Ziel ist unbekannt
            self._cycle = _Cycle(now, need, shown_w, "unknown")
            return []
        self._cycle = _Cycle(now, need, shown_w, "ok")
        if blocked:
            return []
        target_w, lowered = guarded
        reasons = _reasons(need, lowered)
        command = self._command(now, target_w, _round10(base + target_w), reasons, present)
        return (
            [] if command is None else [ControlCommand(command, _inputs(need, target_w, present))]
        )

    def note_verdict(
        self, command: Command, verdict: Verdict | None, *, not_sent: bool = False
    ) -> None:
        """Merkt das Urteil der Safety-Schicht über einen eigenen Befehl (``LastValues.judged``).

        Ein Urteil mit Wert und ``UNCHANGED`` setzen den letzten eigenen Wert, ``not_sent`` löscht
        ihn. ``DEFERRED``, ``REJECTED`` und ``None`` ändern nichts: Der Akku-Sollwert bietet den
        Befehl im nächsten Zyklus wieder an, auch ein Auffrischen. Ein Urteil mit Wert
        (``sendable``, ohne ``not_sent``), live wie im Dry-Run, merkt die Zeit des letzten
        ``decide`` fürs Auffrischen, wenn der Wert ein Override ist (eine Zahl). Ein Urteil über
        einen anderen Befehl als den Override ändert nichts: Es verschöbe sonst das Auffrischen.
        """
        if (command.actuator, command.kind) != _SETPOINT:
            return
        judged = self._last.judged(command, verdict, not_sent=not_sent)
        if judged and verdict is not None and verdict.sendable:
            self._refreshed = None if self._cycle is None else self._cycle.now
        self._settle()

    def note_result(self, result: WriteResult) -> None:
        """Merkt das Ergebnis eines gesendeten Befehls (``LastValues.finished``).

        ``ERROR``, ``TIMEOUT`` und ``NOT_SENT`` löschen den letzten eigenen Wert, wenn er dem
        Ergebnis entspricht, und damit die Zeit des Auffrischens: Der nächste Zyklus sendet den
        Wert neu, als normalen Befehl. Ergebnisse des Keepers mit anderem Wert bleiben ohne Wirkung,
        ebenso jedes Ergebnis einer anderen Art oder eines anderen Aktors.
        """
        if (result.actuator, result.kind) != _SETPOINT:
            return
        self._last.finished(result)
        self._settle()

    def forget(self, actuators: Collection[str]) -> None:
        """Vergisst die letzten eigenen Werte dieser Aktoren, bei ``victron`` den Override.

        Der nächste ``decide`` bietet das Ziel wieder an; hat der Cerbo es schon, antwortet die
        Safety-Schicht ``UNCHANGED``, und das Auffrischen folgt sofort.
        """
        self._last.forget(actuators)
        self._settle()

    def idle(self) -> None:
        """Ein Zyklus ohne Regelung: Der Akku-Sollwert vergisst seine Werte und beginnt neu.

        Die Unterstützung beginnt neu (``Support.reset``): Der SOC muss ``support_soc_pct`` wieder
        erreichen. Health ist bis zum nächsten ``decide`` ``idle``.
        """
        self._last.forget()
        self._refreshed = None
        self._support.reset()
        self._cycle = None

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten ``decide`` (Design 9); was unbekannt ist, ist ``None``.

        - ``state``: ``idle`` vor dem ersten ``decide`` und nach ``idle``; ``locked``, wenn Victron
          gesperrt oder nicht steuerbar ist; ``unknown``, wenn das Ziel unbekannt ist (Bedarf oder
          Basis unbekannt, ein Budget keine endliche Zahl); sonst ``ok``
        - ``target_w``: der gewollte Netzbezug nach dem Wächter (``W'``); unbekannt ohne Bedarf
          und, bei einem Budget, ohne Basis
        - ``override_w``: der letzte eigene Wert als Zahl; ``None`` ohne einen und für „kein
          Override“
        - ``support`` und ``bridging_s``: Stand der Unterstützung und Sekunden der laufenden
          Überbrückung aus dem Bedarf
        - ``refresh_age_s``: Sekunden vom letzten Urteil mit Wert über einen eigenen Override bis
          zum letzten ``decide``
        """
        cycle, refreshed = self._cycle, self._refreshed
        need = None if cycle is None else cycle.need
        return {
            "state": "idle" if cycle is None else cycle.state,
            "target_w": None if cycle is None else cycle.target_w,
            "override_w": self._own_w(),
            "support": None if need is None else need.support,
            "bridging_s": None if need is None else need.bridging_s,
            "refresh_age_s": (
                None
                if cycle is None or refreshed is None
                else (cycle.now - refreshed).total_seconds()
            ),
        }

    def _command(
        self,
        now: datetime,
        target_w: float,
        setpoint_w: float,
        reasons: tuple[str, ...],
        present: Mapping[Key, CommandValue],
    ) -> Command | None:
        """Ein neuer Wert für den Override, sonst das Auffrischen, wenn es fällig ist.

        ``target_w`` ist ``W'``, ``setpoint_w`` das Ziel ``T``.
        """
        own_w = self._own_w()
        value = self._next_value(own_w, target_w, setpoint_w)
        if self._last.changed(_SETPOINT, value):
            if value is None and own_w is None and finite_number(present.get(_SETPOINT)) is None:
                return None  # ohne eigenen Wert und ohne Override am Cerbo: nichts aufzuheben
            return self._last.command(VICTRON, CommandKind.GRID_SETPOINT, value, reasons)
        if own_w is not None and self._refresh_due(now):
            return Command(VICTRON, CommandKind.GRID_SETPOINT, own_w, reasons=reasons, refresh=True)
        return None

    def _next_value(self, own_w: float | None, target_w: float, setpoint_w: float) -> float | None:
        """Der Wert, den der Override nach an, aus und Änderung haben soll (Präzisierung 6).

        ``own_w`` ist der letzte eigene Wert als Zahl, sonst ``None``; ohne Änderung ist das
        Ergebnis ``own_w``.
        """
        if own_w is None:  # „kein Override“ oder keiner: erst ab dem Totband
            return setpoint_w if target_w >= self._deadband_w else None
        if target_w < self._deadband_w / 2:
            return None
        return setpoint_w if abs(setpoint_w - own_w) >= self._deadband_w else own_w

    def _refresh_due(self, now: datetime) -> bool:
        """Ob das Auffrischen fällig ist: ``override_refresh_s`` nach dem letzten Urteil mit Wert.

        Ohne ein solches Urteil ist es sofort fällig (Docstring der Klasse).
        """
        refreshed = self._refreshed
        return refreshed is None or (now - refreshed).total_seconds() >= self._refresh_s

    def _own_w(self) -> float | None:
        """Der letzte eigene Override als Zahl; ``None`` ohne einen und für „kein Override“."""
        return finite_number(self._last.requested(_SETPOINT))

    def _settle(self) -> None:
        """Ohne eigenen Override (eine Zahl) gibt es nichts aufzufrischen: Die Zeit verfällt.

        Kommt der Wert danach nur mit ``UNCHANGED`` zurück, ist das Auffrischen so sofort fällig.
        """
        if self._own_w() is None:
            self._refreshed = None


def _round10(watts: float) -> float:
    """Auf 10 W gerundet, halbe Zehner aufwärts: ``10 · floor(x / 10 + 0,5)``."""
    return 10.0 * math.floor(watts / 10 + 0.5)


def _guarded(
    wanted_w: float, base_w: float | None, budget_w: float | None
) -> tuple[float, bool] | None:
    """``W'`` nach dem Budget des 15-min-Wächters und ob das Budget ``W`` gesenkt hat.

    Mit Budget ist ``W' = max(0, min(W, Budget − Basis))`` (Präzisierung 12), ohne ``W' = W``. Mit
    Budget müssen die Basis bekannt und das Budget eine endliche Zahl sein, sonst ist ``W'``
    unbekannt (``None``).
    """
    if budget_w is None:
        return wanted_w, False
    budget = finite_number(budget_w)
    if budget is None or base_w is None:
        return None
    guarded_w = max(0.0, min(wanted_w, budget - base_w))
    return guarded_w, guarded_w < wanted_w


def _reasons(need: BatteryNeed, lowered: bool) -> tuple[str, ...]:
    """Die Gründe des Overrides; ``lowered`` heißt: Das Budget des Wächters hat ``W`` gesenkt.

    ``battery_support``, wenn der Akku etwas liefern darf, sonst ``battery_comp``; dahinter
    ``peak_guard``, wenn ``lowered``.
    """
    first = BATTERY_SUPPORT if sum(need.allowed_w.values()) > 0.0 else BATTERY_COMP
    return (first, GuardCause.PEAK_GUARD.value) if lowered else (first,)


def _inputs(
    need: BatteryNeed, target_w: float, present: Mapping[Key, CommandValue]
) -> dict[str, float | None]:
    """Die Eingaben eines Befehls für das Entscheidungslog (Design 9).

    ``deficit_w`` und ``allowed_w`` zusammen über die Autos, ``target_w`` ist ``W'``,
    ``actual_setpoint_w`` der Override laut Cerbo; „kein Override“ und unbekannt sind ``None``.
    """
    return {
        "pv_w": need.pv_w,
        "house_w": need.house_w,
        "cars_w": need.cars_w,
        "deficit_w": sum(need.deficit_w.values(), 0.0),
        "allowed_w": sum(need.allowed_w.values(), 0.0),
        "target_w": target_w,
        "actual_setpoint_w": finite_number(present.get(_SETPOINT)),
    }
