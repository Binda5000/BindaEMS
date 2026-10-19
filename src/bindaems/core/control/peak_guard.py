"""15-min-Wächter: Messung, Budget, Hochrechnung, Zustände, Drosselung und Start-Sperre.

Plan 2c-2a, Design 6.1 bis 6.4 und 9, Präzisierungen 9 bis 14. Leistungen in W, Energien in Wh;
``health`` zeigt kW und kWh.

- ``QuarterObs``: die laufende Viertelstunde, Energie und Leistung aus der Abrechnung, der
  Netzanteil je Wallbox mit Boost zuerst
- ``PeakCar``: ein Auto für den Wächter
- ``PeakLimits`` und ``NO_LIMITS``: was der Wächter den anderen Bausteinen vorgibt
- ``Cap``, ``throttle``, ``lift``, ``pending_w`` und ``no_start``: Drosselung, Aufheben und
  Start-Sperre als reine Rechnungen (Präzisierungen 11 und 13)
- ``guard_target_kw``: das Ziel eines Schritts: das Budget des Plan-Slots oder
  ``control.peak_target_kw``
- ``PeakGuard``: Messung, Lücken, ``P_zul``, Hochrechnung, verlangte Senkung, Budget für den Akku,
  die Grenzen je Fahrzeug über die Viertelstunde, die Start-Sperre und der Zustand ``off``, ``gap``,
  ``active`` oder ``exceeded``

Ein fehlender Wert ist ``None`` (unbekannt), ebenso ein Wert, der keine endliche Zahl ist
(``finite_number``). Die Schicht ist rein: Die Zeit kommt als Argument, auf der Wanduhr, wie die
Viertelstunden der Abrechnung. Springt sie, stürzt der Wächter nicht ab.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.plan_exec import ActiveSlot
from bindaems.core.control.tesla_policy import MovingAverage
from bindaems.core.control.timing import held_since, lasted
from bindaems.shared.config import ControlConfig
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import HOLD_MAX_S, LOCAL_TZ, slot_end, slot_start

GAP_S: Final = 10.0  # mehr als so lange ohne bekannte Leistung: Die Viertelstunde ist lückenhaft
MEAN_S: Final = 30.0  # das Fenster des Mittels vom Netzbezug ohne Boost
EXCEEDED_AFTER_S: Final = 60.0  # so lange ununterbrochen über dem Ziel: überschritten
SETTLE_S: Final = 30.0  # so lange nach der letzten Änderung einer Grenze keine neue Änderung
RELEASE_W: Final = 1000.0  # so viel Spielraum über ihrer Senkung hinaus hebt eine Grenze auf
LAST_S: Final = 120.0  # in den letzten so vielen Sekunden der Viertelstunde gilt die Start-Sperre
PEAK_EXCEEDED: Final = "peak.exceeded"  # die ID des Alarms (Design 9)

PeakState = Literal["off", "gap", "active", "exceeded"]  # der Zustand des Wächters (Health)

_QUARTER_H: Final = 0.25  # die Länge einer Viertelstunde in h
_MIN_REST_S: Final = 1.0  # kürzeste Restzeit der Rechnung: Am Ende wird nicht durch 0 geteilt
# Die Stufen der Drosselung in ihrer Reihenfolge (Design 6.3, „Geplant“ nach Plan 4b,
# Präzisierung 8); Boost und Aus gehören zu keiner.
_LEVELS: Final = (Effective.PV, Effective.MIN_PV, Effective.PLANNED, Effective.NOW)


@dataclass(frozen=True)
class QuarterObs:
    """Die laufende Viertelstunde (Präzisierung 9).

    Energie und Leistung zählt sie wie die Abrechnung. Den Netzanteil je Wallbox rechnet die
    Verdrahtung (``RegulationLink``) mit Wallboxen in Boost aus einer zweiten Zuordnung, in der sie
    PV und Akku zuerst bekommen; sonst ist es der Anteil der Abrechnung.

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

    Die Messung liest nur ``wallbox`` und ``mode``: Sie zieht den Netzanteil der Autos in Boost ab.
    Die übrigen Felder brauchen Drosselung und Start-Sperre.
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
    - ``caps_w``: die Leistungsgrenze je Fahrzeug in W (Präzisierung 11)
    - ``no_start``: die Fahrzeuge, die nicht starten sollen (Präzisierung 13)

    Grenzen und Start-Sperre nennen nie ein Fahrzeug in Boost oder Aus.
    """

    budget_w: float | None = None
    caps_w: Mapping[str, float] = field(default_factory=dict)
    no_start: frozenset[str] = frozenset()


NO_LIMITS: Final = PeakLimits()  # keine Vorgabe: Der Wächter ruht, oder die Messung fehlt


@dataclass(frozen=True)
class Cap:
    """Die Grenze eines gedrosselten Autos (Präzisierung 11); Leistungen in W.

    - ``cap_w``: die Leistungsgrenze
    - ``cut_w``: die gesamte Senkung des Autos, seit es die Grenze hat; an ihr misst sich das
      Aufheben, und mit Budget für den Akku zählt sie zur gemerkten Senkung ``S`` des Bedarfs
      (``PeakGuard``, Präzisierung 10)
    - ``mode``: der Modus, in dem das Auto gedrosselt wurde; nur in ihm gilt die Grenze
    """

    cap_w: float
    cut_w: float
    mode: Effective


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
    - ``caps``: die Grenzen je Fahrzeug; ``changed``: der letzte Schritt, in dem eine Grenze
      entstand, sank oder aufgehoben wurde, ``None`` vor der ersten Änderung
    """

    start: datetime
    gappy: bool = False
    last_known: datetime | None = None
    boost_wh: float = 0.0
    over_since: datetime | None = None
    exceeded: Alarm | None = None
    caps: dict[str, Cap] = field(default_factory=dict)
    changed: datetime | None = None


