"""Harte Grenzen und Aktorkatalog aus ``config.yaml`` (Spec 8.2) und die Richtung „sicher“.

Die Grenzen werden einmal aus der validierten Konfiguration gelesen. UI, HA, Planer und
Lernmodelle können sie nur lesen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from bindaems.core.safety.types import SafetyContext, finite_number
from bindaems.shared.config import Config, EvcsConfig, PhaseMap, VehicleCommandLimits
from bindaems.shared.control import (
    VICTRON,
    Command,
    CommandKind,
    CommandValue,
    vehicle_actuator,
    wallbox_actuator,
)

Family = Literal["evcs", "victron", "tesla"]

_VICTRON_KINDS = frozenset(
    {CommandKind.GRID_SETPOINT, CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC}
)
_CHARGING_KINDS = frozenset({CommandKind.CURRENT, CommandKind.CHARGING})


@dataclass(frozen=True)
class ActuatorSpec:
    """Ein Aktor des Katalogs: Familie, Anzeigename, erlaubte Befehlsarten und Freigaben.

    ``live_allowed`` ist die Freigabe in ``config.yaml`` (Spec 8.7). ``needs_watchdog`` heißt,
    dass der Aktor nur bei scharfem Watchdog am Cerbo live gehen darf (Victron und EVCS).
    """

    id: str
    family: Family
    label: str
    kinds: frozenset[CommandKind]
    live_allowed: bool
    needs_watchdog: bool


@dataclass(frozen=True)
class CurrentRange:
    """Zulässiger Ladestrom in A samt sicherem Strom; beim Tesla ist ``safe_a`` gleich ``min_a``."""

    min_a: int
    max_a: int
    safe_a: int


@dataclass(frozen=True)
class HardLimits:
    """Alle harten Grenzen der Safety-Schicht und der Aktorkatalog.

    - ``phase_limit_a``: ``fuse_a`` abzüglich ``fuse_margin_a``
    - ``voltage_v``: Nennspannung
    - ``setpoint_limits``: statische Obergrenzen des Netz-Sollwerts mit ihren Namen, in der
      Reihenfolge Netzladesetpoint, Absicherung, maximale Bezugsleistung (nur falls gesetzt)
    - ``reserve_soc_pct``: USV-Reserve
    - ``max_discharge_w``: maximale Entladeleistung des Akkus
    - ``currents``: Stromgrenzen je Aktor-ID (EVCS und Tesla)
    - ``wallbox_phases``: Phasenzuordnung je Wallbox-Schlüssel (EVCS und Wall Connector)
    - ``vehicle_commands``: Befehlslimits je Fahrzeug-Aktor
    - ``persistent_per_hour``, ``persistent_per_day``: Schreibbudget für persistente Settings
    - ``actuators``: der Katalog in der Reihenfolge Victron, EVCS, Fahrzeuge (je in der
      Reihenfolge der Konfiguration)
    """

    phase_limit_a: float
    voltage_v: float
    setpoint_limits: tuple[tuple[float, str], ...]
    reserve_soc_pct: float
    max_discharge_w: float
    currents: Mapping[str, CurrentRange]
    wallbox_phases: Mapping[str, PhaseMap]
    vehicle_commands: Mapping[str, VehicleCommandLimits]
    persistent_per_hour: int
    persistent_per_day: int
    actuators: Mapping[str, ActuatorSpec]

    @classmethod
    def from_config(cls, cfg: Config) -> HardLimits:
        """Liest Grenzen und Aktorkatalog aus der Konfiguration.

        Aktoren sind Victron, jede EVCS und jedes Fahrzeug mit ``tessie``. Der Wall Connector und
        Fahrzeuge ohne ``tessie`` sind keine Aktoren.
        """
        phase_limit_a = cfg.grid.fuse_a - cfg.grid.fuse_margin_a
        voltage_v = cfg.grid.voltage_nominal_v
        setpoint_limits = [
            (cfg.victron.max_grid_charge_setpoint_w, "maximaler Netzladesetpoint"),
            (3 * voltage_v * phase_limit_a, "Absicherung"),
        ]
        if cfg.grid.max_import_kw is not None:
            setpoint_limits.append((cfg.grid.max_import_kw * 1000, "maximale Bezugsleistung"))

        actuators: dict[str, ActuatorSpec] = {
            VICTRON: ActuatorSpec(
                VICTRON, "victron", "Victron (ESS)", _VICTRON_KINDS, cfg.victron.live_allowed, True
            )
        }
        currents: dict[str, CurrentRange] = {}
        wallbox_phases: dict[str, PhaseMap] = {}
        vehicle_commands: dict[str, VehicleCommandLimits] = {}
        for key, wallbox in cfg.wallboxes.items():
            wallbox_phases[key] = wallbox.phase_map
            if isinstance(wallbox, EvcsConfig):
                actuator = wallbox_actuator(key)
                actuators[actuator] = ActuatorSpec(
                    actuator, "evcs", f"EVCS „{key}“", _CHARGING_KINDS, wallbox.live_allowed, True
                )
                currents[actuator] = CurrentRange(wallbox.min_a, wallbox.max_a, wallbox.safe_a)
        for key, vehicle in cfg.vehicles.items():
            if vehicle.tessie is None:
                continue
            actuator = vehicle_actuator(key)
            actuators[actuator] = ActuatorSpec(
                actuator, "tesla", vehicle.name, _CHARGING_KINDS, vehicle.live_allowed, False
            )
            currents[actuator] = CurrentRange(vehicle.min_a, vehicle.max_a, vehicle.min_a)
            vehicle_commands[actuator] = vehicle.commands

        return cls(
            phase_limit_a=phase_limit_a,
            voltage_v=voltage_v,
            setpoint_limits=tuple(setpoint_limits),
            reserve_soc_pct=cfg.battery.reserve_soc_pct,
            max_discharge_w=cfg.battery.max_discharge_w,
            currents=currents,
            wallbox_phases=wallbox_phases,
            vehicle_commands=vehicle_commands,
            persistent_per_hour=cfg.victron.persistent_writes.per_hour,
            persistent_per_day=cfg.victron.persistent_writes.per_day,
            actuators=actuators,
        )

    def grid_setpoint_max(self, budget_w: float | None) -> tuple[float, str]:
        """Höchster zulässiger Netz-Sollwert in W samt dem Namen der bindenden Grenze.

        Das Minimum aus den statischen Grenzen und, falls gesetzt, dem Leistungsbudget. Bei
        Gleichstand gewinnt die zuerst genannte Grenze.
        """
        candidates = list(self.setpoint_limits)
        if budget_w is not None:
            candidates.append((budget_w, "Leistungsbudget"))
        return min(candidates, key=lambda candidate: candidate[0])

    def safe_commands(self, actuator: str) -> tuple[Command, ...]:
        """Die Befehle, die den Aktor in den sicheren Zustand bringen (Spec 8.5).

        Alle sind dringend und tragen den Grund ``safe_state``. Für etwas, das kein Aktor ist,
        gibt es keine.
        """

        def safe(kind: CommandKind, value: CommandValue) -> Command:
            return Command(actuator, kind, value, urgent=True, reasons=("safe_state",))

        if actuator == VICTRON:
            return (
                safe(CommandKind.GRID_SETPOINT, None),
                safe(CommandKind.MAX_DISCHARGE, -1.0),
                safe(CommandKind.MIN_SOC, self.reserve_soc_pct),
            )
        current = self.currents.get(actuator)
        if current is None:
            return ()
        return (safe(CommandKind.CURRENT, float(current.safe_a)),)


def _number(value: object) -> float | None:
    """Der Wert als Zahl; ``bool``, ``None`` und alles andere gelten nicht als Zahl.

    NaN und ±∞ bleiben Zahlen; die Regeln 1–3 lehnen sie später ab (``finite_number``).
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:  # ein int, der größer ist als jedes float
        return None


