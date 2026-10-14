"""Akku-Sollwert: Fehlbetrag der Autos, Unterstützung und erlaubter Akku-Anteil.

Plan 2c-2a, Design 5.2, Präzisierungen 4 und 5. Reine Rechnungen mit Leistungen in W:

- ``BatteryCar``: ein Auto für den Akku-Sollwert mit Wallbox, geltendem Modus und Leistung
- ``deficits``: der Fehlbetrag je Auto, nachdem die PV das Haus und die Autos davor gedeckt hat
- ``Support``: der Stand der Unterstützung mit SOC-Hysterese und Dauer der Überbrückung
- ``allowed``: der Anteil, den der Akku je Auto liefern darf

Der Fehlbetrag eines Autos ist die Leistung, die es aus Akku oder Netz bezieht. Der Akku liefert
davon den erlaubten Anteil; was darüber hinausgeht, ``max(0, Fehlbetrag − erlaubt)``, kommt aus dem
Netz. Die Hauslast deckt der Akku immer.

Ein fehlender Wert ist ``None`` (unbekannt), ebenso ein Wert, der keine endliche Zahl ist
(``finite_number``). Die Schicht ist rein: Die Zeit kommt als Argument, auf der Uhr der
Safety-Schicht, und steigt von Aufruf zu Aufruf.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from bindaems.core.control.charge_modes import Effective
from bindaems.core.control.timing import held_since
from bindaems.shared.config import ControlConfig
from bindaems.shared.numbers import finite_number

BRIDGE_END_S: Final = 60.0  # so lange ohne Fehlbetrag, dann ist die Überbrückung beendet

SupportReason = Literal["on", "soc", "time", "mode"]  # der Stand der Unterstützung (Health)

# Diese Modi bekommen Akkuleistung, wenn die Unterstützung gilt (Design 5.2).
SUPPORT_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})


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


def deficits(pv_w: float, house_w: float, cars: Sequence[BatteryCar]) -> dict[str, float] | None:
    """Der Fehlbetrag je Wallbox in der Reihenfolge von ``cars`` (Präzisierung 4).

    Die PV deckt zuerst die Hauslast, dann die Autos in der Reihenfolge von ``cars`` (der
    Priorität), wie ``allocate`` der Abrechnung. Was einem Auto danach fehlt, ist sein Fehlbetrag.
    Negative PV, Hauslast und Leistung zählen als 0; ``pv_w`` und ``house_w`` sind bekannt, sonst
    rechnet der Aufrufer nicht.

    Fehlt die Leistung eines Autos und lädt es nicht sicher nicht (``charging`` ist nicht
    ``False``), ist der gewollte Netzbezug unbekannt: Das Ergebnis ist ``None``, denn ihre Leistung
    bestimmt auch, was für die Autos hinter ihm von der PV bleibt. Ein Auto, das sicher nicht
    lädt, zählt ohne Leistung mit 0 W.
    """
    rest_w = max(max(pv_w, 0.0) - max(house_w, 0.0), 0.0)  # was nach dem Haus von der PV bleibt
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
        if self._bridging_since is not None and _lasted(self._clear_since, now, BRIDGE_END_S):
            self._bridging_since = None
        if has_deficit and self._bridging_since is None:
            self._bridging_since = now


def _lasted(since: datetime | None, now: datetime, limit_s: float) -> bool:
    """Ob etwas seit ``since`` mindestens ``limit_s`` Sekunden gilt; ohne Zeitpunkt nein."""
    return since is not None and (now - since).total_seconds() >= limit_s


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