@dataclass(frozen=True)
class _Calc:
    """Die Rechnung eines Schritts; Energie in Wh, Leistungen in W.

    - ``energy_wh``: ``E_bisher`` ohne Boost
    - ``allowed_w``: ``P_zul``; mit dem Budget unter 0 ist es negativ
    - ``rest_s``: ``t_rest``, die Restzeit der Viertelstunde in s, mindestens 1 s
    - ``mean_w``: das Mittel des Netzbezugs ohne Boost; ``None`` ohne einen Wert im Fenster
    - ``projection_w``: die Hochrechnung auf die mittlere Leistung der Viertelstunde; ``None`` ohne
      Mittel
    - ``reduction_w``: die verlangte Senkung ``Bedarf − P_zul``, auch unter 0; ``None`` ohne Mittel
    """

    energy_wh: float
    allowed_w: float
    rest_s: float
    mean_w: float | None
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


def guard_target_kw(control: ControlConfig, active: ActiveSlot | None) -> float | None:
    """Das Ziel des Wächters für einen Schritt (Plan 3c, Präzisierung 9).

    Das Budget des gültigen Plan-Slots, wenn es eine Zahl ist; sonst ``control.peak_target_kw``.
    Ohne beides ``None``: Der Wächter ruht.
    """
    if active is not None and active.budget_kw is not None:
        return active.budget_kw
    return control.peak_target_kw


