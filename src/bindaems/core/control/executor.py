"""Der Ausführer: bringt jeden Befehl durch Safety-Schicht, Aktor und Rücklesen (Spec 6.1, 7.10).

``CommandExecutor.execute`` holt für einen Befehl genau ein Urteil der Safety-Schicht, denn
``authorize`` verändert Rate-Limiter und Schreibbudget. Ist für Aktor und Art noch ein
Schreibvorgang offen, wird der Befehl gar nicht betrachtet. Das Urteil entscheidet dann:

- Soll gesendet werden, prüft der Ausführer die Fähigkeiten des Aktors und sendet. Der
  Schreibvorgang bleibt offen, bis ``poll`` ihn zurückgelesen hat, gescheitert meldet oder die
  Frist verstreicht. Ein Start mit Weckerlaubnis (``Command.wake`` bei ``charging`` mit ``True``)
  an einen Aktor mit ``WakeStart`` prüft statt der Fähigkeiten dessen ``wake_capabilities``: Ein
  schlafendes Fahrzeug kann dafür steuerbar sein. Das Urteil der Safety-Schicht bleibt dasselbe;
  die Weckerlaubnis gilt nur gegenüber dem Aktor.
- Im Dry-Run ergibt ein Urteil mit Wert die Entscheidung ``DRY_RUN`` (nicht bei einem Auffrischen,
  siehe unten), ein abgelehnter Befehl ``REJECTED``; dieselbe Ablehnung (Aktor, Art, angeforderter
  Wert, Befund-Codes) wird höchstens alle 60 s protokolliert.
- Ein Befehl, den ein Stundenlimit zurückstellt (``DEFERRED`` mit dem Befund ``rate.per_hour`` oder
  ``rate.start_stop``, beim Tesla), ergibt die Entscheidung ``DEFERRED``; auch dieselbe
  Zurückstellung höchstens alle 60 s. Ablehnungen und Zurückstellungen halten einander dabei nicht
  zurück. So bricht der Dry-Run-Vergleich am Stundenlimit nicht stumm ab.
- ``UNCHANGED`` und jede andere Zurückstellung (etwa ``rate.interval``) ergeben keine Entscheidung.

Jeder gesendete Befehl endet in genau einer Entscheidung: bestätigt (``OK``), gescheitert
(``ERROR``), unbestätigt (``TIMEOUT``) oder nicht gesendet (``NOT_SENT``). Die letzten drei
Ergebnisse sammelt ``take_failures`` für den fehlerfreien Zyklus (Präzisierung 5 des Plans 2b).
Hat das Gerät einen bestätigten Wert nicht übernommen (``Confirmation.NOT_TAKEN``), ist das Ergebnis
``ERROR`` und trägt ``WriteResult.not_taken``; die Regelung fordert den Wert dann erneut an.

Eine Ausnahme ist das Auffrischen eines Overrides (``Command.refresh`` beim Netz-Sollwert, Plan
2c-2a, Präzisierung 7): Es kommt in festem Abstand (``control.override_refresh_s``, Standard
240 s) mit dem Wert, den der Cerbo schon hat, und füllte sonst das Log. Für einen solchen Befehl
entsteht bei ``DRY_RUN`` und bei ``OK`` keine Entscheidung. ``WriteResult`` und ``take_failures``
bleiben, wie sie sind, und alles andere steht im Log wie bei jedem Befehl: die Ablehnung,
``ERROR``, ``TIMEOUT`` und ``NOT_SENT``. Bei jeder anderen Art hat ``refresh`` keine Wirkung: Auch
ein Strom mit dem Merkmal steht im Log wie jeder andere.

Zwei Uhren: ``ctx.now`` und ``now`` sind die Zeit der Safety-Schicht (``AnchoredClock``); mit ihr
laufen die Fristen und die 60 s der Ablehnungen und Zurückstellungen. ``wall_now`` ist die Wanduhr
und nur der Zeitpunkt der Entscheidung.

Die Schicht ist rein: Die Zeit kommt als Argument, Aktoren und Entscheidungslog erreicht sie nur
über die Protokolle ``Actuator`` und ``DecisionSink``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import uuid4

from bindaems.core.control.actuator import Actuator, Capabilities, Confirmation, WakeStart
from bindaems.core.safety.layer import SafetyLayer
from bindaems.core.safety.rate import HOURLY_LIMITS
from bindaems.core.safety.types import Outcome, SafetyContext, Verdict
from bindaems.shared.control import Command, CommandKind, CommandValue
from bindaems.shared.decisions import Decision, DecisionResult, decision_text
from bindaems.shared.domain import Snapshot

_REPEAT_INTERVAL: Final = timedelta(seconds=60)  # dieselbe Ablehnung bzw. Zurückstellung so oft
# Diese Ergebnisse stehen für ein Auffrischen eines Overrides nicht im Log, alle anderen schon
_QUIET_REFRESH: Final = frozenset({DecisionResult.OK, DecisionResult.DRY_RUN})

_Key = tuple[str, CommandKind]  # Aktor und Art: ein Schreibvorgang je Schlüssel
# Ergebnis (Ablehnung oder Zurückstellung), Aktor, Art, angeforderter Wert und die Codes der Befunde
_RepeatKey = tuple[DecisionResult, str, CommandKind, CommandValue, tuple[str, ...]]


class DecisionSink(Protocol):
    """Nimmt die Entscheidungen entgegen (Spool und Stream); sie wirft nicht."""

    def __call__(self, decision: Decision) -> None: ...


@dataclass(frozen=True)
class WriteResult:
    """Ausgang eines gesendeten Befehls.

    ``value`` ist der gesendete Wert (nach der Safety-Schicht), ``result`` eins von ``OK``,
    ``ERROR``, ``TIMEOUT`` und ``NOT_SENT``. ``detail`` nennt den Grund oder den letzten Text des
    Aktors beim Rücklesen. ``not_taken`` heißt: Das Gerät hat den bestätigten Wert nicht
    übernommen (``Confirmation.NOT_TAKEN``); das Ergebnis ist dann ``ERROR``, und derselbe Wert
    darf erneut angefordert werden.
    """

    actuator: str
    kind: CommandKind
    value: CommandValue
    result: DecisionResult
    detail: str
    not_taken: bool = False


@dataclass(frozen=True)
class _Open:
    """Ein gesendeter Wert, der noch nicht zurückgelesen ist, samt dem Rest für die Entscheidung."""

    verdict: Verdict
    actuator: Actuator
    deadline: datetime
    inputs: Mapping[str, float | None]


class CommandExecutor:
    """Führt Befehle aus und wartet auf das Rücklesen (Plan 2b, Präzisierung 6 und 7).

    ``actuators`` und ``labels`` sind je Aktor-ID gegeben; ohne Eintrag in ``labels`` steht die ID
    im Text. ``sink`` bekommt jede Entscheidung, ``new_id`` liefert ihre Kennung.

    Der Ausführer gehört in den Zyklus der Laufzeit und ist nicht für mehrere Threads gedacht. Die
    Methoden der Aktoren dürfen nicht blockieren und werfen nicht (``Actuator``).
    """

    def __init__(
        self,
        layer: SafetyLayer,
        actuators: Mapping[str, Actuator],
        sink: DecisionSink,
        labels: Mapping[str, str],
        *,
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self._layer = layer
        self._actuators = actuators
        self._sink = sink
        self._labels = labels
        self._new_id = new_id
        self._open: dict[_Key, _Open] = {}
        self._failures: list[WriteResult] = []
        self._repeated: dict[_RepeatKey, datetime] = {}  # wann zuletzt protokolliert

    def execute(
        self,
        command: Command,
        ctx: SafetyContext,
        snap: Snapshot,
        wall_now: datetime,
        inputs: Mapping[str, float | None],
    ) -> Verdict | None:
        """Prüft den Befehl und führt das Urteil aus; ``inputs`` kommen in die Entscheidung.

        ``None``, wenn für Aktor und Art ein Schreibvorgang offen ist: Dann wird der Befehl nicht
        betrachtet, und die Safety-Schicht läuft nicht. Sonst das Urteil der Safety-Schicht, auch
        wenn der Befehl danach nicht gesendet werden konnte (``NOT_SENT``). Wurde gesendet, ist der
        Schreibvorgang offen (``pending``) mit der Frist ``ctx.now + confirm_timeout_s``.
        """
        if (command.actuator, command.kind) in self._open:
            return None
        verdict = self._layer.authorize(command, ctx)
        if verdict.send:
            self._send(verdict, ctx.now, snap, wall_now, inputs)
        elif verdict.sendable:  # ein Wert steht fest, aber der Aktor ist im Dry-Run
            self._decide(verdict, DecisionResult.DRY_RUN, wall_now, inputs)
        else:
            result = _held_back(verdict)
            if result is not None and self._due(verdict, result, ctx.now):
                self._decide(verdict, result, wall_now, inputs)
        return verdict

    def poll(self, snap: Snapshot, now: datetime, wall_now: datetime) -> list[WriteResult]:
        """Fragt die offenen Schreibvorgänge ab und schließt die fertigen ab.

        Jeder abgeschlossene Schreibvorgang ergibt genau eine Entscheidung, außer ein bestätigtes
        Auffrischen eines Overrides (``Command.refresh`` beim Netz-Sollwert). Zuerst fragt der
        Ausführer den Aktor (``confirm``): bestätigt ergibt ``OK``, gescheitert oder nicht
        übernommen (``NOT_TAKEN``) ``ERROR`` mit dem Text des Aktors; bei ``NOT_TAKEN`` trägt das
        Ergebnis zusätzlich ``not_taken``. Einen neuen Versuch gibt der Aufrufer wie jeden Befehl.
        Ist der Wert noch offen und die Frist erreicht (``now >= deadline``), ist es ``TIMEOUT``;
        ein Wert, der genau zur Frist ankommt, gilt also als bestätigt. Sonst bleibt der
        Schreibvorgang offen. Zurück kommen die abgeschlossenen in der Reihenfolge, in der sie
        gesendet wurden.
        """
        finished: list[WriteResult] = []
        for key, write in list(self._open.items()):
            status, detail = write.actuator.confirm(key[1], write.verdict.value, snap)
            if status == Confirmation.CONFIRMED:
                result = DecisionResult.OK
            elif status in (Confirmation.FAILED, Confirmation.NOT_TAKEN):
                result = DecisionResult.ERROR
            elif now >= write.deadline:
                result = DecisionResult.TIMEOUT
            else:
                continue
            del self._open[key]
            finished.append(
                self._finish(
                    write.verdict,
                    result,
                    detail,
                    wall_now,
                    write.inputs,
                    not_taken=status == Confirmation.NOT_TAKEN,
                )
            )
        return finished

    def pending(self) -> frozenset[tuple[str, CommandKind]]:
        """Aktor und Art aller Schreibvorgänge, die noch auf das Rücklesen warten."""
        return frozenset(self._open)

    def take_failures(self) -> list[WriteResult]:
        """Alle ``ERROR``, ``TIMEOUT`` und ``NOT_SENT`` seit dem letzten Aufruf, in der Reihenfolge.

        Der Aufruf leert die Liste. Eine Ablehnung der Safety-Schicht ist kein Fehler beim
        Schreiben und kommt nicht vor.
        """
        failures, self._failures = self._failures, []
        return failures

    def _send(
        self,
        verdict: Verdict,
        now: datetime,
        snap: Snapshot,
        wall_now: datetime,
        inputs: Mapping[str, float | None],
    ) -> None:
        """Sendet den Wert; wurde nicht gesendet, endet der Befehl mit ``NOT_SENT``."""
        reason = self._hand_over(verdict, now, snap, inputs)
        if reason is not None:
            self._finish(verdict, DecisionResult.NOT_SENT, reason, wall_now, inputs)

    def _hand_over(
        self, verdict: Verdict, now: datetime, snap: Snapshot, inputs: Mapping[str, float | None]
    ) -> str | None:
        """Prüft die Fähigkeiten, übergibt den Wert dem Aktor und öffnet den Schreibvorgang.

        Der Grund, wenn nicht gesendet wurde: Der Aktor ist nicht steuerbar oder lehnt den
        Auftrag ab. Sonst ``None``. Bei einem Start mit Weckerlaubnis zählen die Weckfähigkeiten
        des Aktors statt seiner Fähigkeiten (``_capabilities``).
        """
        command = verdict.command
        actuator = self._actuators.get(command.actuator)
        if actuator is None:  # der Katalog kennt ihn, der Ausführer hat keinen Treiber dafür
            return _not_controllable(("Aktor nicht eingerichtet.",))
        capabilities = _capabilities(actuator, verdict, snap, now)
        if not capabilities.controllable:
            return _not_controllable(capabilities.reasons)
        refusal = actuator.send(command.kind, verdict.value)
        if refusal is not None:
            return refusal
        deadline = now + timedelta(seconds=actuator.confirm_timeout_s)
        self._open[(command.actuator, command.kind)] = _Open(
            verdict, actuator, deadline, dict(inputs)
        )
        return None

    def _finish(
        self,
        verdict: Verdict,
        result: DecisionResult,
        detail: str,
        wall_now: datetime,
        inputs: Mapping[str, float | None],
        *,
        not_taken: bool = False,
    ) -> WriteResult:
        """Schließt einen gesendeten Befehl ab: merkt einen Fehler und schreibt die Entscheidung."""
        command = verdict.command
        write = WriteResult(
            command.actuator, command.kind, verdict.value, result, detail, not_taken
        )
        if result != DecisionResult.OK:
            self._failures.append(write)
        self._decide(verdict, result, wall_now, inputs, detail=detail)
        return write

    def _decide(
        self,
        verdict: Verdict,
        result: DecisionResult,
        wall_now: datetime,
        inputs: Mapping[str, float | None],
        *,
        detail: str = "",
    ) -> None:
        """Gibt die Entscheidung zu ``verdict`` an die Senke.

        Ein Auffrischen eines Overrides (``Command.refresh`` beim Netz-Sollwert) ergibt bei ``OK``
        und bei ``DRY_RUN`` keine.
        """
        command = verdict.command
        if _is_refresh(command) and result in _QUIET_REFRESH:
            return
        label = self._labels.get(command.actuator, command.actuator)
        # Der Text nennt den Wert nach der Safety-Schicht; ohne einen (Ablehnung) den angeforderten.
        shown = command.value if verdict.outcome == Outcome.REJECTED else verdict.value
        findings = verdict.findings
        decision = Decision(
            id=self._new_id(),
            ts=wall_now,
            actuator=command.actuator,
            kind=command.kind,
            requested=command.value,
            applied=verdict.value,
            dry_run=verdict.dry_run,
            outcome=str(verdict.outcome),
            result=result,
            codes=(*command.reasons, *(finding.code for finding in findings)),
            text=decision_text(
                label,
                command.kind,
                shown,
                result,
                detail=detail,
                reasons=command.reasons,
                findings=[finding.text for finding in findings],
            ),
            inputs=dict(inputs),
        )
        self._sink(decision)

    def _due(self, verdict: Verdict, result: DecisionResult, now: datetime) -> bool:
        """Soll diese Ablehnung bzw. Zurückstellung (``result``) protokolliert werden?

        Dieselbe höchstens alle 60 s: gleiches Ergebnis, gleicher Aktor, gleiche Art, gleicher
        angeforderter Wert und gleiche Befund-Codes. Eine Ablehnung hält so keine Zurückstellung
        zurück und umgekehrt, ein Start keinen Stopp. Die 60 s laufen auf der Zeit der
        Safety-Schicht, ab der letzten protokollierten.
        """
        command = verdict.command
        codes = tuple(finding.code for finding in verdict.findings)
        key = (result, command.actuator, command.kind, command.value, codes)
        last = self._repeated.get(key)
        if last is not None and now - last < _REPEAT_INTERVAL:
            return False
        # Was länger als 60 s zurückliegt, zählt nicht mehr und kann gehen.
        self._repeated = {
            old: when for old, when in self._repeated.items() if now - when < _REPEAT_INTERVAL
        }
        self._repeated[key] = now
        return True


def _is_refresh(command: Command) -> bool:
    """Frischt der Befehl einen Override auf? ``Command.refresh`` zählt nur beim Netz-Sollwert."""
    return command.refresh and command.kind == CommandKind.GRID_SETPOINT


def _held_back(verdict: Verdict) -> DecisionResult | None:
    """Das Ergebnis eines Urteils, das nichts sendet, aber ins Log gehört; sonst ``None``.

    ``REJECTED`` für eine Ablehnung, ``DEFERRED`` für eine Zurückstellung wegen eines Stundenlimits
    (``HOURLY_LIMITS``). Andere Zurückstellungen und ``UNCHANGED`` gehören nicht hinein.
    """
    if verdict.outcome == Outcome.REJECTED:
        return DecisionResult.REJECTED
    if verdict.outcome == Outcome.DEFERRED and any(
        finding.code in HOURLY_LIMITS for finding in verdict.findings
    ):
        return DecisionResult.DEFERRED
    return None


def _capabilities(
    actuator: Actuator, verdict: Verdict, snap: Snapshot, now: datetime
) -> Capabilities:
    """Die Fähigkeiten, nach denen der Befehl des Urteils gesendet wird.

    Ein Start mit Weckerlaubnis (``Command.wake``, Art ``charging``, gesendet wird ``True``) an
    einen Aktor mit ``WakeStart`` fragt dessen ``wake_capabilities``; jeder andere Befehl, auch
    einer mit ``wake`` bei einem Strom oder einem Stopp, die ``capabilities``.
    """
    command = verdict.command
    if (
        command.wake
        and command.kind == CommandKind.CHARGING
        and verdict.value is True
        and isinstance(actuator, WakeStart)
    ):
        return actuator.wake_capabilities(snap, now)
    return actuator.capabilities(snap, now)


def _not_controllable(reasons: tuple[str, ...]) -> str:
    """Der Grund „nicht steuerbar“ mit den Gründen des Aktors, durch Leerzeichen verbunden."""
    return f"nicht steuerbar: {' '.join(reasons)}" if reasons else "nicht steuerbar"