def toward_safe(command: Command, ctx: SafetyContext, limits: HardLimits) -> bool:
    """Geht der Befehl nachweislich Richtung sicherer Zustand?

    „Ist-Wert bekannt“ heißt, ``(actuator, kind)`` steht in ``ctx.present``. Werte vom falschen
    Typ sind nie „Richtung sicher“, auch nicht der Ist-Wert. Richtung sicher ist:

    - ``current``: Wert ≤ bekannter Ist-Wert, oder der Ist-Wert ist unbekannt und Wert ≤ ``safe_a``
    - ``charging``: Wert ist ``False``
    - ``grid_setpoint``: Wert ist ``None``, oder der Ist-Wert ist bekannt und Wert ≤ wirksamer
      Ist-Sollwert (der Override, sonst ``base_grid_setpoint_w``); ohne Override und mit
      unbekanntem Basis-Sollwert (``None``, NaN, ±∞) gilt nur ``None``
    - ``max_discharge``: Wert ist −1, oder der bekannte Ist-Wert ist ≥ 0 und Wert ≥ Ist-Wert
    - ``min_soc``: Wert ist die Reserve, oder der Ist-Wert ist bekannt und Reserve ≤ Wert ≤ Ist-Wert

    Befehlsarten werden mit ``==`` verglichen: Eine Art als gleicher Text gilt als diese Art.
    """
    kind = command.kind
    if kind == CommandKind.CHARGING:
        return command.value is False
    if kind == CommandKind.GRID_SETPOINT and command.value is None:
        return True
    value = _number(command.value)
    if value is None:
        return False

    key = (command.actuator, kind)
    known = key in ctx.present
    actual = _number(ctx.present.get(key))
    if kind == CommandKind.CURRENT:
        current = limits.currents.get(command.actuator)
        if current is None:
            return False
        if known:
            return actual is not None and value <= actual
        return value <= current.safe_a
    if kind == CommandKind.GRID_SETPOINT:
        if not known:
            return False
        base = finite_number(ctx.base_grid_setpoint_w)
        effective = base if ctx.present[key] is None else actual
        return effective is not None and value <= effective
    if kind == CommandKind.MAX_DISCHARGE:
        return value == -1 or (actual is not None and actual >= 0 and value >= actual)
    if kind == CommandKind.MIN_SOC:
        reserve = limits.reserve_soc_pct
        return value == reserve or (actual is not None and reserve <= value <= actual)
    return False
