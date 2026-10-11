"""Regel 4 der Safety-Schicht: Phasenvorhersage (Spec 8.3).

``check_phases`` sagt voraus, ob eine Phase nach dem Befehl über ``limits.phase_limit_a`` stiege,
und verringert dann den Wert oder lehnt den Befehl ab. Verringerungen werden nie abgelehnt. Die
Vorhersage ist bewusst vorsichtig:

- Eine Wallbox, die nicht lädt, kann auf allen drei Phasen ihrer ``phase_map`` ziehen; lädt sie,
  zählen die Phasen mit mindestens ``ACTIVE_PHASE_A``.
- Ein Tesla an einer unbekannten Wallbox kann auf allen drei Phasen ziehen.
- Der Netz-Sollwert, die Entladegrenze und der Min-SOC wirken gleichmäßig auf die drei Phasen
  (3-Phasen-ESS mit einem Gerät je Phase).
- Ist der Netzbezug einer betroffenen Phase unbekannt, sind nur Verringerungen erlaubt.

Bezeichnungen: ``I_p`` ist der Netzbezug der Phase ``p`` (``ctx.phase_import_a``), ``h_p`` ihr
Spielraum ``phase_limit_a − I_p`` und ``m_p`` der gemessene Strom der Wallbox auf ihr.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, NamedTuple

from bindaems.core.safety.limits import HardLimits
from bindaems.core.safety.types import (
    Finding,
    SafetyContext,
    Step,
    de,
    finite_number,
    invalid_value,
)
from bindaems.shared.config import Phase
from bindaems.shared.control import Command, CommandKind, CommandValue

ACTIVE_PHASE_A: Final = 0.5  # ab diesem Strom zieht eine Wallbox auf einer Phase

_PHASES: tuple[Phase, ...] = ("L1", "L2", "L3")  # bei Gleichstand gilt diese Reihenfolge
_NO_CURRENT: Mapping[Phase, float] = MappingProxyType({})

_PHASES_UNKNOWN = Step(
    None,
    (Finding("phase.unknown", "Phasenströme unbekannt; nur Verringerungen erlaubt."),),
    reject=True,
)
_BATTERY_UNKNOWN = Step(
    None, (Finding("phase.battery_unknown", "Akkuleistung unbekannt."),), reject=True
)
_SOC_UNKNOWN = Step(None, (Finding("phase.soc_unknown", "Akku-SOC unbekannt."),), reject=True)


class _Load(NamedTuple):
    """Eine betroffene Phase: ihr Netzbezug ``I_p`` und der Strom ``m_p`` der Wallbox auf ihr."""

    phase: Phase
    imported: float
    measured: float


def check_phases(
    command: Command, value: CommandValue, ctx: SafetyContext, limits: HardLimits
) -> Step:
    """Sagt die Phasenströme nach dem Befehl voraus und verringert den Wert oder lehnt ab.

    ``value`` ist der Wert nach den Regeln 1–3. Aktor und Art sind schon geprüft; für ``current``
    und ``charging`` steht der Aktor in ``limits.actuators`` und ``limits.currents``.

    Geprüft wird nur, was den Netzbezug erhöhen kann. Als Erhöhung gilt bei

    - ``current``: der Ist-Strom ist kleiner als der Wert; ist er unbekannt, nur ein Wert über
      dem sicheren Strom ``safe_a`` (Spec 8.5), denn der darf immer gesetzt werden
    - ``charging``: ein Start, während die Wallbox nicht nachweislich lädt
    - ``grid_setpoint``: der Wert liegt über dem wirksamen Ist-Sollwert
    - ``max_discharge``: ein Wert von mindestens 0 unter der aktuellen Entladeleistung
    - ``min_soc``: der Wert liegt über der Reserve und über dem SOC

    Alles andere bleibt unverändert; nur ein Strom wird auch bei einer Verringerung auf den
    erlaubten Strom gesenkt, nie unter den Mindeststrom. Die Texte nennen die bindende Phase, die
    mit dem kleinsten Spielraum unter den betroffenen Phasen; bei Gleichstand gilt die Reihenfolge
    L1, L2, L3. Ein Wert vom falschen Typ und eine unbekannte Art werden abgelehnt
    (``value.invalid``). Bei ``reject`` ist ``value`` gleich ``None``.
    """
    kind = command.kind
    if kind == CommandKind.CHARGING:
        if not isinstance(value, bool):
            return _invalid(kind, value)
        return _check_charging(command.actuator, value, ctx, limits)
    if kind == CommandKind.GRID_SETPOINT and value is None:
        return Step(None)  # hebt den Override auf

    number = finite_number(value)
    if number is None:
        return _invalid(kind, value)
    if kind == CommandKind.CURRENT:
        return _check_current(command.actuator, number, ctx, limits)
    if kind == CommandKind.GRID_SETPOINT:
        return _check_grid_setpoint(command.actuator, number, ctx, limits)
    if kind == CommandKind.MAX_DISCHARGE:
        return _check_max_discharge(command.actuator, number, ctx, limits)
    if kind == CommandKind.MIN_SOC:
        return _check_min_soc(number, ctx, limits)
    return _invalid(kind, value)  # unbekannte Art: im Zweifel ablehnen


def _check_current(actuator: str, value: float, ctx: SafetyContext, limits: HardLimits) -> Step:
    """Ladestrom in A: Erlaubt ist ``floor(min(h_p + m_p))`` über die aktiven Phasen der Wallbox."""
    actual = finite_number(ctx.present.get((actuator, CommandKind.CURRENT)))
    if actual is None:  # unbekannt: nur über dem sicheren Strom, den auch der Watchdog blind setzt
        increase = value > limits.currents[actuator].safe_a
    else:
        increase = actual < value
    phases, measured = _wallbox(actuator, ctx, limits)
    active = tuple(p for p in phases if measured.get(p, 0.0) >= ACTIVE_PHASE_A)
    loads = _loads(ctx, active or phases, measured)
    if loads is None:
        return _PHASES_UNKNOWN if increase else Step(value)

    phase, room = _binding(loads, limits)
    allowed = math.floor(room)
    if value <= allowed:
        return Step(value)
    minimum = limits.currents[actuator].min_a
    if allowed >= minimum:
        return _reduced(phase, float(allowed), "A", limits)
    if increase:
        return _rejected(phase, limits)
    text = f"{_over(phase, limits)}; nur noch Mindeststrom {minimum} A möglich."
    return Step(float(minimum), (Finding("phase.minimum", text),))


def _check_charging(actuator: str, start: bool, ctx: SafetyContext, limits: HardLimits) -> Step:
    """Laden an oder aus: Beim Start muss ``I_p − m_p + I_c ≤ Grenze`` auf allen drei Phasen halten.

    Ladestrom ``I_c`` ist der Ist-Strom, aber mindestens der Mindeststrom des Aktors, denn ein
    startendes Auto zieht nie weniger, auch wenn die Wallbox im Stand 0 A meldet. Ist der Ist-Strom
    unbekannt, gilt der Höchststrom.
    """
    if not start or ctx.present.get((actuator, CommandKind.CHARGING)) is True:
        return Step(start)
    phases, measured = _wallbox(actuator, ctx, limits)
    loads = _loads(ctx, phases, measured)
    if loads is None:
        return _PHASES_UNKNOWN

    current = limits.currents[actuator]
    actual = finite_number(ctx.present.get((actuator, CommandKind.CURRENT)))
    charge_a = current.max_a if actual is None else max(actual, current.min_a)
    if all(load.imported - load.measured + charge_a <= limits.phase_limit_a for load in loads):
        return Step(True)
    return _rejected(_binding(loads, limits)[0], limits)


def _check_grid_setpoint(
    actuator: str, value: float, ctx: SafetyContext, limits: HardLimits
) -> Step:
    """Netz-Sollwert in W: Er darf um höchstens ``3 × U × min(h_p)`` über ``S0`` liegen.

    ``S0`` ist der wirksame Ist-Sollwert.
    """
    actual = _setpoint_actual(actuator, ctx)
    if value <= actual:
        return Step(value)
    loads = _loads(ctx, _PHASES)
    if loads is None:
        return _PHASES_UNKNOWN

    phase, room = _binding(loads, limits)
    ceiling = actual + 3 * limits.voltage_v * room
    if value <= ceiling:
        return Step(value)
    if ceiling >= 0:
        return _reduced(phase, float(math.floor(ceiling)), "W", limits)
    if actual >= 0:
        return _reduced(phase, 0.0, "W", limits)  # nie negativ: der Sollwert fällt auf 0
    return _rejected(phase, limits)


def _setpoint_actual(actuator: str, ctx: SafetyContext) -> float:
    """Wirksamer Ist-Sollwert in W: der Override, ohne Override der Basis-Sollwert.

    Fehlt der Ist-Wert oder ist er unbrauchbar, gilt die vorsichtigste Annahme. Ein Override ist
    nie negativ, also ist ``min(Basis, 0)`` der niedrigste mögliche Sollwert.
    """
    lowest = min(ctx.base_grid_setpoint_w, 0.0)
    key = (actuator, CommandKind.GRID_SETPOINT)
    if key not in ctx.present:
        return lowest
    override = ctx.present[key]
    if override is None:
        return ctx.base_grid_setpoint_w
    number = finite_number(override)
    return lowest if number is None else number


def _check_max_discharge(
    actuator: str, value: float, ctx: SafetyContext, limits: HardLimits
) -> Step:
    """Entladegrenze in W (−1 heißt unbegrenzt).

    Die Differenz ``D − Wert`` zur aktuellen Entladeleistung ``D`` kommt aus dem Netz. Die Grenze
    darf also nicht unter ``D − 3 × U × min(h_p)`` liegen und wird sonst angehoben, über
    ``max_discharge_w`` auf −1. Ist ``D`` unbekannt, wird jede Verschärfung abgelehnt: Der
    bisherige Wert ist unbekannt, −1 oder größer als der Wert.
    """
    if value < 0:
        return Step(value)
    discharge = finite_number(ctx.battery_discharge_w)
    if discharge is None:
        actual = finite_number(ctx.present.get((actuator, CommandKind.MAX_DISCHARGE)))
        looser = actual is not None and 0 <= actual <= value
        return Step(value) if looser else _BATTERY_UNKNOWN
    if value >= discharge:
        return Step(value)
    loads = _loads(ctx, _PHASES)
    if loads is None:
        return _PHASES_UNKNOWN

    phase, room = _binding(loads, limits)
    lowest = discharge - 3 * limits.voltage_v * room
    if value >= lowest:
        return Step(value)
    raised = float(math.ceil(lowest))
    if raised > limits.max_discharge_w:
        raised = -1.0
    text = f"{_over(phase, limits)}; Entladegrenze auf {de(raised)} W angehoben."
    return Step(raised, (Finding("phase.discharge", text),))


def _check_min_soc(value: float, ctx: SafetyContext, limits: HardLimits) -> Step:
    """Min-SOC in %: Der Akku stoppt, und seine Entladeleistung ``D`` kommt aus dem Netz.

    Jede Phase muss ``I_p + D / (3 × U) ≤ Grenze`` halten.
    """
    if value <= limits.reserve_soc_pct:
        return Step(value)
    soc = finite_number(ctx.soc_pct)
    if soc is None:
        return _SOC_UNKNOWN
    if value <= soc:
        return Step(value)
    discharge = finite_number(ctx.battery_discharge_w)
    if discharge is None:
        return _BATTERY_UNKNOWN
    loads = _loads(ctx, _PHASES)
    if loads is None:
        return _PHASES_UNKNOWN

    extra_a = discharge / (3 * limits.voltage_v)
    if all(load.imported + extra_a <= limits.phase_limit_a for load in loads):
        return Step(value)
    return _rejected(_binding(loads, limits)[0], limits)


def _wallbox(
    actuator: str, ctx: SafetyContext, limits: HardLimits
) -> tuple[tuple[Phase, ...], Mapping[Phase, float]]:
    """Die Phasen der Wallbox hinter dem Aktor (in der Reihenfolge L1–L3) und ihr Strom ``m_p``.

    Beim Tesla steht die Wallbox in ``ctx.vehicle_wallbox``. Ist sie unbekannt (nicht
    eingetragen, ``None`` oder nicht konfiguriert), gelten L1–L3 ohne Messwerte. Ein fehlender
    oder nicht endlicher Messwert zählt als 0 A.
    """
    if limits.actuators[actuator].family == "tesla":
        key = ctx.vehicle_wallbox.get(actuator)
    else:
        key = actuator.removeprefix("wallbox.")
    phase_map = limits.wallbox_phases.get(key) if key is not None else None
    if key is None or phase_map is None:
        return _PHASES, _NO_CURRENT
    measured = ctx.wallbox_phase_a.get(key, _NO_CURRENT)
    currents = {p: c for p in _PHASES if (c := finite_number(measured.get(p))) is not None}
    return tuple(p for p in _PHASES if p in phase_map), currents


def _loads(
    ctx: SafetyContext, phases: tuple[Phase, ...], measured: Mapping[Phase, float] = _NO_CURRENT
) -> list[_Load] | None:
    """Netzbezug und Wallbox-Strom der betroffenen Phasen; ``None``, wenn ein Netzbezug fehlt."""
    loads = []
    for phase in phases:
        imported = finite_number(ctx.phase_import_a.get(phase))
        if imported is None:
            return None
        loads.append(_Load(phase, imported, measured.get(phase, 0.0)))
    return loads


def _binding(loads: list[_Load], limits: HardLimits) -> tuple[Phase, float]:
    """Die bindende Phase und ihr Spielraum ``h_p + m_p`` in A: der kleinste unter ``loads``.

    Bei Gleichstand gilt die frühere Phase, denn ``min`` liefert das erste kleinste Element.
    """
    phase, imported, measured = min(
        loads, key=lambda load: limits.phase_limit_a - load.imported + load.measured
    )
    return phase, limits.phase_limit_a - imported + measured


def _over(phase: Phase, limits: HardLimits) -> str:
    return f"Phase {phase} würde über {de(limits.phase_limit_a)} A steigen"


def _reduced(phase: Phase, value: float, unit: str, limits: HardLimits) -> Step:
    text = f"{_over(phase, limits)}; reduziert auf {de(value)} {unit}."
    return Step(value, (Finding("phase.reduced", text),))


def _rejected(phase: Phase, limits: HardLimits) -> Step:
    return Step(None, (Finding("phase.rejected", f"{_over(phase, limits)}."),), reject=True)


def _invalid(kind: CommandKind, value: CommandValue) -> Step:
    return Step(None, (invalid_value(kind, value),), reject=True)