class PeakGuard:
    """Der 15-min-Wächter: misst die Viertelstunde, rechnet, drosselt und entscheidet den Zustand.

    Das Ziel bekommt jeder Schritt (``guard_target_kw``); es kann sich innerhalb einer
    Viertelstunde ändern, ``P_zul`` folgt dem Ziel des Schritts. Ohne Ziel ruht er ganz:
    ``NO_LIMITS`` und Zustand ``off``; der Schritt vergisst dann die laufende Viertelstunde, ihre
    Grenzen und einen Alarm ``peak.exceeded``. Sonst ist die laufende Viertelstunde
    ``slot_start(wall_now)`` bis ``slot_end(wall_now)``. Eine ``QuarterObs`` gilt, wenn sie da ist,
    ihr ``start`` der Beginn dieser Viertelstunde ist und ``grid_wh`` eine endliche Zahl; sonst
    zählt sie wie keine. Die Leistung eines Schritts ist bekannt, wenn seine ``QuarterObs`` gilt
    und ``grid_w`` eine endliche Zahl ist. Die Regeln (Präzisierungen 9 bis 14):

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
      ``t_rest = max(1 s, Ende − wall_now)``. Der Akku hat ein Budget, wenn der SOC bekannt ist
      und über ``reserve_soc_pct + reserve_guard_pct`` liegt; dann geht ``P_zul`` als
      ``PeakLimits.budget_w`` hinaus, sonst ist es ``None``. Health zeigt ``P_zul`` in jedem Fall.
      Es ist nie NaN oder ±∞: Ist ``P_zul`` keine endliche Zahl (``grid_wh`` so groß, dass die
      Rechnung überläuft), rechnet der Schritt nicht.
    - **Hochrechnung und Senkung:** Die Hochrechnung ist ``(E_bisher + Mittel · t_rest) / 0,25 h``,
      die verlangte Senkung ``Bedarf − P_zul``; Health zeigt sie mindestens 0. Beide brauchen das
      Mittel; ``P_zul`` braucht es nicht. Der Bedarf ist das Mittel, bei bekanntem ``W`` und
      bekannter Basis und wenn ``Basis + W`` eine endliche Zahl ist das größere von Mittel und dem,
      was von ``Basis + W`` zählt (``C`` ist die feste Grenze von Regel 2, ``ceiling_w``):
      - **mit Budget für den Akku** ``min(Basis + W + S, C) − S``: ``S`` ist die Summe der
        Senkungen ``cut_w`` der Grenzen, die nach dem Aufräumen dieses Schritts bleiben;
        ``Basis + W + S`` ist so der Netzbezug ohne die Senkungen. Was die Autos über ``C`` wollen,
        liefert der Akku wie ohne Ziel (Design 2c-2, Entscheidung 3): Der Wächter senkt insgesamt
        nur um ``min(Basis + W ohne Senkung, C) − P_zul``, einmal und nicht in Stufen.
      - **ohne Budget** hält Regel 2 den Override bei ``C``, und der Netzbezug ist höchstens
        ``min(Basis + W, C)``. Solange ``P_zul`` unter ``C`` liegt, zählt ``Basis + W``: Unter
        ``P_zul`` bringt den Netzbezug dann nur die ganze Senkung. Sonst zählt
        ``min(Basis + W, C)``, und es braucht keine Senkung: Was die Autos über ``C`` wollen,
        liefert der Akku wie ohne Ziel, und ein Akku an seiner Reserve zeigt sich im Mittel.

      ``W`` gibt der Kern ungekappt und unbekannt, solange Victron gesperrt oder nicht steuerbar
      ist (``regulation.peak_wanted_w``): So zählt der Wächter keinen Netzbezug, den kein Override
      durchsetzen kann.
    - **Drosselung:** Die Grenzen je Fahrzeug gelten bis zum Ende der Viertelstunde. Zuerst, vor
      dem Bedarf, verliert ein Auto still seine Grenze, wenn es nicht mehr unter ``cars`` steht
      oder seinen Modus gewechselt hat, auch zwischen PV-Überschuss, Min+PV, Geplant und Sofort:
      Eine Grenze gilt nur im Modus, in dem sie entstand; sonst bliebe etwa ein Auto, das in
      PV-Überschuss auf 0 W gedrosselt wurde, in Sofort unter seinem Boden. Dann, mit Mittel und
      frühestens ``SETTLE_S`` nach der letzten Änderung: Ist die Senkung über 0, drosselt der
      Wächter um ``Senkung − ausstehend`` (``throttle``, ``pending_w``); sonst hebt er mit dem
      Spielraum ``−Senkung`` höchstens eine Grenze auf (``lift``). Eine Grenze, die dabei entsteht,
      sinkt oder wegfällt, ist eine Änderung; die erste einer Viertelstunde darf sofort kommen.
    - **Start-Sperre:** ``no_start`` mit ``t_rest``, ``P_zul`` und dem Mittel.
    - **Überschritten:** Liegt die Hochrechnung ``EXCEEDED_AFTER_S`` lang ohne Unterbrechung über
      dem Ziel, ist die Viertelstunde überschritten, bis zu ihrem Ende, auch wenn danach eine Lücke
      folgt oder die ``QuarterObs`` fehlt. Gezählt wird nur in Schritten, die rechnen; ein Schritt,
      der nicht rechnet, unterbricht. Der Alarm ``peak.exceeded`` nennt die Hochrechnung in diesem
      Moment; Text und ``since`` bleiben bis zum Ende der Viertelstunde fest.

    Ein Schritt rechnet, wenn seine ``QuarterObs`` gilt, die Viertelstunde keine Lücke hat und
    ``P_zul`` endlich ist; nur dann gibt es Hochrechnung, Senkung, ``budget_w``, Grenzen und
    Start-Sperre, sonst ``NO_LIMITS``. Ein Schritt, der nicht rechnet, vergisst die Grenzen und ihre
    letzte Änderung, wie das Ende der Viertelstunde. Die Start-Sperre hängt nicht vom SOC ab, die
    Grenzen nur über den Bedarf. Der Zustand ist der erste, der zutrifft: ``off`` (kein Ziel),
    ``exceeded``, ``gap`` (der Schritt rechnet nicht), sonst ``active``.

    Die Wanduhr darf springen: Ein Sprung zurück beginnt das Mittel neu (``MovingAverage`` verlangt
    eine steigende Zeit) und macht die laufende Viertelstunde lückenhaft, ein Schritt im selben
    Moment wie der vorige zählt keine Boost-Energie. Der Zustand lebt nur im Speicher. Nicht für
    mehrere Threads gedacht.
    """

    def __init__(self, control: ControlConfig, reserve_soc_pct: float, ceiling_w: float) -> None:
        # das Ziel des letzten Schritts; vor dem ersten das der Konfiguration (Health, ``idle``)
        self._target_kw = control.peak_target_kw
        self._margin_w = control.peak_margin_kw * 1000.0
        # Über diesem SOC geht das Budget an den Akku (Präzisierung 12).
        self._budget_above_pct = reserve_soc_pct + control.reserve_guard_pct
        # die feste Grenze ``C`` des Overrides nach Regel 2, ohne das Budget (Präzisierung 10)
        self._ceiling_w = ceiling_w
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
        *,
        target_kw: float | None,
    ) -> PeakLimits:
        """Ein Zyklus zum Zeitpunkt ``wall_now`` (Wanduhr): misst, rechnet und gibt Grenzen vor.

        - ``quarter``: die laufende Viertelstunde; ``None`` heißt nicht da
        - ``cars``: die Autos, je Fahrzeug höchstens eins; die Priorität steht in ``rank``
        - ``wanted_w``: der gewollte Netzbezug ``W`` vor dem Wächter in W, ungekappt; ``base_w``:
          die Basis (``ess.grid_setpoint_w``); ist einer unbekannt, zählt für die Senkung nur das
          Mittel
        - ``soc_pct``: der SOC des Akkus in %; unbekannt gibt kein Budget für den Akku. Ob er eins
          hat, bestimmt ``budget_w`` und den Bedarf der Senkung.
        - ``target_kw``: das Ziel dieses Schritts in kW (``guard_target_kw``); ``None`` heißt, der
          Wächter ruht
        """
        self._target_kw = target_kw
        if target_kw is None:
            self._rest()
            return NO_LIMITS
        target_w = target_kw * 1000.0
        start, end = slot_start(wall_now), slot_end(wall_now)
        obs = quarter if quarter is not None and quarter.start == start else None
        energy_wh = None if obs is None else finite_number(obs.grid_wh)
        power_w = None if obs is None or energy_wh is None else finite_number(obs.grid_w)
        boost_w = 0.0 if obs is None or power_w is None else _boost_w(obs.wallbox_grid_w, cars)
        soc = finite_number(soc_pct)
        funded = soc is not None and soc > self._budget_above_pct  # ein Budget für den Akku

        running = self._follow(wall_now, start, power_w is not None, boost_w)
        if power_w is not None:
            self._mean.add(wall_now, power_w - boost_w)
        calc = None
        if energy_wh is not None and not running.gappy:
            energy_wh = max(0.0, energy_wh - running.boost_wh)
            # Vor dem Bedarf fallen die Grenzen der Autos weg, die gingen oder den Modus wechselten:
            # Ihre Senkung zählt nicht mehr in ``S``.
            running.caps = _kept(running.caps, cars)
            wanted, base = finite_number(wanted_w), finite_number(base_w)
            with_base_w = None if wanted is None or base is None else finite_number(base + wanted)
            cut_w = sum((cap.cut_w for cap in running.caps.values()), 0.0) if funded else None
            calc = self._calc(wall_now, end, energy_wh, target_w, with_base_w, cut_w)
        self._watch(running, wall_now, end, calc, target_w)

        if running.exceeded is not None:
            state: PeakState = "exceeded"
        else:
            state = "gap" if calc is None else "active"
        self._last = _Last(state, end, calc)
        if calc is None:
            running.caps, running.changed = {}, None  # wie am Ende der Viertelstunde
            return NO_LIMITS
        self._adjust(running, wall_now, calc, cars)
        return self._limits(running, calc, cars, funded)

    def target_kw(self) -> float | None:
        """Das Ziel des letzten Schritts in kW, ``None`` ohne Ziel; vor dem ersten Schritt
        ``control.peak_target_kw``."""
        return self._target_kw

    def pressure(self) -> bool:
        """Ob der Wächter im letzten Schritt Druck machte (Plan 6a, Entscheidung 5.2): Zustand
        ``exceeded`` oder eine verlangte Senkung über 0. Vor dem ersten Schritt, ohne Ziel und ohne
        Rechnung (Lücke, ``idle``) zählt nur ``exceeded``."""
        last = self._last
        if last is None:
            return False
        if last.state == "exceeded":
            return True
        reduction_w = None if last.calc is None else last.calc.reduction_w
        return reduction_w is not None and reduction_w > 0

    def _rest(self) -> None:
        """Ein Schritt ohne Ziel: Die laufende Viertelstunde, ihre Grenzen und ihr Alarm fallen weg,
        der Wächter beginnt später ohne Vorgeschichte (mit Lücke, wenn die Viertelstunde läuft)."""
        self._mean.clear()
        self._prev = self._running = self._last = None

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
        - ``target_kw``: das Ziel des letzten Schritts, vor dem ersten ``control.peak_target_kw``
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
        target_w: float,
        with_base_w: float | None,
        cut_w: float | None,
    ) -> _Calc | None:
        """``P_zul``, Hochrechnung und verlangte Senkung eines Schritts, der rechnet.

        - ``with_base_w``: ``Basis + W`` in W; ``None``, wenn ``W`` oder die Basis unbekannt ist
          oder die Summe keine endliche Zahl
        - ``cut_w``: die gemerkte Senkung ``S``, die Summe von ``cut_w`` der Grenzen nach dem
          Aufräumen dieses Schritts, wenn der Akku ein Budget hat; ohne Budget ``None``

        Der Bedarf ist das Mittel oder, mit bekanntem ``with_base_w``, das größere von Mittel und
        dem, was davon zählt (``_counted_w``). ``None``, wenn ``P_zul`` keine endliche Zahl ist:
        Das Budget ist nie NaN oder ±∞.
        """
        rest_s = max(_MIN_REST_S, (end - now).total_seconds())
        allowed_w = finite_number(
            (target_w * _QUARTER_H - energy_wh) * 3600.0 / rest_s - self._margin_w
        )
        if allowed_w is None:
            return None  # ein absurdes ``grid_wh`` lässt die Rechnung überlaufen
        mean_w = self._mean.value(now)
        if mean_w is None:
            return _Calc(energy_wh, allowed_w, rest_s, None, None, None)
        projection_w = (energy_wh + mean_w * rest_s / 3600.0) / _QUARTER_H
        counted_w = None if with_base_w is None else self._counted_w(with_base_w, cut_w, allowed_w)
        need_w = mean_w if counted_w is None else max(mean_w, counted_w)
        return _Calc(energy_wh, allowed_w, rest_s, mean_w, projection_w, need_w - allowed_w)

    def _counted_w(self, with_base_w: float, cut_w: float | None, allowed_w: float) -> float | None:
        """Was vom Netzbezug ``Basis + W`` der Autos für den Bedarf zählt (Präzisierung 10).

        ``C`` ist die feste Grenze von Regel 2, ``allowed_w`` ist ``P_zul``.

        - mit Budget für den Akku (``cut_w`` ist ``S``): ``min(Basis + W + S, C) − S``
        - ohne Budget, solange ``P_zul`` unter ``C`` liegt: ``Basis + W``
        - ohne Budget sonst: ``min(Basis + W, C)``

        ``None``, wenn die Rechnung mit Budget keine endliche Zahl ergibt.
        """
        ceiling_w = self._ceiling_w
        if cut_w is not None:
            return finite_number(min(with_base_w + cut_w, ceiling_w) - cut_w)
        if allowed_w < ceiling_w:
            return with_base_w
        return min(with_base_w, ceiling_w)

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

    def _adjust(
        self, running: _Running, now: datetime, calc: _Calc, cars: Sequence[PeakCar]
    ) -> None:
        """Die Grenzen eines Schritts, der rechnet: drosseln oder aufheben (Präzisierung 11).

        ``running.caps`` sind schon aufgeräumt (``_kept``); was dabei wegfiel, ist keine Änderung.
        """
        caps, reduction_w, changed = running.caps, calc.reduction_w, running.changed
        if reduction_w is None or not (changed is None or lasted(changed, now, SETTLE_S)):
            return
        if reduction_w > 0.0:
            found = throttle(cars, caps, reduction_w - pending_w(cars, caps))
        else:
            found = lift(cars, caps, -reduction_w)
        if found != caps:
            running.caps, running.changed = found, now

    def _limits(
        self, running: _Running, calc: _Calc, cars: Sequence[PeakCar], funded: bool
    ) -> PeakLimits:
        """Die Vorgaben eines Schritts, der rechnet (Präzisierungen 11 bis 13).

        Das Budget geht an den Akku, wenn der SOC es zulässt (``funded``); die Start-Sperre gilt
        unabhängig davon.
        """
        return PeakLimits(
            budget_w=calc.allowed_w if funded else None,
            caps_w={vehicle: cap.cap_w for vehicle, cap in running.caps.items()},
            no_start=no_start(cars, calc.rest_s, calc.allowed_w, calc.mean_w),
        )


