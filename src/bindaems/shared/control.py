"""Steuerungstypen, die core und app gemeinsam brauchen (Spec 11.1).

Aktor-IDs, Befehlsarten, ``Command``, Betriebsarten, Betriebszustände und die Absicht
(``Intent``). Die Safety-Schicht im core prüft jeden ``Command`` gegen die harten Grenzen
(Spec 8.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field

from bindaems.shared.config import _Model

VICTRON: Final = "victron"


def wallbox_actuator(name: str) -> str:
    """Aktor-ID einer EVCS: ``wallbox.<name>`` mit dem Schlüssel aus ``wallboxes``."""
    return f"wallbox.{name}"


def vehicle_actuator(name: str) -> str:
    """Aktor-ID eines Fahrzeugs: ``vehicle.<name>`` mit dem Schlüssel aus ``vehicles``."""
    return f"vehicle.{name}"


class CommandKind(StrEnum):
    """Art eines Befehls; sie legt Einheit und Bedeutung des Werts fest."""

    CURRENT = "current"  # Ladestrom in A, ganzzahlig (EVCS, Tesla)
    CHARGING = "charging"  # Laden an (True) oder aus (False)
    GRID_SETPOINT = "grid_setpoint"  # Netz-Sollwert in W; None hebt den Override auf
    MAX_DISCHARGE = "max_discharge"  # Entladegrenze in W; −1 = unbegrenzt
    MIN_SOC = "min_soc"  # Victron-Min-SOC in %


# Persistente Arten bleiben im Gerät gespeichert; für sie gilt das Schreibbudget (Regel 6).
PERSISTENT_KINDS: frozenset[CommandKind] = frozenset(
    {CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC}
)

CommandValue = float | bool | None


@dataclass(frozen=True)
class Command:
    """Ein Befehl an einen Aktor, so wie die Regelung ihn anfordert.

    ``urgent`` markiert einen Befehl Richtung sicherer Zustand (Spec 8.3). ``reasons`` nennt die
    Gründe der Regelung als Codes für das Entscheidungslog.
    """

    actuator: str
    kind: CommandKind
    value: CommandValue
    urgent: bool = False
    reasons: tuple[str, ...] = ()


class ActuatorMode(StrEnum):
    """Betriebsart eines Aktors: Dry-Run prüft und protokolliert, schreibt aber nicht."""

    DRY_RUN = "dry_run"
    LIVE = "live"


class OperatingState(StrEnum):
    """Betriebszustand des core (Spec 7.2); die Werte sind die Namen."""

    START = "START"
    OBSERVE = "OBSERVE"
    CONTROL = "CONTROL"
    DEGRADED = "DEGRADED"
    SAFE = "SAFE"


class Intent(_Model):
    """Absicht der Bedienung (Spec 5.3): Regelbetrieb und Betriebsart je Aktor.

    ``version`` steigt streng. Ein Aktor, der in ``actuators`` fehlt, läuft im Dry-Run.
    """

    version: Annotated[int, Field(ge=1)]
    control: bool = False
    actuators: dict[str, ActuatorMode] = Field(default_factory=dict)
