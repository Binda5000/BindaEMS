"""Typen der Safety-Schicht: Ergebnis einer Prüfung und der Kontext, in dem geprüft wird.

Die Schicht ist rein: Zeit, Messwerte, Freigaben und Sperren bekommt sie über den
``SafetyContext`` von außen (Spec 8.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from bindaems.shared.config import Phase
from bindaems.shared.control import (
    ActuatorMode,
    Command,
    CommandKind,
    CommandValue,
    OperatingState,
)


def de(value: float) -> str:
    """Zahl für Begründungstexte: zehn signifikante Stellen, Dezimalkomma, echtes Minus (U+2212)."""
    return f"{value:.10g}".replace(".", ",").replace("-", "\N{MINUS SIGN}")


class Outcome(StrEnum):
    """Ergebnis der Prüfung eines Befehls."""

    ACCEPTED = "accepted"  # unverändert angenommen
    CLAMPED = "clamped"  # Regeln 1–3 haben den Wert in seine Grenzen gebracht
    REDUCED = "reduced"  # Regel 4 hat den Wert wegen der Phasenvorhersage verringert
    UNCHANGED = "unchanged"  # Totband oder gleicher Wert: nichts zu senden
    DEFERRED = "deferred"  # Rate-Limit: später noch einmal versuchen
    REJECTED = "rejected"  # abgelehnt


# Bei diesen Ergebnissen steht ein Wert zum Senden fest; im Dry-Run wird er nicht gesendet.
SENDABLE: frozenset[Outcome] = frozenset({Outcome.ACCEPTED, Outcome.CLAMPED, Outcome.REDUCED})


@dataclass(frozen=True)
class Finding:
    """Befund einer Regel: ein stabiler Code und der Begründungstext (deutsch)."""

    code: str
    text: str


@dataclass(frozen=True)
class Step:
    """Ergebnis einer einzelnen Regel; bei ``reject`` ist ``value`` gleich ``None``."""

    value: CommandValue
    findings: tuple[Finding, ...] = ()
    reject: bool = False


@dataclass(frozen=True)
class InputStatus:
    """Ergebnis der Eingangsprüfung (Regel 7): ``ok`` und, wenn nicht, die Probleme."""

    ok: bool
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyContext:
    """Alles, was die Prüfung eines Befehls von außen bekommt.

    - ``now``: Zeitpunkt der Prüfung
    - ``operating``: Betriebszustand (Regel 8)
    - ``modes``: wirksame Betriebsart je Aktor; fehlt der Aktor, gilt Dry-Run
    - ``locks``: Sperrgründe je Aktor; fehlt der Aktor, ist er frei
    - ``inputs``: Ergebnis von Regel 7
    - ``phase_import_a``: Netzbezug je Phase in A, positiv bei Bezug
    - ``wallbox_phase_a``: gemessener Strom je Wallbox-Schlüssel und Netzphase
    - ``vehicle_wallbox``: Fahrzeug-Aktor → Wallbox-Schlüssel; fehlt er oder ist er ``None``,
      ist die Wallbox unbekannt
    - ``present``: bekannter Ist-Wert je Aktor und Art; fehlt der Eintrag, ist der Wert
      unbekannt. Beim Netz-Sollwert heißt ``None`` „kein Override aktiv“.
    - ``battery_discharge_w``: aktuelle Entladeleistung, ≥ 0
    - ``soc_pct``: Akku-SOC
    - ``base_grid_setpoint_w``: persistenter Netz-Sollwert; er gilt ohne Override
    - ``grid_budget_w``: 15-min-Budget (Regel 2); ``None`` heißt keins
    """

    now: datetime
    operating: OperatingState
    modes: Mapping[str, ActuatorMode]
    locks: Mapping[str, tuple[str, ...]]
    inputs: InputStatus
    phase_import_a: Mapping[Phase, float | None]
    wallbox_phase_a: Mapping[str, Mapping[Phase, float]]
    vehicle_wallbox: Mapping[str, str | None]
    present: Mapping[tuple[str, CommandKind], CommandValue]
    battery_discharge_w: float | None = None
    soc_pct: float | None = None
    base_grid_setpoint_w: float = 0.0
    grid_budget_w: float | None = None


@dataclass(frozen=True)
class Verdict:
    """Urteil der Safety-Schicht über einen Befehl.

    ``value`` ist bei ``REJECTED`` gleich ``None``, sonst der Wert nach allen Regeln.
    ``dry_run`` heißt, dass der Aktor nicht live ist: Der Befehl wird geprüft, aber nicht gesendet.
    """

    command: Command
    outcome: Outcome
    value: CommandValue
    findings: tuple[Finding, ...]
    dry_run: bool

    @property
    def sendable(self) -> bool:
        """Es steht ein Wert fest, der gesendet werden dürfte."""
        return self.outcome in SENDABLE

    @property
    def send(self) -> bool:
        """Der Wert wird tatsächlich gesendet: ``sendable`` und kein Dry-Run."""
        return self.sendable and not self.dry_run
