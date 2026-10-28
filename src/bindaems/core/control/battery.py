"""Akku-Sollwert: Fehlbetrag der Autos, Unterstützung, erlaubter Akku-Anteil und Override.

Plan 2c-2a, Design 5.2 bis 5.6, Präzisierungen 4 bis 8 und 12. Rechnungen mit Leistungen in W:

- ``BatteryCar``: ein Auto für den Akku-Sollwert mit Wallbox, geltendem Modus und Leistung
- ``deficits``: der Fehlbetrag je Auto, nachdem die PV das Haus und die Autos davor gedeckt hat
- ``effective_threshold``: die wirksame Schwelle der Unterstützung aus Konfiguration, Absicht und
  Plan (Plan 3c, Präzisierung 8)
- ``Support``: der Stand der Unterstützung mit SOC-Hysterese und Dauer der Überbrückung
- ``Spare``: der Stand des übrigen Akkus für Autos in Sofort mit SOC-Hysterese an der Plan-Schwelle
  (Plan Akku ins Auto, Entscheidung 6)
- ``allowed``: der Anteil, den der Akku je Auto liefern darf
- ``BatteryNeed``: der Bedarf eines Zyklus und daraus der gewollte Netzbezug ``W``
- ``plan_wanted_w``: der gewollte Netzbezug ``W`` mit dem gültigen Slot des Plans (Plan 3c,
  Präzisierung 7); ``cycle_wanted_w`` dasselbe mit unbekannter Basis, für Override und Wächter
- ``BatteryController``: der Override an Victron (``grid_setpoint``) aus dem Bedarf, mit Totband,
  Auffrischen, Sperren und Health

Der Fehlbetrag eines Autos ist die Leistung, die es aus Akku oder Netz bezieht. Der Akku liefert
davon den erlaubten Anteil; was darüber hinausgeht, ``max(0, Fehlbetrag − erlaubt)``, kommt aus dem
Netz. Die Hauslast deckt der Akku immer, außer ein Slot des Plans will ``HALTEN`` oder
``NETZLADEN``: Dann kommen Haus und Autos aus dem Netz, soweit die PV sie nicht deckt
(``plan_wanted_w``), und der erlaubte Anteil ist für jedes Auto 0, auch in Boost.

Akku ins Auto (Plan Akku ins Auto, Entscheidungen 4 und 6): Im Eigenverbrauch bekommt ein Auto in
„Geplant“ höchstens den Akkuanteil, den der gültige Slot für sein Fahrzeug nennt, solange der SOC
über dem geplanten SOC am Ende des Slots liegt; ein Auto in „Sofort“ seinen ganzen Fehlbetrag,
solange der Slot übrigen Akku erlaubt und der SOC über der Plan-Schwelle liegt (``Spare``). Beides
geht nicht von ``support_max_w`` ab, ist aber auf die Entladeleistung begrenzt, die nach Haus,
Unterstützung und Boost frei bleibt (Fix-Welle F3); der Rest ist Netzanteil, den Override und
15-min-Wächter sehen. Ohne gültigen Slot regelt der core wie ohne Plan.

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
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.core.control.timing import held_since, lasted
from bindaems.core.safety.types import Verdict
from bindaems.shared.config import Config, ControlConfig
from bindaems.shared.control import VICTRON, Command, CommandKind, CommandValue, SupportIntent
from bindaems.shared.numbers import finite_number
from bindaems.shared.plan import BatteryIntentKind

BRIDGE_END_S: Final = 60.0  # so lange ohne Fehlbetrag, dann ist die Überbrückung beendet

# Gründe des Overrides (Design 9); hinter ihnen ``peak_guard``, wenn das Budget des Wächters senkt
BATTERY_COMP: Final = "battery_comp"  # der Fehlbetrag der Autos geht aufs Netz statt aus dem Akku
BATTERY_SUPPORT: Final = "battery_support"  # nur der Teil über dem erlaubten Anteil geht aufs Netz
BATTERY_SPARE: Final = "battery_spare"  # Geplant oder Sofort laden aus übrigem Akku
PLAN_HOLD: Final = "plan_hold"  # der Plan will HALTEN: Der Akku gibt nichts ab
PLAN_GRID_CHARGE: Final = "plan_grid_charge"  # der Plan will NETZLADEN: dazu P aus dem Netz

# der Stand der Unterstützung (Health)
SupportReason = Literal["on", "soc", "time", "mode", "off", "plan"]
# der Stand des übrigen Akkus für Sofort (Health, Plan Akku ins Auto)
SpareReason = Literal["on", "off", "plan", "soc"]
# woher die wirksame Schwelle der Unterstützung kommt (Health, Plan 3c)
SupportSource = Literal["config", "intent", "plan"]

# Diese Modi bekommen Akkuleistung, wenn die Unterstützung gilt (Design 5.2).
SUPPORT_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})
# Diese Modi bekommen ihren ganzen Fehlbetrag, wenn der Slot übrigen Akku erlaubt (Plan Akku ins
# Auto, Entscheidung 6).
SPARE_MODES: Final = frozenset({Effective.NOW})

_SETPOINT: Final[Key] = (VICTRON, CommandKind.GRID_SETPOINT)  # der Override

_State = Literal["ok", "unknown", "locked"]  # der Stand eines Zyklus mit ``decide`` (Health)

_ALLOWED: Final = SupportIntent()  # ohne Angabe: erlaubt, Schwelle aus der Konfiguration

# Diese Modi bekommen ihren Akkuanteil aus dem Plan (``BatteryNeed.spare_w``, Plan Akku ins Auto).
_PLAN_SHARE_MODES: Final = SPARE_MODES | {Effective.PLANNED}


@dataclass(frozen=True)
class BatteryCar:
    """Ein Auto für den Akku-Sollwert (Präzisierung 4); ``None`` heißt unbekannt.

    - ``wallbox``: die Wallbox, an der das Auto lädt; ihr Name ist der Schlüssel von Fehlbetrag
      und erlaubtem Anteil und in einer Liste von Autos eindeutig
    - ``mode``: der geltende Modus des Fahrzeugs, ohne Fahrzeug der des Gastes
    - ``power_w``: die Ladeleistung des Autos in W
    - ``charging``: ob das Auto lädt; ohne Leistung entscheidet das, ob der Bedarf unbekannt ist
    - ``vehicle``: der Fahrzeug-Schlüssel des Ladepunkts, auch der eines Gastes; ``None`` für eine
      Wallbox ohne Ladepunkt. Er wählt den Akkuanteil des Plans für „Geplant“ (Plan Akku ins Auto).
    """

    wallbox: str
    mode: Effective
    power_w: float | None
    charging: bool | None
    vehicle: str | None = None


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


def effective_threshold(
    control: ControlConfig, support: SupportIntent, active: ActiveSlot | None
) -> tuple[float, SupportSource]:
    """Die wirksame Schwelle der Unterstützung in % und ihre Quelle (Plan 3c, Präzisierung 8).

    Die eigene Schwelle ist ``support.soc_pct`` (Quelle ``intent``), ohne Angabe
    ``control.support_soc_pct`` (``config``). Die Plan-Schwelle des gültigen Slots
    (``active.support_soc_pct``) gilt, wenn sie echt höher liegt (``plan``); ohne Slot oder ohne
    Plan-Schwelle zählt sie nicht.
    """
    own: tuple[float, SupportSource] = (
        (control.support_soc_pct, "config")
        if support.soc_pct is None
        else (support.soc_pct, "intent")
    )
    planned = None if active is None else finite_number(active.support_soc_pct)
    if planned is not None and planned > own[0]:
        return planned, "plan"
    return own


class Support:
    """Der Stand der Unterstützung: SOC-Hysterese und Überbrückung (Präzisierung 5).

    Die Unterstützung ist die Akkuleistung für Autos in PV-Überschuss und Min+PV
    (``SUPPORT_MODES``). Was „Geplant“ und „Sofort“ aus dem Akku bekommen (Plan Akku ins Auto),
    gehört nicht dazu: Es folgt dem Plan (``allowed``, ``Spare``), nicht dieser Schwelle. Sie gilt,
    wenn ein Auto in einem dieser Modi ist,

    - sie erlaubt ist (Schalter „Akku-Unterstützung erlaubt“ der Absicht, Plan 3c) und kein Slot
      des Plans ``HALTEN`` oder ``NETZLADEN`` will: Dann gibt der Akku ohnehin nichts ab,
    - der SOC sie erlaubt: Sie wird möglich, wenn er die wirksame Schwelle des Zyklus erreicht
      (``threshold_pct``, ``effective_threshold``), und gesperrt, wenn er unter ``threshold_pct −
      support_hysteresis_pct`` fällt; dazwischen bleibt der Stand. Steigt die Schwelle, gilt das
      neue Band sofort. Ein unbekannter SOC sperrt sie im Zyklus, ändert den Stand aber nicht.
    - die Überbrückung noch keine ``support_max_s`` dauert. Sie beginnt mit dem ersten Zyklus mit
      Fehlbetrag und endet, wenn ``BRIDGE_END_S`` lang keiner mehr da war; ab ``support_max_s``
      bleibt die Unterstützung gesperrt, bis sie endet. Einen Fehlbetrag haben die Autos in
      PV-Überschuss und Min+PV, wenn er zusammen mindestens ``setpoint_deadband_w / 2`` ist (und
      über 0): Darunter entsteht ohnehin kein Override, und das Rauschen der Hauslast soll keine
      Überbrückung beginnen oder verlängern.

    Der Stand für Health ist ``"on"`` oder der Grund der Sperre in dieser Reihenfolge: ``"mode"``
    (kein Auto in PV-Überschuss oder Min+PV), ``"off"`` (nicht erlaubt), ``"plan"`` (``HALTEN``
    oder ``NETZLADEN``), ``"soc"``, ``"time"``. SOC-Stand und Überbrückung laufen in jedem Zyklus
    mit, was auch immer der Grund ist. Nach einer Pause beginnt der Stand mit ``reset`` neu, wie
    der von ``Spare``. Der Zustand lebt nur im Speicher. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, control: ControlConfig) -> None:
        self._hysteresis_pct = control.support_hysteresis_pct
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
        *,
        threshold_pct: float,
        allowed: bool,
        plan_blocks: bool,
    ) -> tuple[SupportReason, float | None]:
        """Rechnet einen Zyklus zum Zeitpunkt ``now``.

        Das Ergebnis ist der Stand der Unterstützung und die Sekunden der laufenden Überbrückung
        (``None`` ohne). ``deficit`` ist der Fehlbetrag je Wallbox; ein Auto ohne Eintrag hat
        keinen. ``now`` steigt von Aufruf zu Aufruf (die Uhr der Safety-Schicht).

        - ``threshold_pct``: die wirksame Schwelle des Zyklus in % (``effective_threshold``)
        - ``allowed``: der Schalter „Akku-Unterstützung erlaubt“ der Absicht
        - ``plan_blocks``: Der gültige Slot will ``HALTEN`` oder ``NETZLADEN``.
        """
        soc = finite_number(soc_pct)
        self._follow_soc(soc, threshold_pct)
        supported = [car for car in cars if car.mode in SUPPORT_MODES]
        deficit_w = sum(deficit.get(car.wallbox, 0.0) for car in supported)
        self._follow_bridging(now, deficit_w > 0.0 and deficit_w >= self._threshold_w)
        since = self._bridging_since
        bridging_s = None if since is None else (now - since).total_seconds()

        if not supported:
            return "mode", bridging_s
        if not allowed:
            return "off", bridging_s
        if plan_blocks:
            return "plan", bridging_s
        if soc is None or not self._soc_ok:
            return "soc", bridging_s
        if bridging_s is not None and bridging_s >= self._max_s:
            return "time", bridging_s
        return "on", bridging_s

    def _follow_soc(self, soc: float | None, threshold_pct: float) -> None:
        """Die Hysterese gegen die Schwelle ``threshold_pct``: Der Stand wechselt nur an ihr und an
        ``threshold_pct − support_hysteresis_pct``; unbekannt ändert nichts."""
        if soc is None:
            return
        if soc >= threshold_pct:
            self._soc_ok = True
        elif soc < threshold_pct - self._hysteresis_pct:
            self._soc_ok = False

    def _follow_bridging(self, now: datetime, has_deficit: bool) -> None:
        """Beginnt die Überbrückung mit dem ersten Fehlbetrag und beendet sie nach 60 s ohne."""
        self._clear_since = held_since(self._clear_since, not has_deficit, now)
        if self._bridging_since is not None and lasted(self._clear_since, now, BRIDGE_END_S):
            self._bridging_since = None
        if has_deficit and self._bridging_since is None:
            self._bridging_since = now


