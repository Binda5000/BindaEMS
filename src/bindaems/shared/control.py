"""Steuerungstypen, die core und app gemeinsam brauchen (Spec 11.1).

Aktor-IDs, Befehlsarten, ``Command``, Betriebsarten, Betriebszustände und die Absicht
(``Intent``) samt den Lademodi je Fahrzeug. Die Safety-Schicht im core prüft jeden ``Command``
gegen die harten Grenzen (Spec 8.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AwareDatetime, Field

from bindaems.shared.config import _Model
from bindaems.shared.plan import IntentPlan
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

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
    Weckerlaubnis (Plan 2c-1, Design 7.1) und wirkt nur bei ``charging`` mit ``True``. ``refresh``
    ist das Auffrischen eines Overrides mit gleichem Wert (Plan 2c-2a, Design 8) und wirkt nur bei
    ``grid_setpoint``, bei jeder anderen Art gar nicht. Dort entfällt für die Safety-Schicht das
    Totband, das Auffrischen gilt nie als dringend, und der Ausführer schreibt dafür keine
    Entscheidung, solange es gelingt oder im Dry-Run bleibt.
    """

    actuator: str
    kind: CommandKind
    value: CommandValue
    urgent: bool = False
    reasons: tuple[str, ...] = ()
    wake: bool = False
    refresh: bool = False


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
    """Lademodus eines Fahrzeugs: PV-Überschuss, Min+PV, Sofort, Aus oder Geplant (Plan 2c-1,
    Design 3.1; „Geplant“ ab Plan 4b)."""

    PV = "pv"
    MIN_PV = "min_pv"
    NOW = "now"
    OFF = "off"
    PLANNED = "planned"


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
    - ``target_soc_pct`` und ``departure``: Ziel-SOC (ganze Zahl 20 bis 100) und tägliche
      Abfahrtszeit (lokale Uhrzeit ``HH:MM``) für „Geplant“ (Plan 4b). Beide bleiben auch in den
      anderen Modi stehen; gespeicherte Absichten ohne sie laden mit den Standardwerten.
    """

    mode: ChargeMode = ChargeMode.PV
    boost_until: AwareDatetime | None = None
    wallbox: str | None = None
    target_soc_pct: Annotated[int, Field(ge=20, le=100)] = 80
    departure: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] = "07:00"


def _local_instant(day: date, at: time) -> datetime:
    """Der Zeitpunkt (UTC) der lokalen Uhrzeit ``at`` am Tag ``day``.

    Gibt es die Uhrzeit an dem Tag nicht (Beginn der Sommerzeit), ist es der Übergang selbst,
    also die erste gültige Zeit danach; gibt es sie zweimal (Ende der Sommerzeit), das erste
    Vorkommen.
    """
    naive = datetime.combine(day, at)
    first = naive.replace(tzinfo=LOCAL_TZ, fold=0).astimezone(UTC)
    if first.astimezone(LOCAL_TZ).replace(tzinfo=None) == naive:
        return first
    # Lücke: ``fold=0`` rechnet mit dem alten, ``fold=1`` mit dem neuen Versatz. Der Übergang
    # liegt dazwischen; die Minuten dort zu durchlaufen ist billiger als Übergangstabellen.
    instant = naive.replace(tzinfo=LOCAL_TZ, fold=1).astimezone(UTC)
    new_offset = first.astimezone(LOCAL_TZ).utcoffset()
    while instant.astimezone(LOCAL_TZ).utcoffset() != new_offset:
        instant += timedelta(minutes=1)
    return instant


def next_departure(departure: str, now: datetime) -> datetime:
    """Das erste lokale ``departure`` (``HH:MM``, Wiener Zeit) echt nach ``now``, in UTC.

    Ist die Uhrzeit am lokalen Tag von ``now`` schon erreicht oder vorbei, gilt die des nächsten
    Tages (Plan 4b, Präzisierung 1).
    """
    hour, minute = departure.split(":")
    at = time(int(hour), int(minute))
    moment = ensure_utc(now)
    day = moment.astimezone(LOCAL_TZ).date()
    result = _local_instant(day, at)
    while result <= moment:
        day += timedelta(days=1)
        result = _local_instant(day, at)
    return result


class GuestIntent(_Model):
    """Modus eines Autos ohne Fahrzeug der Konfiguration, eines Gastes (Plan 2c-2a, Design 3.1).

    Es gibt für ihn weder Boost noch Übersteuerung der Wallbox. Ohne Angabe lädt ein Gast im
    PV-Überschuss.
    """

    mode: ChargeMode = ChargeMode.PV


class SupportIntent(_Model):
    """Akku-Unterstützung fürs Auto (Plan 3c, Entscheidung 7).

    ``allowed`` falsch sperrt sie. ``soc_pct`` ist die Unterstützungsschwelle; ohne Angabe gilt
    ``control.support_soc_pct``.
    """

    allowed: bool = True
    soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None


class Intent(_Model):
    """Absicht der Bedienung (Spec 5.3): Regelbetrieb, Betriebsart je Aktor und Lademodi.

    ``version`` steigt streng. Ein Aktor, der in ``actuators`` fehlt, läuft im Dry-Run. Für ein
    Fahrzeug ohne Eintrag in ``vehicles`` gilt ``VehicleIntent()``. ``priority`` nennt die
    Fahrzeug-Schlüssel in der Reihenfolge, in der sie Überschuss und Strom zugeteilt bekommen;
    ohne Angabe gilt die Reihenfolge unter ``vehicles`` in ``config.yaml``. ``guest`` nennt den
    Modus für ein Auto ohne Fahrzeug der Konfiguration; ohne Angabe gilt ``GuestIntent()``.
    ``plan`` ist der bindende Teil des Akkuplans (Plan 3c), ``None`` heißt kein Plan. ``support``
    regelt die Akku-Unterstützung fürs Auto; ohne Angabe ist sie erlaubt.
    """

    version: Annotated[int, Field(ge=1)]
    control: bool = False
    actuators: dict[str, ActuatorMode] = Field(default_factory=dict)
    vehicles: dict[str, VehicleIntent] = Field(default_factory=dict)
    priority: list[str] | None = None
    surplus_priority: SurplusPriority = Field(default_factory=SurplusPriority)
    guest: GuestIntent = Field(default_factory=GuestIntent)
    plan: IntentPlan | None = None
    support: SupportIntent = Field(default_factory=SupportIntent)
