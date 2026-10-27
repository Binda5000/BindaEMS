"""Ladepunkte der Laderegelung und was die Verteilung von ihnen braucht.

- ``ChargePoint``: der Ladepunkt eines Autos an seiner Wallbox in einem Zyklus
- ``charge_points``: die Ladepunkte je Auto mit Wallbox, in der Reihenfolge der Priorität
- ``now_capped``: der Ladepunkt mit dem Höchststrom in „Sofort“ (Plan Sofort-Limit)
- ``reserved_a``: die Zuschläge bei Ausfällen je Phase
- ``can_charge``, ``cars_w``, ``wanted_a``, ``holding_a``, ``enough``: was ein Auto am Überschuss
  und an der Zuteilung des Wächters wünscht, hält und trägt, in „Geplant“ mit festem Bedarf

Ein Auto ist ein Fahrzeug der Konfiguration oder der Gast einer Wallbox (``fleet``, Plan 2c-2b,
Präzisierung 6). Präzisierungen ohne Angabe des Plans sind die von Plan 2c-1. Die Schicht ist rein:
Sie liest nur ihre Argumente.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from bindaems.core.control.assignment import AssignmentKind, VehicleState
from bindaems.core.control.charge_inputs import NO_VEHICLE_DATA, ChargeInputs, WallboxObs
from bindaems.core.control.charge_modes import FULL_POWER, Effective
from bindaems.core.control.charging_tesla import tessie_current
from bindaems.core.control.fleet import car_spec
from bindaems.core.control.phase_guard import PHASES
from bindaems.core.safety.limits import HardLimits
from bindaems.shared.config import Config, EvcsConfig, Phase
from bindaems.shared.control import vehicle_actuator, wallbox_actuator


@dataclass(frozen=True)
class ChargePoint:
    """Der Ladepunkt eines Autos an seiner Wallbox in einem Zyklus (Präzisierungen 5 und 6).

    - ``vehicle``: das Auto, ein Schlüssel aus ``fleet``
    - ``actuator``: ``wallbox.<w>`` an einer EVCS, ``vehicle.<v>`` für ein Fahrzeug mit Tessie am
      Wall Connector; ``None`` für eins ohne Tessie und für einen Gast dort, die nur Last sind
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


def charge_points(
    cfg: Config,
    limits: HardLimits,
    order: Sequence[str],
    vehicles: Mapping[str, VehicleState],
    inputs: ChargeInputs,
) -> dict[str, ChargePoint]:
    """Die Ladepunkte je Auto mit Wallbox; eine Wallbox hat das erste nach Priorität."""
    points: dict[str, ChargePoint] = {}
    taken: set[str] = set()
    for key in order:
        wallbox = vehicles[key].wallbox
        if wallbox is None or wallbox in taken:
            continue
        taken.add(wallbox)
        points[key] = _point(cfg, limits, key, wallbox, vehicles[key].kind, inputs)
    return points


def now_capped(point: ChargePoint, max_a: int | None) -> ChargePoint:
    """Der Ladepunkt mit dem Höchststrom ``max_a`` der Absicht für „Sofort“ (Plan Sofort-Limit).

    Ohne Limit (``None``) derselbe Ladepunkt; sonst ist sein ``max_a`` höchstens das Limit, aber
    nie unter ``min_a`` (der Mindeststrom des Ladepunkts geht vor) und nie über dem bisherigen
    ``max_a``: ``min(max_a, max(Limit, min_a))``. So klemmt auch ein Limit über einem später
    kleineren ``max_a`` in ``config.yaml`` auf den Strombereich des Ladepunkts.
    """
    if max_a is None:
        return point
    return replace(point, max_a=min(point.max_a, max(max_a, point.min_a)))