class Spare:
    """Der Stand des übrigen Akkus für Autos in Sofort (Plan Akku ins Auto, Entscheidung 6).

    Ein Slot mit ``now_battery`` erlaubt jedem Auto in Sofort (``SPARE_MODES``, auch einem Gast)
    seinen ganzen Fehlbetrag aus dem Akku, solange der SOC über der Plan-Schwelle des Slots liegt
    (``support_soc_pct``, der SOC, den der Plan für seine späteren Entladungen braucht). Erreicht
    er sie, ist der übrige Akku gesperrt, bis der SOC wieder ``support_hysteresis_pct`` darüber
    liegt; dazwischen bleibt der Stand. Der Stand folgt dem SOC in jedem Slot mit Plan-Schwelle,
    was auch immer der Grund ist; ein unbekannter SOC sperrt im Zyklus, ändert ihn aber nicht.

    Der Stand ist ``"on"`` oder der Grund der Sperre in dieser Reihenfolge: ``"off"`` (kein Slot
    oder einer ohne ``now_battery``), ``"plan"`` (``HALTEN`` oder ``NETZLADEN``), ``"soc"`` (SOC
    unbekannt, gesperrt oder der Slot ohne Plan-Schwelle). Nach einer Pause beginnt der Stand mit
    ``reset`` neu, wie der von ``Support``. Der Zustand lebt nur im Speicher. Nicht für mehrere
    Threads gedacht.
    """

    def __init__(self, control: ControlConfig) -> None:
        self._hysteresis_pct = control.support_hysteresis_pct
        self.reset()

    def reset(self) -> None:
        """Beginnt neu, etwa nach einer Pause: Ein SOC über der Plan-Schwelle gibt wieder frei."""
        # der SOC hat die Plan-Schwelle erreicht und die Hysterese nicht verlassen
        self._locked = False

    def update(self, soc_pct: float | None, active: ActiveSlot | None) -> SpareReason:
        """Der Stand dieses Zyklus mit dem SOC ``soc_pct`` und dem gültigen Slot ``active``."""
        soc = finite_number(soc_pct)
        threshold = None if active is None else finite_number(active.support_soc_pct)
        if soc is not None and threshold is not None:
            if soc <= threshold:
                self._locked = True
            elif soc >= threshold + self._hysteresis_pct:
                self._locked = False
        if active is None or not active.now_battery:
            return "off"
        if _kind(active) is not BatteryIntentKind.EIGENVERBRAUCH:
            return "plan"
        if soc is None or threshold is None or self._locked:
            return "soc"
        return "on"


