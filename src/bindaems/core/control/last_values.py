"""Die letzten eigenen Werte einer Regelung je Aktor und Art (Plan 2c-1, Präzisierung 10).

- ``Key``: Aktor und Art, der Schlüssel eines Werts
- ``LastValues``: der letzte eigene Wert je Schlüssel und die Merker ``not_taken_retry``; die
  Regelung schickt nur, was sich gegenüber diesem Wert ändert, und baut ihre Befehle mit ``command``
- ``ControlCommand``: ein Befehl der Regelung samt seinen Eingaben für das Entscheidungslog
- ``NOT_TAKEN_RETRY``: der Grund eines Befehls, der nach „nicht übernommen“ erneut kommt

Die Regelung schickt nur Änderungen (Plan 2c-1, Präzisierung 10): Der letzte eigene Wert je Aktor
und Art folgt den Urteilen (``note_verdict``) und den Ergebnissen (``note_result``). Sie vergisst
ihn, wo sich das Gerät ohne sie geändert haben kann; die Safety-Schicht antwortet ``UNCHANGED``,
wenn es den Wert schon hat:

- nach einer Pause (``idle``) für alle Aktoren,
- für den Aktor eines Ladepunkts, wenn er wieder geregelt wird, mit einem neuen Ansteckvorgang und
  wenn ein gemerkter Vorgang die Maschine erreicht; jedes davon geschieht einmal,
- mit jeder höheren Stufe der Überlast für alle geregelten Aktoren: Jede Stufe bietet ihre
  Reaktion einmal an,
- für die Aktoren, die ``forget`` nennt: Die Laufzeit tut das beim Wechsel der wirksamen
  Betriebsart (Dry-Run ↔ live), denn ein Dry-Run-Urteil zählte sonst als gesendet, nach einem
  Wert des Keepers und wenn ein anderes System den Aktor übernimmt.

Nach „nicht übernommen“ fordert die Regelung den Wert als normalen Befehl erneut an
(``not_taken_retry``, Plan 2c-1, Design 7.2): Er wartet auf die 90-s-Regel und die Stundenlimits,
auch wenn er sonst dringend wäre (Plan 2c-1, Präzisierung 11). Dringend bleibt das „Laden aus“ der
Überlast, eine andere Art: „Nicht übernommen“ meldet nur der Strom des Tesla.

Die Schicht ist rein: Sie rechnet nur mit den Urteilen und Ergebnissen, die sie bekommt.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Final

from bindaems.core.control.executor import WriteResult
from bindaems.core.safety.types import Outcome, Verdict
from bindaems.shared.control import Command, CommandKind, CommandValue
from bindaems.shared.decisions import DecisionResult

NOT_TAKEN_RETRY: Final = "not_taken_retry"  # erneut nach „nicht übernommen“

Key = tuple[str, CommandKind]  # Aktor und Art

# Diese Ergebnisse löschen den letzten eigenen Wert: Das Gerät hat ihn vielleicht nicht.
FAILED_RESULTS: Final = frozenset(
    {DecisionResult.ERROR, DecisionResult.TIMEOUT, DecisionResult.NOT_SENT}
)


@dataclass(frozen=True)
class ControlCommand:
    """Ein Befehl der Regelung und seine Eingaben für das Entscheidungslog.

    Bei der Laderegelung nennt ``inputs`` ``surplus_w`` (Überschuss in W), ``limit_a`` (freier
    Strom des Phasenwächters für den Ladepunkt), ``actual_current_a`` (Ist-Sollstrom des Aktors)
    und ``actual_charging`` (1.0 lädt, 0.0 lädt nicht); ``None`` heißt unbekannt (Plan 2c-1,
    Präzisierung 13).
    """

    command: Command
    inputs: Mapping[str, float | None]


@dataclass(frozen=True)
class _Sent:
    """Der letzte eigene Wert: angefordert (``requested``) und nach der Safety-Schicht.

    Mit ``requested`` vergleicht die Regelung, ob sich ein Wert ändert; ``applied`` ist der Wert,
    den ein Ergebnis des Ausführers nennt (``WriteResult.value``).
    """

    requested: CommandValue
    applied: CommandValue


class LastValues:
    """Die letzten eigenen Werte je Aktor und Art und die Merker ``not_taken_retry``.

    Gesetzt wird ein Wert durch ein Urteil mit Wert oder ``UNCHANGED`` (``judged``), gelöscht
    durch ein gescheitertes Ergebnis mit passendem Wert (``finished``) oder durch ``forget``: dort,
    wo sich das Gerät ohne die Regelung geändert haben kann.
    """

    def __init__(self) -> None:
        self._sent: dict[Key, _Sent] = {}
        self._retry: set[Key] = set()  # der nächste Befehl dieser Art trägt ``not_taken_retry``

    def changed(self, key: Key, value: CommandValue) -> bool:
        """Ob ``value`` vom letzten eigenen Wert dieser Art abweicht; ohne einen immer."""
        last = self._sent.get(key)
        return last is None or last.requested != value

    def requested(self, key: Key) -> CommandValue | None:
        """Der letzte eigene Wert dieser Art, wie angefordert; ``None`` ohne einen."""
        last = self._sent.get(key)
        return None if last is None else last.requested

    def holds(self, key: Key) -> bool:
        """Ob es einen letzten eigenen Wert dieser Art gibt.

        Nötig, weil ``requested`` ohne einen Wert und für den Wert ``None`` (kein Override)
        dasselbe liefert: ``None``.
        """
        return key in self._sent

    def retry(self, key: Key) -> bool:
        """Ob der nächste Befehl dieser Art ``not_taken_retry`` trägt."""
        return key in self._retry

    def command(
        self,
        actuator: str,
        kind: CommandKind,
        value: CommandValue,
        reasons: tuple[str, ...],
        *,
        urgent: bool = False,
        wake: bool = False,
    ) -> Command:
        """Der Befehl samt ``not_taken_retry``, wenn die Art zuletzt nicht übernommen wurde.

        Dieser erneute Befehl ist immer normal (Plan 2c-1, Design 7.2): Er wartet auf die 90-s-Regel
        und die Stundenlimits, auch wenn er sonst dringend wäre, etwa als Senkung an der Sicherung
        oder wegen Überlast. Die Sicherheit bleibt bei der Überlast, deren „Laden aus“ (eine andere
        Art) dringend bleibt.
        """
        retry = self.retry((actuator, kind))
        if retry:
            reasons, urgent = (*reasons, NOT_TAKEN_RETRY), False
        return Command(actuator, kind, value, urgent=urgent, reasons=reasons, wake=wake)

    def judged(self, command: Command, verdict: Verdict | None, *, not_sent: bool) -> bool:
        """Das Urteil über einen eigenen Befehl (``note_verdict`` der Regelung).

        Wahr, wenn es den Wert gesetzt hat: ein Urteil mit Wert oder ``UNCHANGED``, gesendet.
        """
        key = (command.actuator, command.kind)
        if not_sent:
            self._sent.pop(key, None)
            return False
        if verdict is None or not (verdict.sendable or verdict.outcome == Outcome.UNCHANGED):
            return False
        self._sent[key] = _Sent(command.value, verdict.value)
        self._retry.discard(key)
        return True

    def finished(self, result: WriteResult) -> None:
        """Das Ergebnis eines gesendeten Befehls (``note_result`` der Regelung)."""
        key = (result.actuator, result.kind)
        last = self._sent.get(key)
        if result.result not in FAILED_RESULTS or last is None or last.applied != result.value:
            return
        del self._sent[key]
        if result.not_taken:
            self._retry.add(key)

    def forget_key(self, key: Key) -> None:
        """Vergisst den Wert und den Merker ``not_taken_retry`` genau dieser Art; andere Arten des
        Aktors bleiben."""
        self._sent.pop(key, None)
        self._retry.discard(key)

    def forget(self, actuators: Collection[str] | None = None) -> None:
        """Vergisst Werte und Merker dieser Aktoren, ohne Angabe die aller Aktoren."""
        if actuators is None:
            self._sent.clear()
            self._retry.clear()
            return
        self._sent = {key: last for key, last in self._sent.items() if key[0] not in actuators}
        self._retry = {key for key in self._retry if key[0] not in actuators}
