"""Die Aktionen bei einer Auslösung: Sicherwerte schreiben, mit Lesen davor und Rücklesen danach.

Jede Aktion bringt einen D-Bus-Pfad auf seinen Sicherwert (Spec 8.6). Der Watchdog liest den Wert
zuerst und schreibt nur, wenn er abweicht; nach dem Schreiben liest er zurück und vergleicht. Ein
gescheiterter Versuch wird nach ``retry_s`` wiederholt, höchstens ``retries`` Mal. Der Zugriff auf
D-Bus steckt hinter ``ItemAccess``, damit die Abläufe ohne D-Bus prüfbar sind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from bindaems_watchdog.config import Action

OPEN = "offen"
OK = "ok"
UNCHANGED = "unverändert"
FAILED = "fehlgeschlagen"

_log = logging.getLogger(__name__)


class ItemAccess(Protocol):
    """Lesen und Schreiben einzelner Pfade anderer D-Bus-Dienste."""

    def find_service(self, service: str, instance: Optional[int]) -> Optional[str]:
        """Der Name des Dienstes vom Typ ``service`` mit dieser DeviceInstance oder ``None``."""
        ...

    def get_value(self, service: str, path: str) -> object:
        """Der aktuelle Wert; ``None`` oder eine leere Liste heißt ungültig."""
        ...

    def set_value(self, service: str, path: str, value: object) -> bool:
        """Schreibt ``value``; ``True``, wenn der Dienst den Wert angenommen hat."""
        ...


@dataclass
class _Task:
    """Der Stand einer Aktion seit dem letzten ``start``."""

    action: Action
    due: float
    attempts: int = 0  # gescheiterte Versuche
    result: str = OPEN


class ActionRunner:
    """Führt die Aktionen einer Auslösung aus, jede für sich und mit Wiederholungen.

    ``start`` beginnt einen Durchlauf, ``tick`` erledigt, was fällig ist. ``results`` hält je
    Aktion ``"offen"``, ``"ok"`` (geschrieben und zurückgelesen), ``"unverändert"`` (stand schon
    auf dem Sicherwert) oder ``"fehlgeschlagen"``; ``failures`` zählt die fehlgeschlagenen. Die
    Namen der Aktionen sind eindeutig (ein Abschnitt je Name in der ``config.ini``).
    """

    def __init__(
        self, access: ItemAccess, actions: Sequence[Action], retries: int, retry_s: float
    ) -> None:
        self._access = access
        self._actions = tuple(actions)
        self._retries = retries
        self._retry_s = retry_s
        self._tasks: List[_Task] = []
        self._failures = 0

    @property
    def busy(self) -> bool:
        """Ob noch eine Aktion offen ist."""
        return any(task.result == OPEN for task in self._tasks)

    @property
    def failures(self) -> int:
        """Zahl der fehlgeschlagenen Aktionen seit dem letzten ``start``."""
        return self._failures

    @property
    def results(self) -> Dict[str, str]:
        """Das Ergebnis je Aktion seit dem letzten ``start``."""
        return {task.action.name: task.result for task in self._tasks}

    def start(self, now: float) -> None:
        """Beginnt einen Durchlauf: Alle Aktionen sind sofort fällig, die Ergebnisse offen."""
        self._tasks = [_Task(action, now) for action in self._actions]
        self._failures = 0

    def tick(self, now: float) -> None:
        """Führt jede offene Aktion aus, die fällig ist."""
        for task in self._tasks:
            if task.result != OPEN or now < task.due:
                continue
            result = self._attempt(task.action)
            if result is not None:
                task.result = result
                continue
            task.attempts += 1
            if task.attempts > self._retries:
                task.result = FAILED
                self._failures += 1
            else:
                task.due = now + self._retry_s

    def _attempt(self, action: Action) -> Optional[str]:
        """Ein Versuch: ``"ok"`` oder ``"unverändert"``; ``None``, wenn er gescheitert ist."""
        name = action.name
        try:
            service = action.service
            if action.instance is not None:
                found = self._access.find_service(action.service, action.instance)
                if found is None:
                    _log.warning(
                        "Aktion %s: kein Dienst %s mit Instanz %d.",
                        name,
                        action.service,
                        action.instance,
                    )
                    return None
                service = found
            where = f"{service} {action.path}"
            before = self._access.get_value(service, action.path)
            if _matches(before, action.value, action.tolerance):
                _log.info("Aktion %s: %s steht schon auf %s.", name, where, _text(before))
                return UNCHANGED
            if not self._access.set_value(service, action.path, action.value):
                _log.warning("Aktion %s: %s nimmt %s nicht an.", name, where, _text(action.value))
                return None
            after = self._access.get_value(service, action.path)
            if _matches(after, action.value, action.tolerance):
                _log.info(
                    "Aktion %s: %s von %s auf %s gesetzt.",
                    name,
                    where,
                    _text(before),
                    _text(after),
                )
                return OK
            _log.warning(
                "Aktion %s: %s zeigt nach dem Schreiben %s statt %s.",
                name,
                where,
                _text(after),
                _text(action.value),
            )
            return None
        except Exception as exc:  # jeder Fehler ist ein gescheiterter Versuch
            _log.warning("Aktion %s: Versuch gescheitert: %s", name, exc)
            return None


def _matches(actual: object, safe: object, tolerance: float) -> bool:
    """Ob ``actual`` dem Sicherwert ``safe`` entspricht.

    Zum Sicherwert ``None`` (ungültig) passen ``None`` und eine leere Liste, denn D-Bus meldet
    „ungültig“ als leeres Array. Zahlen gelten bis ``tolerance`` als gleich; alles andere wird
    mit ``==`` verglichen.
    """
    if safe is None:
        return actual is None or (isinstance(actual, list) and not actual)
    wanted, found = _number(safe), _number(actual)
    if wanted is not None and found is not None:
        return abs(found - wanted) <= tolerance
    return actual == safe


def _number(value: object) -> Optional[float]:
    """``value`` als Zahl oder ``None``; ``bool`` zählt nicht als Zahl."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _text(value: object) -> str:
    """Ein Wert für das Log; ``None`` und leere Listen heißen „ungültig“."""
    if value is None or (isinstance(value, list) and not value):
        return "ungültig"
    return str(value)
