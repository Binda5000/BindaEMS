"""PV-Überschuss und seine Verteilung auf die Autos (Design 6.1, 6.2, 6.5, Präzisierung 7).

Zwei reine Rechnungen; Leistungen sind in W, Ströme in ganzen A je Phase des Autos:

- ``surplus_w``: der Überschuss ``P_verf``, den die Autos zusammen bekommen (6.1)
- ``share_surplus``: seine Verteilung auf die Autos in der Reihenfolge ihrer Priorität (6.2), an
  einer EVCS mit Hysterese beim Erhöhen (6.5)

Vorzeichen wie überall: Netzleistung positiv bei Bezug, Akkuleistung positiv beim Laden. Ein
fehlender Wert ist ``None`` (unbekannt), ebenso ein Wert, der keine endliche Zahl ist
(``finite_number``). Die Grenzen des Phasenwächters kennt die Verteilung nicht; sie gelten erst
danach (Design 6.2, Punkt 4).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from bindaems.shared.control import SurplusOrder, SurplusPriority
from bindaems.shared.numbers import finite_number


def surplus_w(
    *,
    cars_w: float | None,
    grid_w: float | None,
    battery_w: float | None,
    soc_pct: float | None,
    priority: SurplusPriority,
    margin_w: float,
) -> float | None:
    """Der Überschuss ``cars_w − grid_w + anrechenbar − margin_w`` in W (Design 6.1).

    ``cars_w`` ist die Ladeleistung aller Autos jetzt, ``grid_w`` die Netzleistung (positiv bei
    Bezug), ``battery_w`` die Akkuleistung (positiv beim Laden) und ``margin_w`` der Abzug
    ``control.surplus_margin_w``. ``anrechenbar`` ist die Akkuleistung, die der Überschuss mitzählt:

    - Entlädt der Akku, zählt seine Leistung immer, und zwar negativ. So rechnet der Anteil eines
      PV-Autos nie mit Leistung aus dem Akku.
    - Lädt der Akku, zählt seine Leistung nur, wenn die Autos Vorrang haben: bei ``cars_first``
      immer, bei ``battery_first`` nie, bei ``battery_until_soc`` ab einem bekannten SOC von
      mindestens ``priority.soc_pct``. Ein unbekannter SOC zählt nicht.

    Fehlt ``cars_w``, ``grid_w`` oder ``battery_w``, ist der Überschuss ``None``. Er kann auch
    negativ sein.
    """
    cars, grid, battery = finite_number(cars_w), finite_number(grid_w), finite_number(battery_w)
    if cars is None or grid is None or battery is None:
        return None
    counted = _counted_battery_w(battery, finite_number(soc_pct), priority)
    return cars - grid + counted - margin_w


def _counted_battery_w(battery_w: float, soc_pct: float | None, priority: SurplusPriority) -> float:
    """Die Akkuleistung, die der Überschuss mitzählt (Design 6.1)."""
    if battery_w < 0:
        return battery_w  # Entladen zählt immer, und zwar negativ
    return battery_w if _cars_have_priority(soc_pct, priority) else 0.0


def _cars_have_priority(soc_pct: float | None, priority: SurplusPriority) -> bool:
    """Ob die Autos vor dem Akku bekommen, was der Akku gerade lädt."""
    if priority.kind is SurplusOrder.CARS_FIRST:
        return True
    if priority.kind is SurplusOrder.BATTERY_UNTIL_SOC:
        return soc_pct is not None and soc_pct >= priority.soc_pct
    return False  # battery_first


@dataclass(frozen=True)
class SurplusCar:
    """Ein Auto in der Verteilung des Überschusses (Design 6.2, Präzisierung 7).

    - ``key``: Schlüssel des Autos, in einer Verteilung eindeutig
    - ``full_power``: Sofort oder Boost; das Auto bekommt ``max_a``
    - ``minimum``: Min+PV; das Auto bekommt mindestens ``min_a``, auch ohne Überschuss
    - ``phases``: die Phasen, auf denen das Auto lädt; sie legen die Leistung je Ampere fest
    - ``min_a``, ``max_a``: Mindest- und Höchststrom des Autos
    - ``holding_min``: ein PV-Auto in seinen Stopp-Minuten; es lädt noch mit dem Mindeststrom und
      verbraucht ihn vom Überschuss, auch wenn sein Anteil darunter liegt
    - ``held_a``, ``hysteresis_a``: die Hysterese an einer EVCS (Design 6.5). ``held_a`` ist der
      Strom, den die Regelung zuletzt angefordert hat, ``None`` ohne; dann wird nur abgerundet. Der
      Anteil sinkt sofort unter ihn, steigt aber erst ab ``held_a + 1 + hysteresis_a`` A.
    """

    key: str
    full_power: bool
    minimum: bool
    phases: int
    min_a: int
    max_a: int
    holding_min: bool = False
    held_a: int | None = None
    hysteresis_a: float = 0.0


def share_surplus(
    available_w: float | None, cars: Sequence[SurplusCar], voltage_v: float
) -> dict[str, int | None]:
    """Teilt den Überschuss ``available_w`` den Autos in der Reihenfolge von ``cars`` zu.

    Das Ergebnis nennt je Auto seinen Strom in ganzen A, in der Reihenfolge von ``cars``. Dabei ist
    ``U`` die Spannung ``voltage_v`` und ``n`` die Zahl der Phasen des Autos; 1 A hat die Leistung
    ``U × n``:

    1. Autos mit ``full_power`` bekommen ``max_a``. Ihre Leistung ``max_a × U × n`` geht vorab vom
       Rest ab, wo sie auch stehen. Der Phasenwächter bleibt außen vor (Präzisierung 7).
    2. Die übrigen kommen der Reihe nach. Ihr Strom ist ``clamp(floor(Rest / (U × n)), 0, max_a)``,
       mit ``held_a`` nach der Hysterese (``SurplusCar``). Bei ``minimum`` ist er mindestens
       ``min_a``. Liegt er unter ``min_a``, bleibt er so stehen.
    3. Vom Rest geht danach der Strom ab, den das Auto verbraucht: sein Anteil, wenn er mindestens
       ``min_a`` ist. Liegt er darunter, ``min_a`` mit ``holding_min``, sonst nichts: Ein Auto unter
       dem Mindeststrom lädt nicht und lässt den Rest den folgenden Autos.

    Der Rest kann dabei negativ werden, etwa durch den Mindeststrom ohne Überschuss. Die folgenden
    PV-Autos bekommen dann 0 (mit ``minimum`` ``min_a``).

    Ist ``available_w`` ``None``, ist der Überschuss unbekannt: Dann bekommt ein PV-Auto ``None``,
    eines mit ``minimum`` ``min_a`` und eines mit ``full_power`` ``max_a``.

    ``voltage_v`` muss größer als 0 sein, die Phasen mindestens 1, und die Schlüssel der Autos
    müssen eindeutig sein.
    """
    available = finite_number(available_w)
    if available is None:
        return {car.key: _unknown_share(car) for car in cars}

    rest_w = available - sum(_power_w(car, car.max_a, voltage_v) for car in cars if car.full_power)
    shares: dict[str, int | None] = {}
    for car in cars:
        if car.full_power:
            shares[car.key] = car.max_a
            continue
        current_a = _share_a(car, rest_w, voltage_v)
        shares[car.key] = current_a
        rest_w -= _power_w(car, _used_a(car, current_a), voltage_v)
    return shares


def _unknown_share(car: SurplusCar) -> int | None:
    """Der Strom des Autos bei unbekanntem Überschuss."""
    if car.full_power:
        return car.max_a
    return car.min_a if car.minimum else None


def _share_a(car: SurplusCar, rest_w: float, voltage_v: float) -> int:
    """Der Anteil des PV-Autos am Rest: abgerundet, nie negativ, höchstens ``max_a``.

    Mit ``held_a`` bleibt er dort, bis der Rest ihn um 1 A plus ``hysteresis_a`` übersteigt
    (``_hysteresis``). Bei ``minimum`` ist er mindestens ``min_a``; sonst darf er darunter liegen.
    """
    exact_a = rest_w / (voltage_v * car.phases)
    current_a = math.floor(exact_a) if car.held_a is None else _hysteresis(car, car.held_a, exact_a)
    current_a = min(max(current_a, 0), car.max_a)
    return max(current_a, car.min_a) if car.minimum else current_a


def _hysteresis(car: SurplusCar, held_a: int, exact_a: float) -> int:
    """Die Hysterese (Design 6.5): ``exact_a`` ist der Rest in A, ``held_a`` der gehaltene Strom.

    Unter ``held_a`` sinkt der Strom sofort auf abgerundet ``exact_a``. Erhöht wird erst ab
    ``held_a + 1 + hysteresis_a``, und dann auf abgerundet ``exact_a − hysteresis_a``: So liegt der
    neue Strom wieder mindestens ``hysteresis_a`` unter dem Rest. Dazwischen bleibt ``held_a``.
    """
    current_a = math.floor(exact_a)
    if current_a < held_a:
        return current_a
    return max(math.floor(exact_a - car.hysteresis_a), held_a)


def _used_a(car: SurplusCar, current_a: int) -> int:
    """Der Strom, den das Auto vom Rest verbraucht: sein Anteil, ``min_a`` oder nichts."""
    if current_a >= car.min_a:
        return current_a
    return car.min_a if car.holding_min else 0


def _power_w(car: SurplusCar, current_a: int, voltage_v: float) -> float:
    """Die Leistung des Autos bei diesem Strom auf allen seinen Phasen."""
    return current_a * voltage_v * car.phases