def throttle(cars: Sequence[PeakCar], caps: Mapping[str, Cap], amount_w: float) -> dict[str, Cap]:
    """Drosselt die Autos zusammen um höchstens ``amount_w`` (Präzisierung 11).

    Die Reihenfolge ist PV-Überschuss, Min+PV, Geplant, Sofort, innerhalb einer Stufe zuerst das
    Auto mit der niedrigsten Priorität (dem höchsten ``rank``). Je Auto ist ``Ausgang`` das kleinere
    von Grenze und Leistung, ohne bekannte Leistung die Grenze und ohne Grenze die Leistung: Eine
    Grenze weit über der Leistung, etwa nach einem Wechsel auf den Mindeststrom, schluckt so keine
    Senkung. Der Boden ist in PV-Überschuss 0 W, sonst ``min_w``. Das Auto sinkt um
    ``min(Rest, Netzanteil, Ausgang − Boden)``, wenn das über 0 liegt; die neue Grenze ist
    ``Ausgang`` minus diese Senkung, nie unter dem Boden, im Modus des Autos, und ``cut_w`` wächst
    um sie. Ein Auto ohne Netzanteil (ein Anteil, der keine endliche Zahl ist, zählt 0 W) oder ohne
    Ausgang (ohne Grenze und ohne bekannte Leistung) bleibt, wie es ist, ebenso jedes in Boost oder
    Aus. Zurück kommen alle Grenzen, die von ``caps`` mit den neuen.
    """
    found = dict(caps)
    rest_w = amount_w
    for car in _in_order(cars):
        if rest_w <= 0.0:
            break
        cap, power_w = found.get(car.vehicle), finite_number(car.power_w)
        if cap is None or power_w is None:
            start_w = power_w if cap is None else cap.cap_w
        else:
            start_w = min(cap.cap_w, power_w)
        if start_w is None:
            continue
        floor_w = _floor_w(car)
        cut_w = min(rest_w, finite_number(car.grid_w) or 0.0, start_w - floor_w)
        if cut_w > 0.0:
            total_w = cut_w if cap is None else cap.cut_w + cut_w
            found[car.vehicle] = Cap(max(floor_w, start_w - cut_w), total_w, car.mode)
            rest_w -= cut_w
    return found


