"""Aktor-Schnittstelle (Spec 6.1) und der Vergleich beim Rücklesen.

Der Ausführer (``executor.CommandExecutor``) kennt ein Gerät nur über das Protokoll ``Actuator``:
Der Aktor sagt, ob er steuerbar ist (``capabilities``), welche Werte das Gerät gerade hat
(``present``), nimmt einen Auftrag an (``send``), sagt, ob das Gerät den Wert übernommen hat
(``confirm``), und nennt seine eigenen Alarme (``alarms``). Die Frist fürs Rücklesen überwacht der
Ausführer, nicht der Aktor.

Ein Aktor, der ein schlafendes Fahrzeug für einen Start wecken darf (der Tesla), erfüllt zusätzlich
``WakeStart``: ``wake_capabilities`` sagt, ob ein Start mit Weckerlaubnis (``Command.wake``) jetzt
möglich ist. ``Actuator`` verlangt die Methode nicht; der Ausführer fragt per ``isinstance``.

``matches`` vergleicht den gesendeten mit dem zurückgelesenen Wert, mit den Toleranzen aus
Präzisierung 6 des Plans 2b; ``at_most`` prüft mit derselben Toleranz nur die Obergrenze. Die
Schicht ist rein: ohne I/O, die Zeit kommt als Argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Snapshot
from bindaems.shared.numbers import finite_number

# Bestätigt ist ein Wert, wenn der zurückgelesene höchstens so weit abweicht (Einheit der Art).
_TOLERANCE: Final = {
    CommandKind.CURRENT: 0.4,  # A
    CommandKind.GRID_SETPOINT: 1.0,  # W
    CommandKind.MAX_DISCHARGE: 1.0,  # W
    CommandKind.MIN_SOC: 0.5,  # %
    CommandKind.CHARGE_LIMIT: 0.5,  # % (Plan 4b, Präzisierung 9)
    CommandKind.SETPOINT_OFFSET: 0.05,  # K (Plan 6a, Entscheidung 9)
}
# Rundungsfehler bei Zahlen wie 6.4 − 6.0 sollen den Rand des Bands nicht ausschließen.
_ROUNDING: Final = 1e-9
# Entladegrenze „unbegrenzt“: Das ist kein Leistungswert, sondern der Sentinel der Einstellung.
_UNLIMITED: Final = -1.0


class Confirmation(StrEnum):
    """Stand des Rücklesens: noch offen, vom Gerät übernommen oder gescheitert.

    ``NOT_TAKEN`` ist ein Scheitern, nach dem derselbe Befehl erneut gesendet werden darf: Das Gerät
    hat ihn bestätigt, den Wert aber nicht übernommen (Tesla über Tessie, Prüfprotokoll Teil 2 vom
    12.10.2026). Der Aktor sendet dann nicht selbst nach; den neuen Versuch gibt der Aufrufer, in
    der Laufzeit wie jeden Befehl durch die Safety-Schicht.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    NOT_TAKEN = "not_taken"


@dataclass(frozen=True)
class Capabilities:
    """Ist der Aktor jetzt steuerbar? Wenn nicht, nennt ``reasons`` die Gründe, je ein Satz."""

    controllable: bool
    reasons: tuple[str, ...] = ()