def allowed(
    cars: Sequence[BatteryCar],
    deficit: Mapping[str, float],
    *,
    support_on: bool,
    support_max_w: float,
    room_w: float,
    planned_w: Mapping[str, int] = {},
    planned_on: bool = False,
    spare_on: bool = False,
) -> dict[str, float]:
    """Der Anteil des Fehlbetrags, den der Akku je Wallbox liefern darf (Präzisierung 5).

    In der Reihenfolge von ``cars`` (der Priorität) bekommt

    - ein Auto in PV-Überschuss oder Min+PV bei ``support_on`` ``min(Fehlbetrag, Rest)``, wobei der
      Rest ``support_max_w`` ist, vermindert um das, was die Autos davor in diesen Modi bekamen;
      ohne ``support_on`` nichts,
    - ein Auto in Boost seinen ganzen Fehlbetrag; er geht nicht vom Rest ab,
    - ein Auto in Geplant mit Fahrzeug bei ``planned_on`` ``min(Fehlbetrag, Akkuanteil)``, wobei
      ``planned_w`` den Akkuanteil des Slots je Fahrzeug nennt (ohne Eintrag 0); ohne
      ``planned_on`` nichts (Plan 4b, Entscheidung 4; Plan Akku ins Auto, Entscheidung 4),
    - ein Auto in Sofort bei ``spare_on`` seinen ganzen Fehlbetrag (Plan Akku ins Auto,
      Entscheidung 6), sonst nichts,
    - ein Auto in Aus nichts.

    Was Geplant und Sofort bekommen, geht nicht vom Rest ab. Es ist aber durch die freie
    Entladeleistung begrenzt (Fix-Welle F3): ``room_w`` ist die Entladeleistung des Akkus, die nach
    dem Haus bleibt; davon gehen zuerst die Anteile der Unterstützung und von Boost ab, der Rest
    geht an die Autos in Geplant, dann an die in Sofort, je in der Reihenfolge von ``cars``. Was
    darüber liegt, bleibt Netzanteil (``BatteryNeed.grid_w``), den Override und 15-min-Wächter
    sehen; sonst holte ESS es ungesehen aus dem Netz. Das ist eine physikalische Grenze, keine
    eigene Leistungsgrenze im Sinne von Entscheidung 6. Ist ``room_w`` keine endliche Zahl oder
    nicht über 0, bekommen Geplant und Sofort nichts.

    Das Ergebnis nennt jede Wallbox von ``cars``; ein Auto ohne Eintrag in ``deficit`` hat keinen
    Fehlbetrag. Fehlbeträge, ``support_max_w`` und Akkuanteile sind nicht negativ.
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
        elif car.mode is Effective.PLANNED and planned_on and car.vehicle is not None:
            share_w = min(wanted_w, float(planned_w.get(car.vehicle, 0)))
        elif car.mode in SPARE_MODES and spare_on:
            share_w = wanted_w
        else:
            share_w = 0.0
        shares[car.wallbox] = share_w
    room = finite_number(room_w)
    free_w = 0.0 if room is None else room
    free_w -= sum((shares[car.wallbox] for car in cars if car.mode not in _PLAN_SHARE_MODES), 0.0)
    for modes in ({Effective.PLANNED}, SPARE_MODES):
        for car in cars:
            if car.mode in modes:
                shares[car.wallbox] = min(shares[car.wallbox], max(free_w, 0.0))
                free_w -= shares[car.wallbox]
    return shares


def _planned_on(soc_pct: float | None, active: ActiveSlot | None) -> bool:
    """Ob ein Auto in Geplant seinen Akkuanteil bekommt: im Eigenverbrauch, solange der SOC über
    dem geplanten SOC am Ende des Slots liegt; ohne SOC, Slot oder SOC am Ende nicht."""
    soc = finite_number(soc_pct)
    end = None if active is None else finite_number(active.soc_end_pct)
    if soc is None or end is None or _kind(active) is not BatteryIntentKind.EIGENVERBRAUCH:
        return False
    return soc > end


@dataclass(frozen=True)
class BatteryNeed:
    """Der Bedarf eines Zyklus für den Akku-Sollwert (Design 5.2, 5.3); Leistungen in W.

    - ``pv_w``, ``house_w``: PV und Hauslast, wie gemessen
    - ``cars_w``: die Ladeleistung der Autos zusammen, wie der Fehlbetrag sie zählt
    - ``deficit_w``: der Fehlbetrag je Wallbox (``deficits``)
    - ``allowed_w``: der Anteil, den der Akku je Wallbox liefern darf (``allowed``); eine Wallbox
      ohne Eintrag bekommt nichts. Bei ``HALTEN`` und ``NETZLADEN`` ist er für jede 0, auch in
      Boost (Plan 3c, Entscheidung 3): ``grid_w`` ist dann der ganze Fehlbetrag.
    - ``support``: der Stand der Unterstützung, ``bridging_s`` die Sekunden der laufenden
      Überbrückung (``Support``)
    - ``spare_w``: der erlaubte Anteil der Autos in Geplant und Sofort zusammen (Plan Akku ins
      Auto); ``spare``: der Stand des übrigen Akkus für Sofort (``Spare``)
    """

    pv_w: float
    house_w: float
    cars_w: float
    deficit_w: Mapping[str, float]
    allowed_w: Mapping[str, float]
    support: SupportReason
    bridging_s: float | None
    spare_w: float = 0.0
    spare: SpareReason = "off"

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


def plan_wanted_w(need: BatteryNeed, base_w: float, active: ActiveSlot | None) -> float:
    """Der gewollte Netzbezug ``W`` über der Basis ``base_w`` mit dem gültigen Slot ``active``
    (Plan 3c, Präzisierung 7 und Entscheidung 2).

    - **``HALTEN``:** ``max(0, Haus + Autos − PV − Basis)``. Der Akku gibt nichts ab, auch nicht
      an ein Auto in Boost (Entscheidung 3), und ein PV-Überschuss lädt ihn weiter.
    - **``NETZLADEN(P)``:** dasselbe plus ``P`` (``grid_charge_w``).
    - **``EIGENVERBRAUCH`` oder kein Slot:** ``BatteryNeed.wanted_w``; die Basis zählt nicht.

    PV und Hauslast unter 0 (Messrauschen) zählen als 0, wie im Fehlbetrag (``deficits``); die
    Autos zählen mit ``cars_w``.
    """
    kind = _kind(active)
    if active is None or kind is BatteryIntentKind.EIGENVERBRAUCH:
        return need.wanted_w
    drawn_w = max(need.house_w, 0.0) + need.cars_w - max(need.pv_w, 0.0)
    hold_w = max(0.0, drawn_w - base_w)
    if kind is BatteryIntentKind.NETZLADEN:
        return hold_w + active.intent.grid_charge_w
    return hold_w


def cycle_wanted_w(
    need: BatteryNeed | None, base_w: float | None, active: ActiveSlot | None
) -> float | None:
    """``W`` dieses Zyklus (``plan_wanted_w``); ``None`` heißt unbekannt.

    Unbekannt ist ``W`` ohne Bedarf und, bei ``HALTEN`` und ``NETZLADEN``, ohne Basis (``None``
    oder keine endliche Zahl): Dort zählt ``W`` von der Basis an.
    """
    if need is None:
        return None
    base = finite_number(base_w)
    if base is None:
        return need.wanted_w if _kind(active) is BatteryIntentKind.EIGENVERBRAUCH else None
    return plan_wanted_w(need, base, active)


def _kind(active: ActiveSlot | None) -> BatteryIntentKind:
    """Die Absicht des Slots für den Akku; ohne Slot ``EIGENVERBRAUCH``."""
    return BatteryIntentKind.EIGENVERBRAUCH if active is None else active.intent.kind


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
      Basis ``ess.grid_setpoint_w``. ``W`` folgt dem gültigen Slot des Plans (``plan_wanted_w``,
      Plan 3c); ohne Slot und im Eigenverbrauch ist es ``BatteryNeed.wanted_w``. Alles Weitere gilt
      für jede Absicht gleich. ``W'`` ist ``W`` nach dem Budget des 15-min-Wächters:
      ``max(0, min(W, floor10(Budget) − Basis))`` mit ``floor10(x) = 10 · floor(x / 10)``, ohne
      Budget ``W``. Mit ``W'`` über 0 liegt ``T`` so nie über dem Budget.
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
    - **Unbekanntes Ziel** (Bedarf, Basis, ``W`` oder ``W'`` unbekannt): kein Befehl, auch kein
      Auffrischen. Ein Override verfällt so nach 300 s (Design 5.2).
    - **Gesperrt oder nicht steuerbar** (Victron): kein Befehl, und der Akku-Sollwert vergisst seine
      Werte; Bedarf, Unterstützung und Ziel rechnet er für Health weiter. Ein offener
      Schreibvorgang oder eine Übergabe (``blocked``) hält nur den Befehl dieses Zyklus zurück.
    - **Gründe:** ``plan_hold`` bei ``HALTEN``, ``plan_grid_charge`` bei ``NETZLADEN``; sonst
      ``battery_spare``, wenn Autos in Geplant und Sofort etwas aus dem Akku bekommen
      (``BatteryNeed.spare_w`` über 0, Plan Akku ins Auto), ``battery_support``, wenn der erlaubte
      Anteil zusammen über 0 liegt, und ``battery_comp``; dahinter ``peak_guard``, wenn das Budget
      ``W`` gesenkt hat.

    Kein Befehl ist dringend. Der Zustand lebt nur im Speicher: die letzten eigenen Werte, die
    Unterstützung, der übrige Akku für Sofort, die Zeit des letzten Urteils mit Wert über einen
    eigenen Override und der Stand des letzten ``decide``. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, cfg: Config) -> None:
        control = cfg.control
        self._control = control
        self._deadband_w = control.setpoint_deadband_w
        self._refresh_s = control.override_refresh_s
        self._support_max_w = control.support_max_w
        # die Entladegrenze des Akkus; ein Wert der BMS ist hier nicht bekannt (Fix-Welle F3)
        self._max_discharge_w = cfg.battery.max_discharge_w
        self._support = Support(control)
        self._spare = Spare(control)
        self._last = LastValues()
        # das letzte Urteil mit Wert über den eigenen Override; ``None`` ohne einen oder ohne Urteil
        self._refreshed: datetime | None = None
        self._cycle: _Cycle | None = None  # ``None`` vor dem ersten ``decide`` und nach ``idle``
        # die wirksame Schwelle des letzten ``assess``; ``None`` davor und nach ``idle``
        self._threshold: tuple[float, SupportSource] | None = None
        # Stand des übrigen Akkus und Anteil von Geplant und Sofort des letzten ``assess``; ``None``
        # davor und nach ``idle``, der Anteil auch, wenn der Bedarf unbekannt ist
        self._spare_state: SpareReason | None = None
        self._spare_w: float | None = None

    def assess(
        self,
        now: datetime,
        soc_pct: float | None,
        pv_w: float | None,
        house_w: float | None,
        cars: Sequence[BatteryCar],
        *,
        support: SupportIntent = _ALLOWED,
        active: ActiveSlot | None = None,
    ) -> BatteryNeed | None:
        """Der Bedarf dieses Zyklus: Fehlbetrag, Unterstützung und erlaubter Anteil.

        ``cars`` steht in der Reihenfolge von Präzisierung 4. ``None``, wenn PV oder Hauslast
        unbekannt sind oder ``deficits`` es ist; die Unterstützung bleibt dann unberührt. ``now``
        steigt von Aufruf zu Aufruf (die Uhr der Safety-Schicht).

        ``support`` ist die Akku-Unterstützung der Absicht, ``active`` der gültige Slot des Plans
        (Plan 3c, Präzisierung 8): Sie bestimmen die wirksame Schwelle (``effective_threshold``),
        die Health auch ohne Bedarf zeigt. Bei ``HALTEN`` und ``NETZLADEN`` ist die Unterstützung
        gesperrt (``plan``), und der Akku liefert keinem Auto etwas, auch nicht in Boost
        (Entscheidung 3).

        Der Slot bestimmt auch, was Geplant und Sofort aus dem Akku bekommen (Plan Akku ins Auto):
        Geplant den Akkuanteil seines Fahrzeugs (``vehicles_battery``), solange der SOC über
        ``soc_end_pct`` liegt; Sofort seinen ganzen Fehlbetrag, solange ``Spare`` ``"on"`` ist.
        Beides zusammen höchstens die freie Entladeleistung (``allowed``, Fix-Welle F3):
        ``battery.max_discharge_w`` der Konfiguration minus dem Teil der Hauslast, den die PV
        nicht deckt. Den Stand von ``Spare`` rechnet jeder Aufruf, auch ohne Bedarf.
        """
        threshold_pct, source = effective_threshold(self._control, support, active)
        self._threshold = threshold_pct, source
        spare = self._spare.update(soc_pct, active)
        self._spare_state, self._spare_w = spare, None
        pv, house = finite_number(pv_w), finite_number(house_w)
        if pv is None or house is None:
            return None
        deficit = deficits(pv, house, cars)
        if deficit is None:
            return None
        held = _kind(active) is not BatteryIntentKind.EIGENVERBRAUCH
        reason, bridging_s = self._support.update(
            now,
            soc_pct,
            cars,
            deficit,
            threshold_pct=threshold_pct,
            allowed=support.allowed,
            plan_blocks=held,
        )
        if held:  # die Entladesperre gilt für alles
            shares = dict.fromkeys((car.wallbox for car in cars), 0.0)
        else:
            shares = allowed(
                cars,
                deficit,
                support_on=reason == "on",
                support_max_w=self._support_max_w,
                room_w=self._max_discharge_w - max(max(house, 0.0) - max(pv, 0.0), 0.0),
                planned_w={} if active is None else active.vehicles_battery,
                planned_on=_planned_on(soc_pct, active),
                spare_on=spare == "on",
            )
        spare_w = sum((shares[car.wallbox] for car in cars if car.mode in _PLAN_SHARE_MODES), 0.0)
        self._spare_w = spare_w
        # Jede Leistung ist bekannt oder, weil das Auto sicher nicht lädt, 0 W (``deficits``).
        cars_w = sum((_power_w(car) or 0.0 for car in cars), 0.0)
        return BatteryNeed(pv, house, cars_w, deficit, shares, reason, bridging_s, spare_w, spare)

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
        active: ActiveSlot | None = None,
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
        - ``active``: der gültige Slot des Plans (``Regulation.active``); ``None`` heißt keiner

        ``now`` ist die Zeit auf der Uhr der Safety-Schicht. Die Eingaben jedes Befehls für das
        Entscheidungslog nennt ``_inputs``.
        """
        base = finite_number(base_w)
        wanted_w = finite_number(cycle_wanted_w(need, base, active))
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
        reasons = _reasons(need, lowered, active)
        command = self._command(now, target_w, _round10(base + target_w), reasons, present)
        if command is None:
            return []
        return [ControlCommand(command, _inputs(need, target_w, present, active))]

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

        Die Unterstützung beginnt neu (``Support.reset``): Der SOC muss die wirksame Schwelle
        wieder erreichen. Ebenso der übrige Akku für Sofort (``Spare.reset``). Health ist bis zum
        nächsten ``decide`` ``idle``, Schwelle und übriger Akku bis zum nächsten ``assess``
        unbekannt.
        """
        self._last.forget()
        self._refreshed = None
        self._support.reset()
        self._spare.reset()
        self._cycle = None
        self._threshold = None
        self._spare_state = None
        self._spare_w = None

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten ``decide`` (Design 9); was unbekannt ist, ist ``None``.

        - ``state``: ``idle`` vor dem ersten ``decide`` und nach ``idle``; ``locked``, wenn Victron
          gesperrt oder nicht steuerbar ist; ``unknown``, wenn das Ziel unbekannt ist (Bedarf oder
          Basis unbekannt, ein Budget keine endliche Zahl); sonst ``ok``
        - ``target_w``: der gewollte Netzbezug nach dem Wächter (``W'``); unbekannt ohne Bedarf
          und, bei einem Budget, ohne Basis
        - ``override_w``: der letzte eigene Wert als Zahl; ``None`` ohne einen und für „kein
          Override“. Es ist der angeforderte Wert, vor den Regeln 2 und 4 der Safety-Schicht: Der
          Cerbo kann weniger halten, etwa 8000 W, wenn 17 940 W angefordert waren. Was er hält,
          zeigt ``actual_setpoint_w`` in den Eingaben der Entscheidungen (``_inputs``).
        - ``support`` und ``bridging_s``: Stand der Unterstützung und Sekunden der laufenden
          Überbrückung aus dem Bedarf
        - ``refresh_age_s``: Sekunden vom letzten Urteil mit Wert über einen eigenen Override bis
          zum letzten ``decide``
        - ``support_soc_pct`` und ``support_source``: die wirksame Schwelle des letzten ``assess``
          und ihre Quelle (``config``, ``intent``, ``plan``; Plan 3c, Präzisierung 8), auch ohne
          Bedarf; vor dem ersten ``assess`` und nach ``idle`` ``None``
        - ``spare`` und ``spare_w``: der Stand des übrigen Akkus für Sofort (``Spare``) und der
          erlaubte Anteil von Geplant und Sofort zusammen in W aus dem letzten ``assess`` (Plan Akku
          ins Auto); vor dem ersten ``assess`` und nach ``idle`` ``None``, ``spare_w`` auch ohne
          Bedarf
        """
        cycle, refreshed, threshold = self._cycle, self._refreshed, self._threshold
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
            "support_soc_pct": None if threshold is None else threshold[0],
            "support_source": None if threshold is None else threshold[1],
            "spare": self._spare_state,
            "spare_w": self._spare_w,
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


