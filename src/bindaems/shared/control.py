"""Steuerungstypen, die core und app gemeinsam brauchen (Spec 11.1).

Aktor-IDs, Befehlsarten, ``Command``, Betriebsarten, Betriebszustände und die Absicht
(``Intent``) samt den Lademodi je Fahrzeug. Die Safety-Schicht im core prüft jeden ``Command``
gegen die harten Grenzen (Spec 8.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AwareDatetime, Field

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
    Gründe der Regelung als Codes für das Entscheidungslog. ``wake`` ist ein Start mit
    Weckerlaubnis (Plan 2c-1, Design 7.1) und wirkt nur bei ``charging`` mit ``True``.
    """

    actuator: str
    kind: CommandKind
    value: CommandValue
    urgent: bool = False
    reasons: tuple[str, ...] = ()
    wake: bool = False


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


class ChargeMode(StrEnum):
    """Lademodus eines Fahrzeugs: PV-Überschuss, Min+PV, Sofort oder Aus (Plan 2c-1, Design 3.1)."""

    PV = "pv"
    MIN_PV = "min_pv"
    NOW = "now"
    OFF = "off"


class SurplusOrder(StrEnum):
    """Wer den PV-Überschuss zuerst bekommt: die Autos, der Akku oder der Akku bis zu einem SOC."""

    CARS_FIRST = "cars_first"
    BATTERY_FIRST = "battery_first"
    BATTERY_UNTIL_SOC = "battery_until_soc"


class SurplusPriority(_Model):
    """Vorrang beim Überschuss (Spec 7.5); ``soc_pct`` gilt für ``battery_until_soc``."""

    kind: SurplusOrder = SurplusOrder.BATTERY_UNTIL_SOC
    soc_pct: Annotated[float, Field(ge=0, le=100)] = 30.0


class VehicleIntent(_Model):
    """Absicht für ein Fahrzeug (Plan 2c-1, Design 3.1).

    - ``mode``: der Lademodus; ohne Eintrag in der Absicht lädt ein Fahrzeug im PV-Überschuss
    - ``boost_until``: Bis dahin lädt das Fahrzeug wie in „Sofort“, längstens bis zum Abstecken.
      Der core klemmt auf höchstens 12 h nach dem Empfang; ein Zeitpunkt in der Vergangenheit
      heißt kein Boost.
    - ``wallbox``: Schlüssel der Wallbox, an der das Fahrzeug hängt; übersteuert die Zuordnung
      und gilt wie der Boost bis zum Abstecken
    """

    mode: ChargeMode = ChargeMode.PV
    boost_until: AwareDatetime | None = None
    wallbox: str | None = None


class Intent(_Model):
    """Absicht der Bedienung (Spec 5.3): Regelbetrieb, Betriebsart je Aktor und Lademodi.

    ``version`` steigt streng. Ein Aktor, der in ``actuators`` fehlt, läuft im Dry-Run. Für ein
    Fahrzeug ohne Eintrag in ``vehicles`` gilt ``VehicleIntent()``. ``priority`` nennt die
    Fahrzeug-Schlüssel in der Reihenfolge, in der sie Überschuss und Strom zugeteilt bekommen;
    ohne Angabe gilt die Reihenfolge unter ``vehicles`` in ``config.yaml``.
    """

    version: Annotated[int, Field(ge=1)]
    control: bool = False
    actuators: dict[str, ActuatorMode] = Field(default_factory=dict)
    vehicles: dict[str, VehicleIntent] = Field(default_factory=dict)
    priority: list[str] | None = None
    surplus_priority: SurplusPriority = Field(default_factory=SurplusPriority)