class Actuator(Protocol):
    """Was der Ausführer von einem Aktor braucht (Plan 2b, Präzisierung 12).

    ``id`` ist die Aktor-ID des Katalogs der Safety-Schicht, ``confirm_timeout_s`` die Frist in
    Sekunden, binnen der ein gesendeter Wert zurückgelesen sein muss (ab dem Senden). Der Ausführer
    liest beide nur; ein Attribut, eine Klassenvariable oder eine Property erfüllen das Protokoll.

    Keine Methode blockiert. Ein Fehler des Geräts oder der Verbindung kommt über den Rückgabewert
    von ``send`` oder ``confirm``, nicht als Ausnahme.
    """

    @property
    def id(self) -> str: ...

    @property
    def confirm_timeout_s(self) -> float: ...

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Ist der Aktor zu ``now`` steuerbar, und wenn nicht, warum nicht?"""
        ...

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        """Die zurückgelesenen Werte je Art; was fehlt, ist unbekannt.

        Beim Strom ist das der Sollstrom, nicht der Messstrom. Beim Netz-Sollwert heißt ``None``
        „kein Override aktiv“.
        """
        ...

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        """Stößt das Schreiben an, ohne auf das Gerät zu warten.

        ``None``, wenn der Auftrag angenommen ist; sonst ein deutscher Grund, warum nicht gesendet
        wurde.
        """
        ...

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        """Hat das Gerät ``value`` übernommen? Der Text nennt den Grund eines Scheiterns."""
        ...

    def alarms(self, now: datetime) -> list[Alarm]:
        """Die Alarme, die der Aktor selbst kennt, zu ``now``; ohne eigenen Anlass ``[]``.

        EVCS und Victron haben keine eigenen. Der Tesla meldet, solange er nach Fehlern gesperrt
        ist.
        """
        ...


@runtime_checkable
class WakeStart(Protocol):
    """Ein Aktor, der für einen Start mit Weckerlaubnis eigene Fähigkeiten meldet (Design 7.1).

    Für einen Befehl ``charging`` mit ``True`` und ``Command.wake`` fragt der Ausführer
    ``wake_capabilities`` statt ``capabilities``: Ist das Fahrzeug nur deshalb nicht steuerbar, weil
    es schläft, kann der Start es wecken. Jeden anderen Befehl prüft der Ausführer wie bisher mit
    ``capabilities``. Wie bei ``Actuator`` blockiert die Methode nicht und wirft nicht.
    """

    def wake_capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        """Ist ein Start mit Weckerlaubnis zu ``now`` möglich, und wenn nicht, warum nicht?

        Das sind die Gründe von ``capabilities`` ohne die, die der Start selbst aufhebt (bei einem
        Fahrzeug: dass es schläft).
        """
        ...


def matches(kind: CommandKind, wanted: CommandValue, actual: CommandValue) -> bool:
    """Entspricht der zurückgelesene Wert ``actual`` dem gesendeten ``wanted``?

    Die Toleranzen je Art (Ränder gehören dazu):

    - ``current``: ±0,4 A
    - ``charging``: gleicher Wahrheitswert
    - ``grid_setpoint``: ±1 W, oder beide ``None`` (kein Override)
    - ``max_discharge``: ±1 W; −1 (unbegrenzt) passt nur zu −1, denn es ist kein Leistungswert und
      läge sonst im Band um 0 W (Entladen gesperrt)
    - ``min_soc``: ±0,5 %
    - ``charge_limit``: ±0,5 %
    - ``setpoint_offset``: ±0,05 K (das Echo der UVR kommt in Zehnteln)

    Werte vom falschen Typ passen nie, auch nicht zu sich selbst: ein ``bool`` ist keine Zahl,
    NaN und ±∞ sind keine Zahlen, und eine Zahl ist kein Wahrheitswert. ``None`` passt nur zu
    ``None``, und nur beim Netz-Sollwert ist es ein Wert.
    """
    if kind == CommandKind.CHARGING:
        return isinstance(wanted, bool) and isinstance(actual, bool) and wanted == actual
    if kind == CommandKind.GRID_SETPOINT and (wanted is None or actual is None):
        return wanted is None and actual is None
    want, have = finite_number(wanted), finite_number(actual)
    if want is None or have is None:
        return False
    if kind == CommandKind.MAX_DISCHARGE and _UNLIMITED in (want, have):
        return want == have
    return abs(want - have) <= _TOLERANCE.get(kind, 0.0) + _ROUNDING


def at_most(kind: CommandKind, limit: CommandValue, actual: CommandValue) -> bool:
    """Liegt der zurückgelesene Wert ``actual`` höchstens bei ``limit``?

    Nach oben gilt die Toleranz der Art aus ``matches`` (der Rand gehört dazu), nach unten keine
    Grenze. Nur endliche Zahlen zählen: ``bool``, ``None``, NaN und ±∞ liegen nie darunter. Für den
    sicheren Zustand beim Strom (``safe_state``): Weniger Strom ist sicherer.
    """
    want, have = finite_number(limit), finite_number(actual)
    if want is None or have is None:
        return False
    return have <= want + _TOLERANCE.get(kind, 0.0) + _ROUNDING