def lift(cars: Sequence[PeakCar], caps: Mapping[str, Cap], headroom_w: float) -> dict[str, Cap]:
    """Hebt höchstens eine Grenze ganz auf (Präzisierung 11).

    ``headroom_w`` ist der Spielraum ``P_zul − Bedarf`` mit dem Bedarf der verlangten Senkung
    (``PeakGuard``, „Hochrechnung und Senkung“). In umgekehrter Reihenfolge der Drosselung (Sofort,
    Geplant, Min+PV, PV-Überschuss, innerhalb einer Stufe die höchste Priorität zuerst) fällt die
    erste Grenze weg, deren Senkung ``cut_w`` plus ``RELEASE_W`` höchstens der Spielraum ist. Zurück
    kommen die übrigen Grenzen.
    """
    for car in reversed(_in_order(cars)):
        cap = caps.get(car.vehicle)
        if cap is not None and cap.cut_w + RELEASE_W <= headroom_w:
            return {vehicle: kept for vehicle, kept in caps.items() if vehicle != car.vehicle}
    return dict(caps)


def pending_w(cars: Sequence[PeakCar], caps: Mapping[str, Cap]) -> float:
    """Was die gedrosselten Autos ihrer Grenze noch nicht gefolgt sind, in W (Präzisierung 11).

    Je Auto mit Grenze ``max(0, Leistung − Grenze)``; ohne bekannte Leistung gilt es als nicht
    gefolgt, und seine ganze Senkung ``cut_w`` steht aus.
    """
    total_w = 0.0
    for car in cars:
        cap = caps.get(car.vehicle)
        if cap is not None:
            power_w = finite_number(car.power_w)
            total_w += cap.cut_w if power_w is None else max(0.0, power_w - cap.cap_w)
    return total_w