def _point(
    cfg: Config,
    limits: HardLimits,
    key: str,
    wallbox_key: str,
    kind: AssignmentKind,
    inputs: ChargeInputs,
) -> ChargePoint:
    """Der Ladepunkt des Autos ``key`` an der Wallbox ``wallbox_key`` (Präzisierung 6).

    Phasen und Strombereich des Autos kommen aus ``car_spec``: Ein Gast lädt auf allen drei Phasen
    der ``phase_map``, an einer EVCS in deren Strombereich; am Wall Connector hat er keinen Aktor
    und ist Last, sein ``max_a`` ist das des Wall Connectors (Plan 2c-2b, Präzisierung 6).

    Geregelt ist er, wenn sein Aktor steuerbar ist (am Wall Connector auch nur weckbar), er
    nicht pausiert (ein Fahrzeug mit Tessie, solange die Zuordnung unklar ist) und seine
    Wallbox erreichbar ist oder Tessie seinen Strom kennt.
    """
    car = car_spec(cfg, key)
    wallbox = cfg.wallboxes[wallbox_key]
    seen = inputs.wallboxes[wallbox_key]
    tessie = NO_VEHICLE_DATA
    if car.tessie:
        tessie = inputs.vehicles.get(key, NO_VEHICLE_DATA)
    measured = seen.phase_a if seen.reachable else tessie_current(tessie, wallbox.phase_map)
    if isinstance(wallbox, EvcsConfig):
        evcs_actuator = wallbox_actuator(wallbox_key)
        actuator: str | None = evcs_actuator
        unclear = kind is AssignmentKind.UNCLEAR  # Präzisierung 5: die ganze EVCS
        phases = wallbox.phase_map if unclear else wallbox.phase_map[: car.phases]
        min_a = wallbox.min_a if unclear else max(wallbox.min_a, car.min_a)
        max_a = wallbox.max_a if unclear else min(wallbox.max_a, car.max_a)
        steerable = inputs.controllable.get(evcs_actuator) is True
        paused = False
    else:
        candidate = vehicle_actuator(key)
        actuator = candidate if candidate in limits.actuators else None
        phases = wallbox.phase_map[: car.phases]
        min_a, max_a = car.min_a, min(car.max_a, wallbox.max_a)
        steerable = actuator is not None and (
            inputs.controllable.get(actuator) is True or inputs.wakeable.get(actuator) is True
        )
        paused = kind is AssignmentKind.UNCLEAR
    return ChargePoint(
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


def reserved_a(
    cfg: Config, points: Sequence[ChargePoint], wallboxes: Mapping[str, WallboxObs]
) -> dict[Phase, float]:
    """Die Zuschläge bei Ausfällen je Phase, nur ohne geregelten Ladepunkt (Präzisierung 6).

    - Eine EVCS, die nicht erreichbar ist: ``safe_a`` auf allen Phasen ihrer ``phase_map``,
      auch ohne Fahrzeug an ihr.
    - Ein Auto am Wall Connector, der nicht erreichbar ist, ohne Strom aus Tessie, auch ein Gast:
      das ``max_a`` seines Ladepunkts auf allen drei Phasen. Geregelt ist dieser Ladepunkt nie.
    """
    regulated = {point.wallbox for point in points if point.regulated}
    reserved = dict.fromkeys(PHASES, 0.0)
    for key, wallbox in cfg.wallboxes.items():
        out = not wallboxes[key].reachable
        if out and isinstance(wallbox, EvcsConfig) and key not in regulated:
            for phase in wallbox.phase_map:
                reserved[phase] += wallbox.safe_a
    for point in points:
        if not point.evcs and point.measured is None and not wallboxes[point.wallbox].reachable:
            for phase in PHASES:
                reserved[phase] += point.max_a
    return reserved


def can_charge(vehicle: VehicleState, mode: Effective, *, reached: bool = False) -> bool:
    """Ob das Auto am Überschuss teilnimmt: nicht abgesteckt, nicht „geladen“, nicht Aus.

    ``reached``: In „Geplant“ hat das Auto den Ziel-SOC erreicht; es lädt dann auch keinen
    Überschuss (Plan 4b, Entscheidung 4).
    """
    return (
        vehicle.plugged is not False
        and not vehicle.complete
        and not reached
        and mode is not Effective.OFF
    )


def cars_w(points: Sequence[ChargePoint], wallboxes: Mapping[str, WallboxObs]) -> float | None:
    """Die Ladeleistung an den Wallboxen dieser Ladepunkte; ``None``, wenn eine fehlt."""
    total = 0.0
    for point in points:
        power = wallboxes[point.wallbox].power_w
        if power is None:
            return None
        total += power
    return total


def wanted_a(
    point: ChargePoint,
    mode: Effective,
    share: int | None,
    *,
    reserved_w: float = 0.0,
    voltage_v: float | None = None,
) -> int:
    """Der Wunsch eines Autos, das laden kann: Sofort und Boost ``max_a``, in Sofort mit Limit
    gekappt (``now_capped``), sonst der Anteil.

    PV wünscht den Anteil, ohne Wert 0; Min+PV den Anteil, mindestens ``min_a``. „Geplant“ mit
    festem Bedarf ``reserved_w`` (in W) wünscht ``max(ceil(reserved_w / (U · n)), Anteil)`` im
    Bereich ``[min_a, max_a]``, mit ``U = voltage_v`` und ``n`` den Phasen des Ladepunkts; ohne
    festen Bedarf wie PV (Plan 4b, Präzisierung 7).
    """
    if mode in FULL_POWER:
        return point.max_a
    if mode is Effective.MIN_PV:
        return max(share or 0, point.min_a)
    if mode is Effective.PLANNED and reserved_w > 0:
        if voltage_v is None:
            raise ValueError("ein fester Bedarf braucht die Spannung")
        # auf 1e-9 A gerundet: Die volle Leistung ergibt so genau ``max_a``
        firm_a = math.ceil(round(reserved_w / (voltage_v * len(point.phases)), 9))
        return min(max(firm_a, share or 0, point.min_a), point.max_a)
    return share or 0


def holding_a(point: ChargePoint) -> float:
    """Der größte gemessene Strom des Ladepunkts; er bleibt für die folgenden Autos belegt."""
    return max(point.measured.values(), default=0.0) if point.measured is not None else 0.0


def enough(point: ChargePoint | None, share: int | None, free: float | None) -> bool | None:
    """Ob Anteil und freier Strom den Mindeststrom tragen; ``None``, wenn eins davon fehlt."""
    if point is None or share is None or free is None:
        return None
    return share >= point.min_a and free >= point.min_a
