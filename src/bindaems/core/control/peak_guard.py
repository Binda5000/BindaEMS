"""15-min-Wächter, Teil 1: Messung, Budget, Hochrechnung und Zustände.

Plan 2c-2a, Design 6.1 bis 6.4 und 9, Präzisierungen 9, 10, 12 und 14. Leistungen in W, Energien in
Wh; ``health`` zeigt kW und kWh.

- ``QuarterObs``: die laufende Viertelstunde, wie die Abrechnung sie zählt
- ``PeakCar``: ein Auto für den Wächter
- ``PeakLimits`` und ``NO_LIMITS``: was der Wächter den anderen Bausteinen vorgibt
- ``PeakGuard``: Messung, Lücken, ``P_zul``, Hochrechnung, verlangte Senkung und Budget für den Akku
  und der Zustand ``off``, ``gap``, ``active`` oder ``exceeded``

Die Drosselung der Wallboxen (``caps_w``) und die Start-Sperre (``no_start``) folgen in Teil 2; bis
dahin bleiben sie leer. Ein fehlender Wert ist ``None`` (unbekannt), ebenso ein Wert, der keine
endliche Zahl ist (``finite_number``). Die Schicht ist rein: Die Zeit kommt als Argument, auf der
Wanduhr, wie die Viertelstunden der Abrechnung. Springt sie, stürzt der Wächter nicht ab.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.tesla_policy import MovingAverage
from bindaems.core.control.timing import held_since, lasted
from bindaems.shared.config import ControlConfig
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import HOLD_MAX_S, LOCAL_TZ, slot_end, slot_start

GAP_S: Final = 10.0  # mehr als so lange ohne bekannte Leistung: Die Viertelstunde ist lückenhaft
MEAN_S: Final = 30.0  # das Fenster des Mittels vom Netzbezug ohne Boost
EXCEEDED_AFTER_S: Final = 60.0  # so lange ununterbrochen über dem Ziel: überschritten
PEAK_EXCEEDED: Final = "peak.exceeded"  # die ID des Alarms (Design 9)

PeakState = Literal["off", "gap", "active", "exceeded"]  # der Zustand des Wächters (Health)

_QUARTER_H: Final = 0.25  # die Länge einer Viertelstunde in h
_MIN_REST_S: Final = 1.0  # kürzeste Restzeit der Rechnung: Am Ende wird nicht durch 0 geteilt


@dataclass(frozen=True)
class QuarterObs:
    """Die laufende Viertelstunde, wie die Abrechnung sie zählt (Präzisierung 9).

    - ``start``: der Beginn der laufenden Viertelstunde; der Wächter prüft ihn gegen
      ``slot_start(wall_now)``
    - ``grid_wh``: der Netzbezug darin bis jetzt in Wh
    - ``grid_w``: der Netzbezug des Zyklus in W; ``None`` heißt ohne Bilanz, unbekannt
    - ``wallbox_grid_w``: der Netzanteil je Wallbox im Zyklus in W, nach dem Namen der Wallbox
    """

    start: datetime
    grid_wh: float
    grid_w: float | None
    wallbox_grid_w: Mapping[str, float]


@dataclass(frozen=True)
class PeakCar:
    """Ein Auto für den Wächter (Präzisierungen 11 und 13); ``None`` heißt unbekannt.

    Leistungen in W.

    - ``vehicle``: das Fahrzeug; sein Name ist der Schlüssel von ``PeakLimits``
    - ``wallbox``: die Wallbox, an der es lädt; ihr Name ist der Schlüssel von
      ``QuarterObs.wallbox_grid_w``
    - ``mode``: der geltende Modus des Fahrzeugs, ohne Fahrzeug der des Gastes
    - ``rank``: die Priorität, 0 ist die höchste
    - ``power_w``: die Ladeleistung des Autos
    - ``grid_w``: sein Netzanteil (Präzisierung 11)
    - ``min_w``: die Mindestleistung seines Ladepunkts

    Teil 1 liest nur ``wallbox`` und ``mode``: Es zieht den Netzanteil der Autos in Boost ab. Die
    übrigen Felder braucht die Drosselung (Teil 2).
    """

    vehicle: str
    wallbox: str
    mode: Effective
    rank: int
    power_w: float | None
    grid_w: float
    min_w: float


@dataclass(frozen=True)
class PeakLimits:
    """Was der Wächter den anderen Bausteinen vorgibt (Präzisierungen 11 bis 13).

    - ``budget_w``: das Budget ``P_zul`` in W für den Akku-Sollwert und die Safety-Schicht; ``None``
      heißt keins
    - ``caps_w``: die Leistungsgrenze je Fahrzeug in W (Teil 2)
    - ``no_start``: die Fahrzeuge, die nicht starten sollen (Teil 2)
    """

    budget_w: float | None = None
    caps_w: Mapping[str, float] = field(default_factory=dict)
    no_start: frozenset[str] = frozenset()


NO_LIMITS: Final = PeakLimits()  # keine Vorgabe: Der Wächter ruht, oder die Messung fehlt


@dataclass(frozen=True)
class _Prev:
    """Der vorige Schritt: Sein Anteil an Boost gilt bis zum nächsten (Halteprinzip).

    - ``time``, ``start``: sein Zeitpunkt und seine Viertelstunde
    - ``known``: ob er eine bekannte Leistung hatte
    - ``boost_w``: der Netzanteil der Autos in Boost in W
    """

    time: datetime
    start: datetime
    known: bool
    boost_w: float


@dataclass
class _Running:
    """Die laufende Viertelstunde: Zustand, der mit ihrem Ende verfällt.

    - ``gappy``: lückenhaft bis zu ihrem Ende
    - ``last_known``: der letzte Schritt mit bekannter Leistung; ``None`` bis zum ersten
    - ``boost_wh``: die Energie der Autos in Boost bisher
    - ``over_since``: seit wann die Hochrechnung ununterbrochen über dem Ziel liegt
    - ``exceeded``: der Alarm, sobald die Viertelstunde überschritten ist
    """

    start: datetime
    gappy: bool = False
    last_known: datetime | None = None
    boost_wh: float = 0.0
    over_since: datetime | None = None
    exceeded: Alarm | None = None


@dataclass(frozen=True)
class _Calc:
    """Die Rechnung eines Schritts; Energie in Wh, Leistungen in W.

    - ``energy_wh``: ``E_bisher`` ohne Boost
    - ``allowed_w``: ``P_zul``; mit dem Budget unter 0 ist es negativ
    - ``projection_w``: die Hochrechnung auf die mittlere Leistung der Viertelstunde; ``None`` ohne
      Mittel
    - ``reduction_w``: die verlangte Senkung, auch unter 0; ``None`` ohne Mittel
    """

    energy_wh: float
    allowed_w: float
    projection_w: float | None
    reduction_w: float | None

    def health(self) -> dict[str, float | None]:
        """Die vier Werte von Health in kWh und kW, ohne Rundung; die Senkung mindestens 0."""
        reduction_w = None if self.reduction_w is None else max(0.0, self.reduction_w)
        return {
            "energy_kwh": self.energy_wh / 1000.0,
            "projected_kw": _kw(self.projection_w),
            "budget_kw": self.allowed_w / 1000.0,
            "reduction_kw": _kw(reduction_w),
        }


# Die vier Werte von Health in einem Schritt, der nicht rechnet.
_NO_VALUES: Final[dict[str, float | None]] = {
    "energy_kwh": None,
    "projected_kw": None,
    "budget_kw": None,
    "reduction_kw": None,
}


@dataclass(frozen=True)
class _Last:
    """Der letzte Schritt für Health: Zustand, Ende der Viertelstunde und Rechnung.

    ``calc`` ist ``None``, wenn der Schritt nicht rechnete.
    """

    state: PeakState
    quarter_end: datetime
    calc: _Calc | None


class PeakGuard:
    """Der 15-min-Wächter, Teil 1: misst die Viertelstunde, rechnet und entscheidet den Zustand.

    Ohne ``control.peak_target_kw`` ruht er ganz: ``NO_LIMITS`` und Zustand ``off``. Sonst ist die
    laufende Viertelstunde ``slot_start(wall_now)`` bis ``slot_end(wall_now)``. Eine ``QuarterObs``
    gilt, wenn sie da ist, ihr ``start`` der Beginn dieser Viertelstunde ist und ``grid_wh`` eine
    endliche Zahl; sonst zählt sie wie keine. Die Leistung eines Schritts ist bekannt, wenn seine
    ``QuarterObs`` gilt und ``grid_w`` eine endliche Zahl ist. Die Regeln (Präzisierungen 9, 10, 12
    und 14):

    - **Boost:** Der Netzanteil der Wallboxen der Autos in Boost zählt außerhalb des Budgets. Der
      Wächter integriert ihn nach dem Halteprinzip der Abrechnung: Der Anteil des vorigen Schritts
      gilt bis zu diesem, wenn der vorige Schritt eine bekannte Leistung hatte, in derselben
      Viertelstunde lag und der Abstand über 0 und höchstens ``HOLD_MAX_S`` liegt, so weit wie die
      Abrechnung integriert (``SlotAccumulator``). Was dort in ``grid_wh`` fehlt, darf er nicht als
      Boost davon abziehen. ``E_bisher`` ist ``grid_wh`` minus diese Energie, nicht unter 0. Das
      Mittel der letzten ``MEAN_S`` Sekunden nimmt in jedem Schritt mit bekannter Leistung
      ``grid_w`` minus den Anteil dieses Schritts auf, auch in einer lückenhaften Viertelstunde: So
      ist es aktuell, wenn die nächste beginnt.
    - **Lücken:** Eine Viertelstunde ist lückenhaft bis zu ihrem Ende, sobald in einem Schritt
      mehr als ``GAP_S`` seit dem letzten Schritt mit bekannter Leistung vergangen sind; gab es in
      ihr noch keinen, zählt ihr Beginn. So ist auch eine Viertelstunde lückenhaft, in der der
      erste Schritt später als ``GAP_S`` nach ihrem Beginn kommt (der Start des core mitten
      drin). Ruhte die Regelung (``idle``) oder sprang die Wanduhr innerhalb der Viertelstunde
      zurück, ist sie ebenfalls lückenhaft. Die nächste beginnt normal: Ein Schritt in einer
      anderen Viertelstunde als der vorige beginnt diese neu, ohne Lücke, ohne Boost-Energie und
      nicht überschritten.
    - **Budget:** ``P_zul = (Ziel · 0,25 h − E_bisher) / t_rest − peak_margin_kw`` mit
      ``t_rest = max(1 s, Ende − wall_now)``. Als ``PeakLimits.budget_w`` geht es hinaus, wenn der
      SOC bekannt ist und über ``reserve_soc_pct + reserve_guard_pct`` liegt; sonst ist es
      ``None``. Health zeigt ``P_zul`` in jedem Fall. Es ist nie NaN oder ±∞: Ist ``P_zul`` keine
      endliche Zahl (``grid_wh`` so groß, dass die Rechnung überläuft), rechnet der Schritt nicht.
    - **Hochrechnung und Senkung:** Die Hochrechnung ist ``(E_bisher + Mittel · t_rest) / 0,25 h``.
      Die verlangte Senkung ist ``max(Mittel, Basis + W) − P_zul``, bei unbekanntem ``W`` oder
      unbekannter Basis oder wenn ``Basis + W`` keine endliche Zahl ist ``Mittel − P_zul``; Health
      zeigt sie mindestens 0. Beide brauchen das Mittel; ``P_zul`` braucht es nicht. In diesem Teil
      ändert die Senkung keine Grenze.
    - **Überschritten:** Liegt die Hochrechnung ``EXCEEDED_AFTER_S`` lang ohne Unterbrechung über
      dem Ziel, ist die Viertelstunde überschritten, bis zu ihrem Ende, auch wenn danach eine Lücke
      folgt oder die ``QuarterObs`` fehlt. Gezählt wird nur in Schritten, die rechnen; ein Schritt,
      der nicht rechnet, unterbricht. Der Alarm ``peak.exceeded`` nennt die Hochrechnung in diesem
      Moment; Text und ``since`` bleiben bis zum Ende der Viertelstunde fest.

    Ein Schritt rechnet, wenn seine ``QuarterObs`` gilt, die Viertelstunde keine Lücke hat und
    ``P_zul`` endlich ist; nur dann gibt es Hochrechnung, Senkung und ``budget_w``, sonst
    ``NO_LIMITS``. Der Zustand ist der erste, der zutrifft: ``off`` (kein Ziel), ``exceeded``,
    ``gap`` (der Schritt rechnet nicht), sonst ``active``.

    Die Wanduhr darf springen: Ein Sprung zurück beginnt das Mittel neu (``MovingAverage`` verlangt
    eine steigende Zeit) und macht die laufende Viertelstunde lückenhaft, ein Schritt im selben
    Moment wie der vorige zählt keine Boost-Energie. Der Zustand lebt nur im Speicher. Nicht für
    mehrere Threads gedacht.
    """

    def __init__(self, control: ControlConfig, reserve_soc_pct: float) -> None:
        self._target_kw = control.peak_target_kw
        self._margin_w = control.peak_margin_kw * 1000.0
        # Über diesem SOC geht das Budget an den Akku (Präzisierung 12).
        self._budget_above_pct = reserve_soc_pct + control.reserve_guard_pct
        self._mean = MovingAverage(MEAN_S)
        self._prev: _Prev | None = None  # der vorige Schritt; ``None`` vor dem ersten
        self._running: _Running | None = None  # die Viertelstunde des letzten Schritts
        self._last: _Last | None = None  # der letzte Schritt für Health

    def step(
        self,
        wall_now: datetime,
        quarter: QuarterObs | None,
        cars: Sequence[PeakCar],
        wanted_w: float | None,
        base_w: float | None,
        soc_pct: float | None,
    ) -> PeakLimits:
        """Ein Zyklus zum Zeitpunkt ``wall_now`` (Wanduhr): misst, rechnet und gibt Grenzen vor.

        - ``quarter``: die laufende Viertelstunde; ``None`` heißt nicht da
        - ``cars``: die Autos; hier zählen die in Boost
        - ``wanted_w``: der gewollte Netzbezug ``W`` vor dem Wächter in W, ``base_w``: die Basis
          (``ess.grid_setpoint_w``); ist einer unbekannt, zählt für die Senkung nur das Mittel
        - ``soc_pct``: der SOC des Akkus in %; unbekannt gibt kein Budget für den Akku
        """
        target_kw = self._target_kw
        if target_kw is None:
            return NO_LIMITS
        target_w = target_kw * 1000.0
        start, end = slot_start(wall_now), slot_end(wall_now)
        obs = quarter if quarter is not None and quarter.start == start else None
        energy_wh = None if obs is None else finite_number(obs.grid_wh)
        power_w = None if obs is None or energy_wh is None else finite_number(obs.grid_w)
        boost_w = 0.0 if obs is None or power_w is None else _boost_w(obs.wallbox_grid_w, cars)

        running = self._follow(wall_now, start, power_w is not None, boost_w)
        if power_w is not None:
            self._mean.add(wall_now, power_w - boost_w)
        calc = None
        if energy_wh is not None and not running.gappy:
            energy_wh = max(0.0, energy_wh - running.boost_wh)
            calc = self._calc(wall_now, end, energy_wh, wanted_w, base_w, target_w)
        self._watch(running, wall_now, end, calc, target_w)

        if running.exceeded is not None:
            state: PeakState = "exceeded"
        else:
            state = "gap" if calc is None else "active"
        self._last = _Last(state, end, calc)
        return self._limits(calc, soc_pct)

    def idle(self) -> None:
        """Ein Zyklus ohne Regelung: Die laufende Viertelstunde ist lückenhaft bis zu ihrem Ende.

        Health zeigt dann ``gap`` ohne Werte, bei einer überschrittenen Viertelstunde ``exceeded``.
        Eine spätere Viertelstunde beginnt normal. Vor dem ersten Schritt ändert sich nichts.
        """
        running, last = self._running, self._last
        if running is None or last is None:
            return
        running.gappy = True
        running.over_since = None
        state: PeakState = "gap" if running.exceeded is None else "exceeded"
        self._last = _Last(state, last.quarter_end, None)

    def health(self) -> dict[str, Any]:
        """Der Stand des letzten Schritts (Design 9); was ohne Wert ist, ist ``None``.

        - ``state``: ``off`` ohne Ziel; sonst ``gap`` vor dem ersten Schritt und danach der Zustand
          des letzten Schritts (nach ``idle`` der neue)
        - ``target_kw``: das Ziel
        - ``energy_kwh``: ``E_bisher`` ohne Boost; ``projected_kw``: die Hochrechnung;
          ``budget_kw``: ``P_zul``, auch unter der Schwelle des SOC; ``reduction_kw``: die verlangte
          Senkung, mindestens 0. Alle vier gibt es nur nach einem Schritt, der rechnet;
          ``projected_kw`` und ``reduction_kw`` zudem nur mit Mittel.
        - ``quarter_end``: das Ende der Viertelstunde des letzten Schritts, ISO in UTC; mit Ziel und
          nach dem ersten Schritt
        """
        last = self._last
        if self._target_kw is None:
            state: PeakState = "off"
        else:
            state = "gap" if last is None else last.state
        values = _NO_VALUES if last is None or last.calc is None else last.calc.health()
        return {
            "state": state,
            "target_kw": self._target_kw,
            **values,
            "quarter_end": None if last is None else last.quarter_end.isoformat(),
        }

    def alarms(self) -> list[Alarm]:
        """``peak.exceeded``, solange der Zustand ``exceeded`` ist (Präzisierung 14)."""
        running = self._running
        exceeded = None if running is None else running.exceeded
        return [] if exceeded is None else [exceeded]

    def _follow(self, now: datetime, start: datetime, known: bool, boost_w: float) -> _Running:
        """Zählt den Schritt zur laufenden Viertelstunde: Boost-Energie und Lücken."""
        prev = self._prev
        jumped_back = prev is not None and now < prev.time
        if jumped_back:
            self._mean.clear()  # Die Wanduhr sprang zurück: Das Fenster des Mittels beginnt neu.
        running = self._running
        if running is None or running.start != start:
            running = self._running = _Running(start)  # die erste oder eine andere Viertelstunde
        elif jumped_back:
            # Innerhalb der Viertelstunde zurück: Boost-Energie und Mittel passen nicht mehr.
            running.gappy = True
        if prev is not None and prev.known and prev.start == start:
            interval_s = (now - prev.time).total_seconds()
            if 0.0 < interval_s <= HOLD_MAX_S:
                running.boost_wh += prev.boost_w * interval_s / 3600.0
        since = running.start if running.last_known is None else running.last_known
        if (now - since).total_seconds() > GAP_S:
            running.gappy = True
        if known:
            running.last_known = now
        self._prev = _Prev(now, start, known, boost_w)
        return running

    def _calc(
        self,
        now: datetime,
        end: datetime,
        energy_wh: float,
        wanted_w: float | None,
        base_w: float | None,
        target_w: float,
    ) -> _Calc | None:
        """``P_zul``, Hochrechnung und verlangte Senkung eines Schritts, der rechnet.

        ``None``, wenn ``P_zul`` keine endliche Zahl ist: Das Budget ist nie NaN oder ±∞.
        """
        rest_s = max(_MIN_REST_S, (end - now).total_seconds())
        allowed_w = finite_number(
            (target_w * _QUARTER_H - energy_wh) * 3600.0 / rest_s - self._margin_w
        )
        if allowed_w is None:
            return None  # ein absurdes ``grid_wh`` lässt die Rechnung überlaufen
        mean_w = self._mean.value(now)
        if mean_w is None:
            return _Calc(energy_wh, allowed_w, None, None)
        projection_w = (energy_wh + mean_w * rest_s / 3600.0) / _QUARTER_H
        wanted, base = finite_number(wanted_w), finite_number(base_w)
        with_base_w = None if wanted is None or base is None else finite_number(base + wanted)
        need_w = mean_w if with_base_w is None else max(mean_w, with_base_w)
        return _Calc(energy_wh, allowed_w, projection_w, need_w - allowed_w)

    def _watch(
        self, running: _Running, now: datetime, end: datetime, calc: _Calc | None, target_w: float
    ) -> None:
        """Zählt die Hochrechnung über dem Ziel und setzt „überschritten“."""
        projection_w = None if calc is None else calc.projection_w
        over = projection_w is not None and projection_w > target_w
        running.over_since = held_since(running.over_since, over, now)
        if projection_w is None or running.exceeded is not None:
            return
        if lasted(running.over_since, now, EXCEEDED_AFTER_S):
            until = end.astimezone(LOCAL_TZ).strftime("%H:%M")
            text = (
                f"15-min-Ziel überschritten: {de(round(projection_w / 1000.0, 1))} kW "
                f"in der Viertelstunde bis {until}."
            )
            running.exceeded = Alarm(PEAK_EXCEEDED, Severity.WARNING, text, now)

    def _limits(self, calc: _Calc | None, soc_pct: float | None) -> PeakLimits:
        """Die Grenzen eines Schritts: Das Budget geht an den Akku, wenn der SOC es zulässt."""
        soc = finite_number(soc_pct)
        if calc is None or soc is None or soc <= self._budget_above_pct:
            return NO_LIMITS
        return PeakLimits(budget_w=calc.allowed_w)


def _boost_w(wallbox_grid_w: Mapping[str, float], cars: Sequence[PeakCar]) -> float:
    """Der Netzanteil der Autos in Boost zusammen in W.

    Ein Anteil, der keine endliche Zahl ist, zählt 0: Es wird dann weniger abgezogen, nichts
    verschwiegen.
    """
    shares = (wallbox_grid_w.get(car.wallbox, 0.0) for car in cars if car.mode is Effective.BOOST)
    return sum((finite_number(share) or 0.0 for share in shares), 0.0)


def _kw(watts: float | None) -> float | None:
    """Die Leistung in kW, ohne Rundung; ``None`` bleibt ``None``."""
    return None if watts is None else watts / 1000.0
