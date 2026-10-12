"""Status des Totmann-Watchdogs am Cerbo (Spec 8.6) und seine Alarme.

Der Victron-Adapter schreibt die Werte des D-Bus-Dienstes ``com.victronenergy.bindaems`` als
``watchdog.*``-Signale in den Zustandsspeicher. ``read_watchdog`` macht daraus den Status,
``armed_for_core`` sagt, ob der Watchdog für diesen core scharf ist, ``timeout_matches``, ob er das
konfigurierte Timeout meldet, und ``watchdog_alarms`` liefert die Alarme. Alles ist rein: Die Zeit
kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from bindaems.shared.domain import Alarm, Severity, Snapshot, Value
from bindaems.shared.numbers import de, finite_number

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
    ``last_trip_failures`` die Zahl der Aktionen, die dabei fehlgeschlagen sind. ``heartbeat`` ist
    das Echo: der Zähler, den der Watchdog zuletzt angenommen hat (``/Heartbeat``). ``timeout_s``
    ist das Timeout, mit dem er arbeitet (``/TimeoutSeconds``).
    """

    state: WatchdogState
    trip_count: int | None
    last_trip: datetime | None
    last_trip_failures: int | None
    version: str | None
    heartbeat: int | None = None
    timeout_s: float | None = None

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
        heartbeat=_integer(snap.ok("watchdog.heartbeat")),
        timeout_s=finite_number(snap.ok("watchdog.timeout_s")),
    )


def armed_for_core(status: WatchdogStatus, recent: Collection[int], timeout_s: float) -> bool:
    """Ob der Watchdog für diesen core scharf ist, nicht nur irgendwie (Spec 8.6).

    Das gilt, wenn er scharf ist, als Echo einen der zuletzt gesendeten Zähler (``recent``) zeigt
    und das erwartete Timeout meldet (``timeout_matches``). Ein unbekanntes Echo oder Timeout
    zählt nicht: Hat ein früherer Lauf oder ein Werkzeug den Watchdog scharf gemacht, schützt er
    diesen core nicht, denn sein Heartbeat kommt nicht von hier.
    """
    return (
        status.armed
        and status.heartbeat is not None
        and status.heartbeat in recent
        and timeout_matches(status, timeout_s)
    )


def timeout_matches(status: WatchdogStatus, timeout_s: float) -> bool:
    """Meldet der Watchdog das konfigurierte Timeout (``victron.watchdog.timeout_s``)?

    Verglichen wird als Zahl und so, wie der Watchdog es melden kann (``_reportable_timeout``).
    Ein unbekanntes Timeout passt nie, ebenso keins zu einer Einstellung, die keine endliche Zahl
    ist. Zustand und Echo spielen keine Rolle.
    """
    return status.timeout_s is not None and status.timeout_s == _reportable_timeout(timeout_s)


def watchdog_alarms(
    status: WatchdogStatus,
    required: bool,
    now: datetime,
    *,
    expected_timeout_s: float | None = None,
) -> list[Alarm]:
    """Alarme zum Status; ``required`` heißt: Der Betrieb braucht den Watchdog.

    Ausgelöst, falsches Timeout und falsche Version melden sich unabhängig von ``required``,
    „antwortet nicht“ und „deaktiviert“ nur, wenn der Watchdog gebraucht wird. Das Timeout wird mit
    ``expected_timeout_s`` (``victron.watchdog.timeout_s``) verglichen, sobald beide bekannt sind,
    und zwar so, wie der Watchdog es melden kann (``_reportable_timeout``); ohne erwartetes
    Timeout entfällt der Vergleich. Der Text nennt das konfigurierte Timeout, denn das ändert der
    Betreiber, wenn er die ``config.ini`` neu erzeugt.
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
    if (
        expected_timeout_s is not None
        and status.timeout_s is not None
        and (reportable := _reportable_timeout(expected_timeout_s)) is not None
        and status.timeout_s != reportable
    ):
        text = (
            f"Watchdog-Timeout {de(status.timeout_s)} s am Cerbo, "
            f"erwartet {de(expected_timeout_s)} s; "
            "config.ini neu erzeugen und mit install.sh übertragen."
        )
        alarms.append(Alarm("watchdog.timeout", Severity.ERROR, text, now))
    if status.version is not None and status.version != WATCHDOG_VERSION:
        text = (
            f"Watchdog-Version {status.version} am Cerbo, erwartet {WATCHDOG_VERSION}; "
            "bitte mit install.sh aktualisieren."
        )
        alarms.append(Alarm("watchdog.version", Severity.WARNING, text, now))
    return alarms


def _reportable_timeout(timeout_s: float) -> int | None:
    """Das konfigurierte Timeout so, wie der Watchdog am Cerbo es melden kann.

    Der Dienst veröffentlicht ``/TimeoutSeconds`` als ``int`` der Einstellung, schneidet also ab:
    90,5 erscheint als 90. ``None``, wenn die Einstellung keine endliche Zahl ist (die
    Konfiguration lässt ``inf`` zu); das darf den Regelzyklus nicht abbrechen.
    """
    number = finite_number(timeout_s)
    return int(number) if number is not None else None


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
