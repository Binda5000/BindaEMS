"""Welche Aktoren Werte des EMS halten (Plan 2b, Präzisierung 3: Übergabe).

Ein Aktor ist owned, sobald der core ihm einen Wert gesendet hat; beim Start gelten die als owned,
die die gespeicherte Absicht live anfordert und ``config.yaml`` freigibt (``initially_owned``).
Ist ein owned Aktor nicht mehr live gewünscht, wird er übergeben: Er bekommt seine Sicherwerte,
und erst wenn sie zurückgelesen sind, gibt ihn die Laufzeit frei (``release``). Ein gesperrter
Aktor, den nachweislich ein anderes System regelt, verliert „owned“ sofort (``drop``). Eine Sperre,
die nur ein nicht lesbarer Prüfpunkt verursacht, nimmt ihm die Werte des EMS nicht.

Die Schicht ist rein: Wann ein Aktor übergeben ist, entscheidet die Laufzeit.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Set as AbstractSet


class Ownership:
    """Die Menge der owned Aktoren; die Laufzeit hält sie über die Zyklen."""

    def __init__(self, owned: Iterable[str] = ()) -> None:
        self._owned: set[str] = set(owned)

    @property
    def owned(self) -> frozenset[str]:
        """Die Aktoren, die Werte des EMS halten können, als Momentaufnahme."""
        return frozenset(self._owned)

    def note_sent(self, actuator: str) -> None:
        """Der core hat dem Aktor einen Wert gesendet: Ab jetzt ist er owned."""
        self._owned.add(actuator)

    def release(self, actuator: str) -> None:
        """Der Aktor ist übergeben und hält keine Werte des EMS mehr; sonst ohne Wirkung."""
        self._owned.discard(actuator)

    def drop(self, actuators: Iterable[str]) -> frozenset[str]:
        """Nimmt die Aktoren ohne Übergabe heraus, denn ein anderes System regelt sie.

        Das Ergebnis sind die Aktoren davon, die owned waren.
        """
        dropped = self._owned.intersection(actuators)
        self._owned -= dropped
        return frozenset(dropped)

    def handing_back(self, wanted: AbstractSet[str]) -> frozenset[str]:
        """Die owned Aktoren, die nicht mehr live gewünscht sind: owned − ``wanted``."""
        return frozenset(self._owned.difference(wanted))
