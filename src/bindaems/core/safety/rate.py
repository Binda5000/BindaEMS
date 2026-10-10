"""Regel 5 der Safety-Schicht: Mindestabstände, Stundenlimits und Totbänder (Spec 8.3).

``RateLimiter`` merkt sich je Aktor und Art, wann zuletzt ein Befehl erfasst wurde, und meldet,
wenn ein neuer zu früh oder zu oft käme. ``deadband`` erkennt einen Befehl, der den bekannten
Ist-Wert nicht oder nur unter dem Totband ändert. Beides ist rein: Die Zeit kommt als Argument.

Das Fenster der Stundenlimits ist halboffen: Ein Befehl zum Zeitpunkt ``t`` zählt, solange
``now − t`` kleiner ist als 3600 s.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime
from typing import Final

from bindaems.core.safety.limits import Family, HardLimits
from bindaems.core.safety.types import Finding, SafetyContext, de, finite_number
from bindaems.shared.config import VehicleCommandLimits
from bindaems.shared.control import Command, CommandKind, CommandValue

EVCS_INTERVAL_S: Final = 5.0  # EVCS: Strom und Laden an/aus
VICTRON_OVERRIDE_INTERVAL_S: Final = 2.0  # Victron: Netz-Sollwert
EVCS_DEADBAND_A: Final = 1.0
VICTRON_DEADBAND_W: Final = 50.0

_WINDOW_S: Final = 3600.0  # Fenster der Stundenlimits
_SAME_BELOW: Final = 1e-9  # Zahlen mit geringerem Abstand sind gleich
_ALL_KINDS: Final = tuple(CommandKind)


class RateLimiter:
    """Mindestabstände und Stundenlimits je Aktor und Art (Spec 8.3 Regel 5).

    Ein Mindestabstand gilt je Aktor und Art seit dem letzten ``record``:

    - EVCS: ``current`` und ``charging`` alle 5 s
    - Victron: ``grid_setpoint`` alle 2 s; die persistenten Arten haben das Schreibbudget
    - Tesla (Spec 6.5): ``current`` alle ``current_interval_s``; ``charging`` ohne Mindestabstand

    Beim Tesla gelten dazu Stundenlimits im gleitenden Fenster: höchstens ``per_hour`` Befehle
    zusammen, davon höchstens ``start_stop_per_hour`` ``charging``-Befehle.
    """

    def __init__(self, limits: HardLimits) -> None:
        self._limits = limits
        self._times: dict[tuple[str, CommandKind], deque[datetime]] = {}

    def check(self, command: Command, now: datetime) -> Finding | None:
        """Prüft der Reihe nach Mindestabstand, Befehle je Stunde und Start/Stopp je Stunde.

        Der erste Verstoß ergibt den Befund (``rate.interval``, ``rate.per_hour`` oder
        ``rate.start_stop``), sonst ``None``. ``check`` ändert nichts; erst ``record`` merkt sich
        einen Befehl. Aktor und Art sind schon geprüft: Was nicht im Katalog steht oder keine Regel
        hat, kommt nie zu früh.
        """
        actuator = command.actuator
        interval = _interval_s(self._limits, command)
        recorded = self._times.get((actuator, command.kind))
        if interval is not None and recorded and _age_s(now, recorded[-1]) < interval:
            return Finding("rate.interval", f"Mindestabstand {de(interval)} s noch nicht erreicht.")

        tesla = _tesla_commands(self._limits, actuator)
        if tesla is None:
            return None
        if self._count(actuator, _ALL_KINDS, now) >= tesla.per_hour:
            return Finding(
                "rate.per_hour", f"Höchstens {tesla.per_hour} Befehle pro Stunde erreicht."
            )
        if (
            command.kind == CommandKind.CHARGING
            and self._count(actuator, (CommandKind.CHARGING,), now) >= tesla.start_stop_per_hour
        ):
            text = f"Höchstens {tesla.start_stop_per_hour} Start/Stopp pro Stunde erreicht."
            return Finding("rate.start_stop", text)
        return None

    def record(self, command: Command, now: datetime) -> None:
        """Merkt sich den Befehl zum Zeitpunkt ``now``.

        Erfasst wird, was der Aufrufer meldet; ob der Befehl gesendet oder im Dry-Run nur geprüft
        wurde, ist gleich. Einträge, die zu ``now`` mindestens 3600 s zurückliegen, fallen weg; der
        neueste Eintrag je Aktor und Art bleibt immer, auch für einen Mindestabstand über 3600 s.
        Läuft die Uhr zurück, kann ein Eintrag am alten Rand des Fensters fehlen, den ein früheres
        ``now`` noch gezählt hätte; er wäre um den Rückschritt später ohnehin ausgeschieden.
        """
        times = self._times.setdefault((command.actuator, command.kind), deque())
        times.append(now)
        while _age_s(now, times[0]) >= _WINDOW_S:  # der neue Eintrag hält die Schleife an
            times.popleft()

    def _count(self, actuator: str, kinds: Iterable[CommandKind], now: datetime) -> int:
        """Zahl der erfassten Befehle des Aktors in den Arten ``kinds`` innerhalb des Fensters."""
        return sum(
            1
            for kind in kinds
            for recorded in self._times.get((actuator, kind), ())
            if _age_s(now, recorded) < _WINDOW_S
        )


def deadband(
    command: Command, value: CommandValue, ctx: SafetyContext, limits: HardLimits
) -> Finding | None:
    """Erkennt einen Befehl, der den bekannten Ist-Wert nicht oder nur unter dem Totband ändert.

    ``value`` ist der Wert nach den Regeln 1–4; Aktor und Art kommen aus ``command``. Ohne bekannten
    Ist-Wert (``(actuator, kind)`` fehlt in ``ctx.present``) gibt es kein Totband. Sonst:

    - Gleicher Wert ergibt ``rate.unchanged``, auch innerhalb des Totbands: ``bool`` mit ``bool``,
      ``None`` mit ``None`` (beim Netz-Sollwert: kein Override) und Zahlen mit Abstand unter 1e-9.
      Verschiedene Typen sind nie gleich, auch ``True`` und 1.0 nicht.
    - Sonst ergibt eine Änderung unter dem Totband ``rate.deadband``: beim EVCS-Strom unter 1 A,
      beim Tesla-Strom unter ``current_step_a`` und beim Netz-Sollwert unter 50 W. Die anderen Arten
      haben kein Totband.

    Eine Zahl, die nicht endlich ist, und alles, was keine Zahl ist, gleicht keinem Wert und liegt
    in keinem Totband (``finite_number``).
    """
    key = (command.actuator, command.kind)
    if key not in ctx.present:
        return None
    actual = ctx.present[key]
    if _same(value, actual):
        return Finding("rate.unchanged", "Wert unverändert.")

    band = _band(limits, command)
    new, old = finite_number(value), finite_number(actual)
    if band is None or new is None or old is None:
        return None
    width, unit = band
    if abs(new - old) < width:
        return Finding("rate.deadband", f"Änderung unter dem Totband ({de(width)} {unit}).")
    return None


def _age_s(now: datetime, recorded: datetime) -> float:
    """Sekunden seit ``recorded``; negativ, wenn der Eintrag nach ``now`` liegt."""
    return (now - recorded).total_seconds()


def _family(limits: HardLimits, actuator: str) -> Family | None:
    """Die Familie des Aktors laut Katalog; ``None``, wenn er nicht darin steht."""
    spec = limits.actuators.get(actuator)
    return None if spec is None else spec.family


def _tesla_commands(limits: HardLimits, actuator: str) -> VehicleCommandLimits | None:
    """Die Befehlslimits des Aktors; ``None``, wenn er kein Tesla ist."""
    if _family(limits, actuator) != "tesla":
        return None
    return limits.vehicle_commands[actuator]


def _interval_s(limits: HardLimits, command: Command) -> float | None:
    """Mindestabstand in s für Aktor und Art des Befehls; ``None`` heißt keiner."""
    family, kind = _family(limits, command.actuator), command.kind
    if family == "evcs" and kind in (CommandKind.CURRENT, CommandKind.CHARGING):
        return EVCS_INTERVAL_S
    if family == "victron" and kind == CommandKind.GRID_SETPOINT:
        return VICTRON_OVERRIDE_INTERVAL_S
    if family == "tesla" and kind == CommandKind.CURRENT:
        return limits.vehicle_commands[command.actuator].current_interval_s
    return None


def _band(limits: HardLimits, command: Command) -> tuple[float, str] | None:
    """Totband und Einheit für Aktor und Art des Befehls; ``None`` heißt keins."""
    family, kind = _family(limits, command.actuator), command.kind
    if family == "evcs" and kind == CommandKind.CURRENT:
        return EVCS_DEADBAND_A, "A"
    if family == "tesla" and kind == CommandKind.CURRENT:
        return float(limits.vehicle_commands[command.actuator].current_step_a), "A"
    if family == "victron" and kind == CommandKind.GRID_SETPOINT:
        return VICTRON_DEADBAND_W, "W"
    return None


def _same(value: CommandValue, actual: CommandValue) -> bool:
    """Gleicher Wert: ``bool`` mit ``bool``, ``None`` mit ``None``, Zahlen mit Abstand < 1e-9."""
    if isinstance(value, bool) and isinstance(actual, bool):
        return value == actual
    if value is None and actual is None:
        return True
    # ``bool`` und ``None`` sind keine Zahlen; alles andere Unbrauchbare auch nicht
    new, old = finite_number(value), finite_number(actual)
    return new is not None and old is not None and abs(new - old) < _SAME_BELOW
