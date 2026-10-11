"""Die Zustandsdatei des Watchdogs (``state.json``).

Sie hält fest, ob der Watchdog scharf war, damit er nach einem Neustart des Cerbo wieder scharf
startet, und die Zähler der Auslösungen (Spec 8.6).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from typing import Optional, Tuple

UNREADABLE = "state.json unlesbar; der Watchdog startet scharf."


@dataclass(frozen=True)
class StoredState:
    """Was der Watchdog über einen Neustart hinweg behält."""

    armed: bool = False
    trip_count: int = 0
    last_trip_time: int = 0


def read_state(path: str) -> Tuple[StoredState, Optional[str]]:
    """Liest die Zustandsdatei; das Zweite ist ein Hinweis für das Log oder ``None``.

    Fehlt die Datei, ist es der erste Start: wartend, keine Auslösungen. Ist sie unlesbar, weiß
    man nicht, ob der Watchdog scharf war; dann startet er scharf und löst nach dem Timeout aus,
    falls das EMS ausgefallen ist, statt ewig zu warten. Unlesbar heißt: nicht zu öffnen, kein
    JSON, kein Objekt oder ein Feld fehlt oder hat die falsche Art. Zusätzliche Felder schaden
    nicht.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return StoredState(), None
    except (OSError, ValueError, RecursionError):  # ValueError umfasst JSON- und Kodierungsfehler
        return StoredState(armed=True), UNREADABLE
    state = _from_json(data)
    if state is None:
        return StoredState(armed=True), UNREADABLE
    return state, None


def write_state(path: str, state: StoredState) -> None:
    """Schreibt die Zustandsdatei atomar.

    Der Inhalt geht zuerst nach ``<path>.tmp`` im selben Verzeichnis, wird mit ``flush`` und
    ``os.fsync`` auf die Platte gebracht und ersetzt dann die Datei: Ein Leser sieht immer den
    alten oder den neuen Inhalt, auch wenn der Strom mitten im Schreiben ausfällt. Bei einem
    Fehler bleibt die alte Datei, die temporäre wird entfernt, und der Fehler (``OSError``) wird
    weitergegeben. Es schreibt nur ein Prozess; die temporäre Datei hat einen festen Namen.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp = path + ".tmp"
    data = {
        "armed": state.armed,
        "trip_count": state.trip_count,
        "last_trip_time": state.last_trip_time,
    }
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):  # fehlt sie oder geht es nicht, zählt der erste Fehler
            os.remove(temp)
        raise


def _from_json(data: object) -> Optional[StoredState]:
    if not isinstance(data, dict):
        return None
    armed = data.get("armed")
    trip_count = _count(data.get("trip_count"))
    last_trip_time = _count(data.get("last_trip_time"))
    if not isinstance(armed, bool) or trip_count is None or last_trip_time is None:
        return None
    return StoredState(armed, trip_count, last_trip_time)


def _count(value: object) -> Optional[int]:
    """Die ganze Zahl ab 0 in ``value`` oder ``None``; ``bool`` zählt nicht als Zahl."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