def _floor10(watts: float) -> float:
    """Auf 10 W abgerundet: ``10 · floor(x / 10)``."""
    return 10.0 * math.floor(watts / 10)


def _guarded(
    wanted_w: float, base_w: float | None, budget_w: float | None
) -> tuple[float, bool] | None:
    """``W'`` nach dem Budget des 15-min-Wächters und ob das Budget ``W`` gesenkt hat.

    Mit Budget ist ``W' = max(0, min(W, floor10(Budget) − Basis))`` (Präzisierung 12), ohne
    ``W' = W``. Das Budget zählt auf 10 W abgerundet: Liegt ``W'`` über 0, ist ``Basis + W'``
    höchstens ``floor10(Budget)``, und das Ziel ``round10(Basis + W')`` liegt nie über dem Budget.
    Regel 2 kappt den Override also nicht auf einen Wert, der kein Vielfaches von 10 W ist, und
    das Entscheidungslog bleibt ohne ``range.ceiling``. Bei ``W' = 0`` gibt es keinen Override,
    denn ``setpoint_deadband_w`` liegt über 0 (``ControlConfig``).
    Mit Budget müssen die Basis bekannt und das Budget eine endliche Zahl sein, sonst ist ``W'``
    unbekannt (``None``).
    """
    if budget_w is None:
        return wanted_w, False
    budget = finite_number(budget_w)
    if budget is None or base_w is None:
        return None
    guarded_w = max(0.0, min(wanted_w, _floor10(budget) - base_w))
    return guarded_w, guarded_w < wanted_w


