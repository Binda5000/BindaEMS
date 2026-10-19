"""Bedarf, späteste Startzeit und Stand des geplanten Ladens je Fahrzeug (Plan 4b).

- ``effective_target``: das Ziel, mit dem gerechnet wird (Ladelimit im Auto darunter zählt)
- ``need_kwh``: die Energie aus dem Netz bis zum Ziel
- ``full_power_w``: die volle Ladeleistung eines Fahrzeugs an einer Wallbox
- ``latest_start``: ab wann das Auto mit voller Leistung laden muss, um das Ziel zu erreichen
- ``PlannedState``: der Stand des geplanten Ladens eines Fahrzeugs in einem Zyklus
- ``PlannedTracker``: führt je Fahrzeug die Merker „späteste Startzeit erreicht“ und „Ziel
  erreicht“ über die Zyklen eines Ansteckvorgangs

Die app rechnet dieselbe Formel (die app importiert nichts aus dem core);
``tests/unit/test_planned_formula.py`` vergleicht beide. Die Schicht ist rein: Sie liest nur ihre
Argumente. Abfahrt und späteste Startzeit stehen auf der Wanduhr ``wall_now``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from bindaems.core.control.vehicle_soc import SocReading, SocSource
from bindaems.shared.config import EvcsConfig, TwcConfig, VehicleConfig
from bindaems.shared.control import VehicleIntent, next_departure

LATEST_START_MARGIN: Final = 1.1  # Zuschlag auf die Ladedauer bei voller Leistung
LATEST_START_BUFFER: Final = timedelta(minutes=15)  # Puffer vor der Abfahrt
TARGET_RESTART_PP: Final = 3.0  # Prozentpunkte unter dem Ziel, ab denen wieder geladen wird

PlannedKind = Literal["plan", "surplus", "due", "reached"]


def effective_target(target: float, limit_pct: float | None, sync: bool) -> float:
    """Das Ziel in %, mit dem gerechnet wird.

    Ohne Abgleich des Ladelimits (``sync`` falsch) stoppt das Auto selbst an einem Limit unter
    dem Ziel; dann ist das bekannte Limit das Ziel (Plan 4b, Entscheidung 11).
    """
    if limit_pct is not None and not sync:
        return float(min(target, limit_pct))
    return float(target)


def need_kwh(soc_pct: float, target_pct: float, usable_kwh: float, efficiency: float) -> float:
    """Die Energie in kWh, die das Auto aus der Wallbox bis zum Ziel braucht.

    ``max(0, Ziel − Ladestand) / 100 · usable_kwh / efficiency``; ``efficiency`` als Anteil.
    """
    return max(0.0, target_pct - soc_pct) / 100.0 * usable_kwh / efficiency


def full_power_w(
    vehicle: VehicleConfig, wallbox: EvcsConfig | TwcConfig, voltage_v: float
) -> float:
    """Die volle Ladeleistung in W: ``min(vehicle.max_a, wallbox.max_a) · U · phases``.

    ``voltage_v`` ist ``grid.voltage_nominal_v``, ``phases`` die des Fahrzeugs.
    """
    return min(vehicle.max_a, wallbox.max_a) * voltage_v * vehicle.phases


def latest_start(departure_at: datetime, need: float, full_w: float) -> datetime | None:
    """Ab hier lädt das Auto mit voller Leistung, um bis ``departure_at`` am Ziel zu sein.

    ``departure_at − need / (full_w / 1000) · LATEST_START_MARGIN h − LATEST_START_BUFFER``.
    ``need`` ist der Bedarf aus der Wallbox (``need_kwh``, der Wirkungsgrad steckt schon darin).
    Ohne Bedarf gibt es keine (``None``).
    """
    if need <= 0:
        return None
    hours = need / (full_w / 1000.0) * LATEST_START_MARGIN
    return departure_at - timedelta(hours=hours) - LATEST_START_BUFFER


@dataclass(frozen=True)
class PlannedState:
    """Der Stand des geplanten Ladens eines Fahrzeugs in einem Zyklus.

    - ``state``: ``plan`` (ein gültiger Slot lädt das Auto mit Leistung über 0), ``surplus``
      (PV-Überschuss: vor der spätesten Startzeit oder ein gültiger Slot mit 0 W), ``due``
      (späteste Startzeit erreicht, volle Leistung bis zum Ziel), ``reached`` (Ziel erreicht)
    - ``target_soc_pct``: das wirksame Ziel (``effective_target``)
    - ``departure_at``: die nächste Abfahrt (UTC)
    - ``need_kwh``: der Bedarf bis zum Ziel; ``latest_start``: die späteste Startzeit dafür,
      ``None`` ohne Bedarf
    - ``plan_w``: die Leistung des gültigen Slots für das Auto; ``None`` ohne gültigen Slot
    - ``soc_pct``, ``soc_source``: der Ladestand, mit dem gerechnet wurde, und seine Quelle
    """

    state: PlannedKind
    target_soc_pct: float
    departure_at: datetime
    need_kwh: float
    latest_start: datetime | None
    plan_w: float | None
    soc_pct: float | None
    soc_source: SocSource


@dataclass
class _Memory:
    """Was ein Fahrzeug über die Zyklen eines Ansteckvorgangs behält.

    - ``session``: der Ansteckvorgang, zu dem die Merker gehören
    - ``target``: das wirksame Ziel des letzten Zyklus
    - ``latched``: die späteste Startzeit war erreicht; gilt bis zum Ziel
    - ``reached``: das Ziel war erreicht; gilt, bis der Ladestand ``TARGET_RESTART_PP`` darunter
      fällt
    """

    session: int | None
    target: float
    latched: bool = False
    reached: bool = False


class PlannedTracker:
    """Der Stand des geplanten Ladens je Fahrzeug (Präzisierung 6 von Plan 4b)."""

    def __init__(self) -> None:
        self._memory: dict[str, _Memory] = {}

    def step(
        self,
        key: str,
        *,
        wall_now: datetime,
        session: int | None,
        intent: VehicleIntent,
        soc: SocReading,
        limit_pct: float | None,
        sync: bool,
        full_w: float,
        efficiency: float,
        usable_kwh: float,
        slot_w: float | None,
    ) -> PlannedState:
        """Der Stand des Fahrzeugs ``key`` in diesem Zyklus.

        - ``session``: der Ansteckvorgang (für einen Vorgang immer derselbe Wert)
        - ``soc``: der Ladestand; ``assumed`` rechnet mit 0 %
        - ``limit_pct``, ``sync``: das Ladelimit im Auto und ob der core es angleicht
        - ``full_w``: die volle Ladeleistung (``full_power_w``)
        - ``efficiency``: der Wirkungsgrad als Anteil, ``usable_kwh`` die nutzbare Kapazität
        - ``slot_w``: die Leistung des gültigen Plan-Slots für das Auto; ``None`` ohne gültigen
          Slot oder wenn der Slot das Auto nicht nennt (Ruling R22)

        Reihenfolge: ``reached`` vor ``plan`` vor ``due`` vor ``surplus``. Erreicht ist das Ziel
        nur mit bekanntem Ladestand (``measured`` oder ``estimated``) ab dem Ziel; es bleibt
        erreicht, bis der Ladestand unter Ziel − ``TARGET_RESTART_PP`` fällt. Ohne gültigen Slot
        merkt sich der Tracker ab der spätesten Startzeit ``due`` bis zum Ziel; nur ein gültiger
        Slot mit Leistung über 0 geht dann vor. Ein neuer Ansteckvorgang oder ein höheres Ziel
        setzt beide Merker zurück. Die Abfahrt ist immer die nächste nach ``wall_now``.
        """
        target = effective_target(intent.target_soc_pct, limit_pct, sync)
        departure_at = next_departure(intent.departure, wall_now)
        known = soc.source != "assumed" and soc.pct is not None
        soc_pct = soc.pct if soc.pct is not None else 0.0
        need = need_kwh(soc_pct, target, usable_kwh, efficiency)
        start = latest_start(departure_at, need, full_w)

        memory = self._memory.get(key)
        if memory is None or memory.session != session or target > memory.target:
            memory = _Memory(session, target)
            self._memory[key] = memory
        memory.target = target
        if known and soc_pct >= target:
            memory.reached = True
        elif known and soc_pct < target - TARGET_RESTART_PP:
            memory.reached = False
        if known and memory.reached:
            memory.latched = False
            kind: PlannedKind = "reached"
        else:
            if slot_w is None and start is not None and wall_now >= start:
                memory.latched = True
            if slot_w is not None and slot_w > 0:
                kind = "plan"
            elif memory.latched:
                kind = "due"
            else:
                kind = "surplus"
        return PlannedState(
            state=kind,
            target_soc_pct=target,
            departure_at=departure_at,
            need_kwh=need,
            latest_start=start,
            plan_w=slot_w,
            soc_pct=soc.pct,
            soc_source=soc.source,
        )
