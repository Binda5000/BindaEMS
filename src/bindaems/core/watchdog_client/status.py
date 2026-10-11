"""Status des Totmann-Watchdogs am Cerbo (Spec 8.6) und seine Alarme.

Der Victron-Adapter schreibt die Werte des D-Bus-Dienstes ``com.victronenergy.bindaems`` als
``watchdog.*``-Signale in den Zustandsspeicher. ``read_watchdog`` macht daraus den Status,
``watchdog_alarms`` die Alarme. Beides ist rein: Die Zeit kommt als Argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from bindaems.core.safety.types import finite_number
from bindaems.shared.domain import Alarm, Severity, Snapshot, Value

# Muss ``VERSION`` in ``cerbo-watchdog/bindaems_watchdog/__init__.py`` entsprechen.
WATCHDOG_VERSION: Final = "1.0.0"

_TRIPPED_TEXT: Final = "Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt."
_NO_ANSWER_TEXT: Final = "Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run."
_DISABLED_TEXT: Final = "Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run."


class WatchdogState(StrEnum):
    """Zustand des Watchdogs; WAITING bis DISABLED sind die Werte 0–3 von ``/State``."""

    WAITING = "waiting"
    ARMED = "armed"
    TRIPPED = "tripped"
    DISABLED = "disabled"
    UNKNOWN = "unknown"  # nicht erreichbar oder ein unbekannter Wert


_STATES: Final = {
    0: WatchdogState.WAITING,
    1: WatchdogState.ARMED,
    2: WatchdogState.TRIPPED,
    3: WatchdogState.DISABLED,
}


@dataclass(frozen=True)
class WatchdogStatus:
    """Was der core vom Watchdog weiß; ``None`` heißt unbekannt.

    ``last_trip`` ist der Zeitpunkt der letzten Auslösung (``None``: nie oder unbekannt),
    ``last_trip_failures`` die Zahl der Aktionen, die dabei fehlgeschlagen sind.
    """

    state: WatchdogState
    trip_count: int | None
    last_trip: datetime | None
    last_trip_failures: int | None
    version: str | None

    @property
    def armed(self) -> bool:
        return self.state is WatchdogState.ARMED

    @classmethod
    def unknown(cls) -> WatchdogStatus:
        """Der Status, solange der Watchdog nichts gemeldet hat."""
        return cls(WatchdogState.UNKNOWN, None, None, None, None)


def read_watchdog(snap: Snapshot) -> WatchdogStatus:
    """Liest den Status aus den ``watchdog.*``-Signalen; nur Werte mit Qualität ``OK`` zählen."""
    version = snap.ok("watchdog.version")
    return WatchdogStatus(
        state=_state(snap.ok("watchdog.state")),
        trip_count=_integer(snap.ok("watchdog.trip_count")),
        last_trip=_trip_time(snap.ok("watchdog.last_trip_time")),
        last_trip_failures=_integer(snap.ok("watchdog.last_trip_failures")),
        version=version if isinstance(version, str) else None,
    )


def watchdog_alarms(status: WatchdogStatus, required: bool, now: datetime) -> list[Alarm]:
    """Alarme zum Status; ``required`` heißt: Der Betrieb braucht den Watchdog.

    Ausgelöst und falsche Version melden sich unabhängig von ``required``, „antwortet nicht“ und
    „deaktiviert“ nur, wenn der Watchdog gebraucht wird.
    """
    alarms: list[Alarm] = []
    if status.state is WatchdogState.TRIPPED:
        text = _TRIPPED_TEXT
        if status.last_trip_failures is not None and status.last_trip_failures > 0:
            text += f" Fehlgeschlagene Aktionen: {status.last_trip_failures}."
        since = status.last_trip if status.last_trip is not None else now
        alarms.append(Alarm("watchdog.tripped", Severity.ERROR, text, since))
    if required and status.state is WatchdogState.UNKNOWN:
        alarms.append(Alarm("watchdog.unavailable", Severity.ERROR, _NO_ANSWER_TEXT, now))
    if required and status.state is WatchdogState.DISABLED:
        alarms.append(Alarm("watchdog.unavailable", Severity.ERROR, _DISABLED_TEXT, now))
    if status.version is not None and status.version != WATCHDOG_VERSION:
        text = (
            f"Watchdog-Version {status.version} am Cerbo, erwartet {WATCHDOG_VERSION}; "
            "bitte mit install.sh aktualisieren."
        )
        alarms.append(Alarm("watchdog.version", Severity.WARNING, text, now))
    return alarms


def _state(value: Value) -> WatchdogState:
    code = _integer(value)
    return _STATES.get(code, WatchdogState.UNKNOWN) if code is not None else WatchdogState.UNKNOWN


def _integer(value: Value) -> int | None:
    """Der Wert als ganze Zahl; Brüche, Texte, ``bool`` und NaN oder ±∞ ergeben ``None``."""
    number = finite_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _trip_time(value: Value) -> datetime | None:
    """Unix-Sekunden als UTC-Zeit; 0 heißt „nie“, ein unbrauchbarer Wert gilt als unbekannt.

    Ein Zeitstempel außerhalb des darstellbaren Bereichs darf den Regelzyklus nicht abbrechen:
    Ohne den Zyklus bliebe der Heartbeat aus.
    """
    seconds = finite_number(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None