def _reasons(need: BatteryNeed, lowered: bool, active: ActiveSlot | None) -> tuple[str, ...]:
    """Die Gründe des Overrides; ``lowered`` heißt: Das Budget des Wächters hat ``W`` gesenkt.

    ``plan_hold`` bei ``HALTEN``, ``plan_grid_charge`` bei ``NETZLADEN``; sonst
    ``battery_spare``, wenn Geplant und Sofort etwas aus dem Akku bekommen, ``battery_support``,
    wenn der Akku etwas liefern darf, und ``battery_comp``; dahinter ``peak_guard``, wenn
    ``lowered``.
    """
    kind = _kind(active)
    if kind is BatteryIntentKind.HALTEN:
        first = PLAN_HOLD
    elif kind is BatteryIntentKind.NETZLADEN:
        first = PLAN_GRID_CHARGE
    elif need.spare_w > 0.0:
        first = BATTERY_SPARE
    elif sum(need.allowed_w.values()) > 0.0:
        first = BATTERY_SUPPORT
    else:
        first = BATTERY_COMP
    return (first, GuardCause.PEAK_GUARD.value) if lowered else (first,)


def _inputs(
    need: BatteryNeed,
    target_w: float,
    present: Mapping[Key, CommandValue],
    active: ActiveSlot | None,
) -> dict[str, float | None]:
    """Die Eingaben eines Befehls für das Entscheidungslog (Design 9).

    ``deficit_w`` und ``allowed_w`` zusammen über die Autos, ``target_w`` ist ``W'``,
    ``actual_setpoint_w`` der Override laut Cerbo; „kein Override“ und unbekannt sind ``None``.
    Mit einem gültigen Slot kommt ``plan_grid_charge_w`` dazu (Plan 3c, Präzisierung 10): ``P``
    bei ``NETZLADEN``, sonst 0; ohne Slot fehlt der Schlüssel.
    """
    found: dict[str, float | None] = {
        "pv_w": need.pv_w,
        "house_w": need.house_w,
        "cars_w": need.cars_w,
        "deficit_w": sum(need.deficit_w.values(), 0.0),
        "allowed_w": sum(need.allowed_w.values(), 0.0),
        "target_w": target_w,
        "actual_setpoint_w": finite_number(present.get(_SETPOINT)),
    }
    if active is not None:
        found["plan_grid_charge_w"] = active.intent.grid_charge_w
    return found