def no_start(
    cars: Sequence[PeakCar], rest_s: float, allowed_w: float, mean_w: float | None
) -> frozenset[str]:
    """Die Fahrzeuge, die nicht starten sollen (Präzisierung 13).

    In den letzten ``LAST_S`` Sekunden der Viertelstunde (``rest_s`` ist ``t_rest``) jedes Auto in
    PV-Überschuss, Min+PV, Geplant oder Sofort, dessen Mindestleistung über ``P_zul − Mittel``
    liegt, ob es lädt oder nicht; die Laderegelung hält nur ein Auto an, das nicht lädt. Ohne Mittel
    keins.
    """
    if rest_s > LAST_S or mean_w is None:
        return frozenset()
    room_w = allowed_w - mean_w
    return frozenset(car.vehicle for car in cars if car.mode in _LEVELS and car.min_w > room_w)


def _kept(caps: Mapping[str, Cap], cars: Sequence[PeakCar]) -> dict[str, Cap]:
    """Die Grenzen, die bleiben: die der Autos unter ``cars``, im Modus, in dem sie sie bekamen.

    Was so wegfällt, weil ein Auto geht oder seinen Modus wechselt (auch nach Boost oder Aus),
    fällt still weg.
    """
    modes = {car.vehicle: car.mode for car in cars}
    return {vehicle: cap for vehicle, cap in caps.items() if modes.get(vehicle) is cap.mode}


def _in_order(cars: Sequence[PeakCar]) -> list[PeakCar]:
    """Die Autos, die der Wächter drosselt, in der Reihenfolge der Drosselung (Design 6.3)."""
    levels = [car for car in cars if car.mode in _LEVELS]
    return sorted(levels, key=lambda car: (_LEVELS.index(car.mode), -car.rank))


def _floor_w(car: PeakCar) -> float:
    """Der Boden der Drosselung: in PV-Überschuss 0 W, in Min+PV, Geplant und Sofort ``min_w``."""
    return 0.0 if car.mode is Effective.PV else car.min_w


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
