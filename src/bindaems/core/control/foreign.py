"""Fremde Änderungen an Strom und Netz-Sollwert erkennen (Spec 7.9, Plan 2b, Präzisierung 8).

Hat das Gerät einen Wert des EMS bestätigt, erwartet der Detektor ihn dort (``expect``). Weicht der
zurückgelesene Wert davon ab (``matches`` der Aktor-Schnittstelle) und ist kein eigener
Schreibvorgang offen, ist das ein Treffer. Erst ``hits`` Treffer in Folge gelten als fremde
Änderung (``check``); ab dann ist der neue Wert die Erwartung.

Beobachtet werden der Strom und der Netz-Sollwert-Override (``WATCHED_KINDS``). Jede Erkennung
besteht als Alarm ``competitor.foreign.<aktor>`` für ``hold_s`` fort (``alarms``). Mit diesem Alarm
sperrt ``core.safety.locks`` den Aktor wie bei jedem mitregelnden System.

Die Schicht ist rein: ohne I/O, die Zeit kommt als Argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from bindaems.core.control.actuator import matches
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Severity
from bindaems.shared.numbers import de, finite_number

FOREIGN_HITS: Final = 3  # so viele Treffer in Folge gelten als fremde Änderung
WATCHED_KINDS: Final = frozenset({CommandKind.CURRENT, CommandKind.GRID_SETPOINT})

_ALARM_PREFIX: Final = "competitor.foreign."
# Name der Größe im Alarmtext und Einheit ihrer Werte, je beobachteter Art
_NAMES: Final = {CommandKind.CURRENT: "Strom", CommandKind.GRID_SETPOINT: "Netz-Sollwert"}
_UNITS: Final = {CommandKind.CURRENT: "A", CommandKind.GRID_SETPOINT: "W"}
_NO_OVERRIDE: Final = "kein Override"

_Key = tuple[str, CommandKind]  # Aktor und Art


@dataclass(frozen=True)
class ForeignChange:
    """Eine erkannte fremde Änderung: Das Gerät hatte ``actual`` statt des erwarteten ``expected``.

    ``at`` ist der Zeitpunkt der Prüfung, die sie erkannt hat. Beim Netz-Sollwert heißt ``None``
    „kein Override“.
    """

    actuator: str
    kind: CommandKind
    expected: CommandValue
    actual: CommandValue
    at: datetime


@dataclass
class _Watch:
    """Die Erwartung eines Paars und sein Zähler.

    ``count`` sind die Treffer in Folge seit der letzten Übereinstimmung.
    """

    expected: CommandValue
    count: int = 0


class ForeignChangeDetector:
    """Erkennt Änderungen an Strom und Override, die das EMS nicht geschrieben hat.

    Die Laufzeit meldet jeden bestätigten Wert mit ``expect`` und ruft ``check`` bei jeder Prüfung
    auf (alle 10 s). ``alarms`` liefert die Alarme für die Sperren; ``forget`` beendet die
    Beobachtung eines Aktors, der keine Werte des EMS mehr hält. ``hold_s`` ist die Zeit, die ein
    Alarm nach der Erkennung besteht (``safety.lock_release_s``), ``hits`` die Zahl der Treffer in
    Folge. Nicht für mehrere Threads gedacht.
    """

    def __init__(self, hold_s: float, hits: int = FOREIGN_HITS) -> None:
        self._hold = timedelta(seconds=hold_s)
        self._needed = hits
        self._watched: dict[_Key, _Watch] = {}
        self._latest: dict[str, ForeignChange] = {}  # die letzte Erkennung je Aktor

    def expect(self, actuator: str, kind: CommandKind, value: CommandValue) -> None:
        """Das EMS hat ``value`` am Gerät bestätigt: Ab jetzt wird es dort erwartet.

        Der Zähler der Treffer beginnt wieder bei 0. Für eine Art, die nicht in ``WATCHED_KINDS``
        steht, hat der Aufruf keine Wirkung.
        """
        if kind in WATCHED_KINDS:
            self._watched[(actuator, kind)] = _Watch(value)

    def forget(self, actuator: str) -> None:
        """Beendet die Beobachtung des Aktors: Erwartungen, Zähler und Erkennung sind vergessen.

        Sein Alarm endet damit sofort. Für einen Aktor ohne Erwartung hat der Aufruf keine Wirkung.
        """
        self._watched = {key: watch for key, watch in self._watched.items() if key[0] != actuator}
        self._latest.pop(actuator, None)

    def check(
        self,
        present: Mapping[_Key, CommandValue],
        pending: AbstractSet[_Key],
        now: datetime,
    ) -> list[ForeignChange]:
        """Vergleicht die zurückgelesenen Werte mit den Erwartungen und liefert neue Erkennungen.

        ``present`` sind die zurückgelesenen Werte je Aktor und Art (beim Netz-Sollwert heißt
        ``None`` „kein Override“, ein fehlender Eintrag „unbekannt“), ``pending`` die offenen
        Schreibvorgänge des Ausführers. Nur Paare mit Erwartung zählen; für jedes gilt:

        - Mit offenem Schreibvorgang wird das Paar übergangen, der Zähler bleibt.
        - Ein unbekannter Ist-Wert ist weder Treffer noch Übereinstimmung, der Zähler bleibt.
        - Passt der Ist-Wert (``matches``), beginnt der Zähler wieder bei 0.
        - Sonst ist es ein Treffer. Beim ``hits``-ten Treffer in Folge ist die Änderung erkannt:
          Der Ist-Wert wird die neue Erwartung, und der Zähler beginnt wieder bei 0.

        Die Erkennungen kommen in der Reihenfolge der Erwartungen.
        """
        found: list[ForeignChange] = []
        for key, watch in self._watched.items():
            if key in pending or key not in present:
                continue
            actuator, kind = key
            actual = present[key]
            if matches(kind, watch.expected, actual):
                watch.count = 0
                continue
            watch.count += 1
            if watch.count < self._needed:
                continue
            change = ForeignChange(actuator, kind, watch.expected, actual, now)
            watch.expected, watch.count = actual, 0
            self._latest[actuator] = change
            found.append(change)
        return found

    def alarms(self, now: datetime, labels: Mapping[str, str]) -> list[Alarm]:
        """Je Aktor mit einer Erkennung der Alarm ``competitor.foreign.<aktor>`` (Fehler).

        Text und ``since`` stammen aus der letzten Erkennung des Aktors. Der Alarm besteht,
        solange ``now − since`` unter ``hold_s`` liegt. Der Text nennt Größe, Ist- und Sollwert mit
        Einheit; beim Netz-Sollwert steht „kein Override“ für ``None``. Ohne Eintrag in ``labels``
        steht die Aktor-ID im Text.
        """
        alarms: list[Alarm] = []
        for actuator, change in self._latest.items():
            if now - change.at >= self._hold:
                continue
            label, kind = labels.get(actuator, actuator), change.kind
            actual, expected = _show(kind, change.actual), _show(kind, change.expected)
            message = f"{label}: {_NAMES[kind]} von außen geändert ({actual} statt {expected})."
            alarms.append(Alarm(f"{_ALARM_PREFIX}{actuator}", Severity.ERROR, message, change.at))
        return alarms


def _show(kind: CommandKind, value: CommandValue) -> str:
    """Der Wert im Alarmtext: Zahl mit Einheit, beim Netz-Sollwert ``None`` als „kein Override“."""
    if kind == CommandKind.GRID_SETPOINT and value is None:
        return _NO_OVERRIDE
    number = finite_number(value)
    return "unbekannt" if number is None else f"{de(number)} {_UNITS[kind]}"
