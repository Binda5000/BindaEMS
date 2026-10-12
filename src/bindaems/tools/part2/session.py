"""Sitzung des Prüfprotokolls Teil 2 (Spec 17.2): dieselben Adapter und Aktoren wie im core.

Das Werkzeug ``bindaems.tools.verify_part2`` schreibt selbst auf die Geräte, mit Rückfrage beim
Betreiber (Plan 2b, Präzisierung 11). Die Sitzung startet dafür die echten Adapter und schreibt
über die Aktoren des core (``build_session``). Für jeden Wert gilt:

- Vorher prüfen ihn die Regeln 1–3 der Safety-Schicht (``check_range``). Würden sie ihn klemmen
  oder ablehnen, ist das ein Fehler des Werkzeugs (``ValueError``); gesendet wird dann nichts.
- Gesendet wird nur an einen Aktor, der in diesem Moment steuerbar ist (``capabilities``). Einen
  schlafenden Tesla weckt die Sitzung so nie.
- Innerhalb der Frist bleibt sie hartnäckig: Sie wartet, bis der Aktor steuerbar ist, sendet
  erneut, wenn er ablehnt, und ohne Bestätigung nach ``resend_s`` noch einmal. So übersteht ein
  Wert das Neuverbinden zum Cerbo nach einer langen Rückfrage.
- Vor dem ersten Schreiben je Aktor und Art merkt sie sich den vorgefundenen Wert (``present``).
  Ist er unbekannt, schreibt sie nicht: Er ließe sich nicht wiederherstellen. Die Teile prüfen
  vorher mit ``found_problem``, ob er zu den Regeln 1–3 passt; sonst ließe er sich auch nicht
  wiederherstellen.
- Eine Rückfrage liest von der Konsole, solange steht der Event-Loop und mit ihm der Adapter von
  Tessie. Dessen Werte bleiben 900 s gültig, doch das Auto kann inzwischen schlafen, und ein Befehl
  weckte es. Werte von Tessie, die nicht neuer sind als die letzte Antwort, gelten darum als
  unbekannt: Vor dem Schreiben an einen Tesla wartet die Sitzung auf einen neueren Wert
  (``fresh_after_question``) und sendet sonst nichts.
- Nach jedem Schritt (``restore_after_step``), bei Abbruch und am Ende (``restore``) stellt sie
  die vorgefundenen Werte in umgekehrter Reihenfolge wieder her, auch nach Strg+C. Ein Wert, der
  schon wieder steht, wird nicht noch einmal geschrieben. Auch beim Wiederherstellen schreibt sie
  nur Werte, die die Regeln 1–3 unverändert durchlassen.
- Persistente Einstellungen (Entladegrenze, Min-SOC) sind Flash am Cerbo: Die Sitzung sendet sie je
  Lauf höchstens ``MAX_PERSISTENT_WRITES``-mal, das Wiederherstellen eingeschlossen. Danach
  verweigert sie das Schreiben, ohne zu senden. Der Betreiber muss einen Wert, der so stehen bleibt,
  von Hand zurückstellen; das Wiederherstellen sagt es einmal als Befund.

Die Teile halten jeden Befund mit ``record`` fest, sobald er feststeht. So nennt der Bericht nach
einem Abbruch die Schritte bis dahin.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from functools import partial
from types import MappingProxyType
from typing import Final

import httpx
from pydantic import SecretStr

from bindaems.core.actuators.evcs import EvcsActuator
from bindaems.core.actuators.tesla import TeslaActuator
from bindaems.core.actuators.victron import VictronActuator
from bindaems.core.adapters.base import Adapter
from bindaems.core.adapters.evcs import EvcsAdapter, PymodbusReader
from bindaems.core.adapters.tessie import TessieAdapter
from bindaems.core.adapters.twc import TwcAdapter
from bindaems.core.adapters.victron_mqtt import SOURCE as VICTRON_SOURCE
from bindaems.core.adapters.victron_mqtt import AiomqttTransport, VictronMqttAdapter
from bindaems.core.control.actuator import Actuator, Confirmation, matches
from bindaems.core.safety.limits import Family, HardLimits
from bindaems.core.safety.ranges import check_range
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, EvcsConfig, Secrets
from bindaems.shared.control import (
    PERSISTENT_KINDS,
    VICTRON,
    Command,
    CommandKind,
    CommandValue,
)
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import SystemClock
from bindaems.tools.verify_checks import Finding

RESTORE_S: Final = 15.0  # Frist je wiederherzustellendem Wert, vom Senden bis zur Bestätigung
RESEND_S: Final = 5.0  # ohne Bestätigung geht derselbe Wert nach so vielen Sekunden noch einmal
MAX_PERSISTENT_WRITES: Final = 4  # persistente Schreibzugriffe je Lauf, alle Arten zusammen
PERSISTENT_LIMIT: Final = (
    f"nicht gesendet: höchstens {MAX_PERSISTENT_WRITES} persistente Schreibzugriffe je Lauf."
)
SKIPPED_UNATTENDED: Final = "übersprungen (--yes)"  # Schritt mit Handgriff, aber niemand da
UNKNOWN_ORIGINAL: Final = "Ausgangswert unbekannt; er ließe sich nicht wiederherstellen."
STALE_AFTER_QUESTION: Final = "Fahrzeugdaten nach der Rückfrage nicht aktuell."
CLOUD_SIGNAL: Final = "online"  # dieses Signal des Tesla meldet Tessie bei jeder Abfrage neu

_LABELS: Final = {"ok": "OK", "warn": "WARNUNG", "fail": "FEHLER", "info": "INFO"}
# Punkt im Prüfprotokoll für die Zeilen des Wiederherstellens: je Familie, bei Victron je Art
_RESTORE_ITEMS: Final[Mapping[str, str]] = {"evcs": "17.2-6", "tesla": "17.2-7"}
_VICTRON_ITEMS: Final[Mapping[CommandKind, str]] = {
    CommandKind.GRID_SETPOINT: "17.2-1",
    CommandKind.MAX_DISCHARGE: "17.2-2",
    CommandKind.MIN_SOC: "17.2-2",
}

_Key = tuple[str, CommandKind]  # Aktor und Art


def seconds(value: float) -> str:
    """Sekunden für Texte: auf eine Nachkommastelle gerundet, mit Dezimalkomma (``de``)."""
    return de(round(value, 1))


def minutes(value_s: float) -> str:
    """Minuten für Texte aus ``value_s`` Sekunden: eine Nachkommastelle, Dezimalkomma (``de``)."""
    return de(round(value_s / 60, 1))


def _show(finding: Finding) -> None:
    """Zeigt einen Befund auf der Konsole, wie er im Bericht steht."""
    label = _LABELS[finding.status]
    print(f"{finding.item} {finding.title}: {label} – {finding.detail}")


def _not_controllable(reasons: Sequence[str]) -> str:
    """Wie im Ausführer: „nicht steuerbar: {Gründe, mit Leerzeichen verbunden}“."""
    return f"nicht steuerbar: {' '.join(reasons)}" if reasons else "nicht steuerbar"


def _range_problems(
    actuator: str, kind: CommandKind, value: CommandValue, limits: HardLimits
) -> str | None:
    """Was die Regeln 1–3 an dem Wert ändern oder ablehnen würden; ``None``, wenn nichts."""
    step = check_range(Command(actuator, kind, value), limits, None)
    if not step.reject and not step.findings:
        return None
    return " ".join(finding.text for finding in step.findings) or "abgelehnt"


def _value_text(kind: CommandKind, value: CommandValue) -> str:
    """Art und Wert für die Zeilen des Wiederherstellens: „Strom 12 A“, „Laden an“, …"""
    number = finite_number(value)
    shown = de(number) if number is not None else repr(value)
    if kind == CommandKind.CURRENT:
        return f"Strom {shown} A"
    if kind == CommandKind.CHARGING:
        return "Laden an" if value is True else "Laden aus" if value is False else f"Laden {shown}"
    if kind == CommandKind.GRID_SETPOINT:
        return "kein Netz-Sollwert-Override" if value is None else f"Netz-Sollwert {shown} W"
    if kind == CommandKind.MAX_DISCHARGE:
        return "Entladegrenze unbegrenzt" if number == -1 else f"Entladegrenze {shown} W"
    if kind == CommandKind.MIN_SOC:
        return f"Min-SOC {shown} %"
    return f"{kind} {shown}"


def _restore_item(family: Family, kind: CommandKind) -> str:
    if family == "victron":
        return _VICTRON_ITEMS.get(kind, "17.2-1")
    return _RESTORE_ITEMS[family]


class Part2Session:
    """Adapter, Aktoren und Zustandsspeicher eines Laufs von Teil 2 (Plan 2b, Präzisierung 11).

    - ``cfg`` ist die Konfiguration, ``limits`` die harten Grenzen daraus (Regeln 1–3).
    - ``adapters`` laufen zwischen ``start`` und ``stop`` als Tasks und füllen ``store``.
    - ``actuators`` sind die Aktoren je Aktor-ID, wie im core.
    - ``ask`` stellt dem Betreiber eine Ja/Nein-Frage.
    - ``monotonic`` ist die Uhr für Wartezeiten, auch für die Teile; ``poll_s`` der Abstand, in
      dem Rücklesen und Warten den Speicher ansehen.
    - ``cleanup`` läuft am Ende von ``stop``, etwa um den HTTP-Client zu schließen.
    - ``resend_s``: so lange wartet ``set_and_confirm`` auf die Bestätigung, bevor es denselben
      Wert noch einmal sendet.
    - ``unattended`` (``--yes``): Niemand führt Handgriffe aus; die Teile überspringen Schritte,
      die einen Handgriff des Betreibers brauchen (``SKIPPED_UNATTENDED``).

    ``findings`` sind die Befunde, die die Teile mit ``record`` festhalten. ``failure`` nennt,
    warum der letzte Aufruf von ``set_and_confirm`` keinen bestätigten Wert ergab. ``confirmed``
    ist der zuletzt bestätigte Wert je Aktor und Art, auch vom Wiederherstellen.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        store: StateStore,
        adapters: Sequence[Adapter],
        actuators: Mapping[str, Actuator],
        ask: Callable[[str], bool],
        monotonic: Callable[[], float] = time.monotonic,
        poll_s: float = 0.5,
        cleanup: Sequence[Callable[[], Awaitable[None]]] = (),
        resend_s: float = RESEND_S,
        unattended: bool = False,
    ) -> None:
        self.cfg = cfg
        self.limits = HardLimits.from_config(cfg)
        self.adapters: tuple[Adapter, ...] = tuple(adapters)
        self.actuators: Mapping[str, Actuator] = dict(actuators)
        self.monotonic = monotonic
        self.unattended = unattended
        self.findings: list[Finding] = []
        self.failure: str | None = None
        self._store = store
        self._ask = ask
        self._poll_s = poll_s
        self._resend_s = resend_s
        self._cleanup = tuple(cleanup)
        self._tasks: list[asyncio.Task[None]] = []
        # die vorgefundenen Werte je Aktor und Art, in der Reihenfolge des ersten Schreibens
        self._originals: dict[_Key, CommandValue] = {}
        self._sent: dict[_Key, CommandValue] = {}  # zuletzt angenommener Auftrag
        self._confirmed: dict[_Key, CommandValue] = {}  # zuletzt bestätigter Wert
        self._unrestorable: set[_Key] = set()  # nicht wiederherstellbar, schon gemeldet
        self._persistent_writes = 0  # angenommene Aufträge für persistente Arten in diesem Lauf
        self._answered: datetime | None = (
            None  # Zeit der letzten Antwort, nach der Uhr des Speichers
        )

    @property
    def confirmed(self) -> Mapping[_Key, CommandValue]:
        """Der zuletzt bestätigte Wert je Aktor und Art, auch vom Wiederherstellen."""
        return MappingProxyType(self._confirmed)

    async def start(self) -> None:
        """Startet jeden Adapter als Task; die Tasks laufen bis ``stop``."""
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(adapter.run(), name=f"adapter:{adapter.name}")
                for adapter in self.adapters
            ]

    async def stop(self) -> None:
        """Bricht die Adapter ab, beendet laufende Tesla-Befehle und ruft ``cleanup`` auf.

        Ein zweiter Aufruf tut nichts mehr.
        """
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for actuator in self.actuators.values():
            if isinstance(actuator, TeslaActuator):
                await actuator.aclose()
        cleanup, self._cleanup = self._cleanup, ()
        for close in cleanup:
            await close()

    def snapshot(self) -> Snapshot:
        """Der Zustand aller Signale jetzt."""
        return self._store.snapshot()

    def ask(self, question: str) -> bool:
        """Stellt dem Betreiber die Frage; ``True`` heißt Ja.

        Die Frage liest von der Konsole, solange steht der Event-Loop mit jedem Adapter. Die Sitzung
        merkt sich darum, wann die Antwort kam, auch bei Strg+C: Werte von Tessie, die nicht neuer
        sind, gelten danach als unbekannt (``fresh_after_question``).
        """
        try:
            return self._ask(question)
        finally:
            self._answered = self.snapshot().ts

    async def fresh_after_question(self, actuator: str, timeout_s: float) -> bool:
        """Hat sich Tessie nach der letzten Rückfrage für den Tesla ``actuator`` gemeldet?

        Wartet bis ``timeout_s`` darauf, dass der Zeitstempel von ``online`` neuer ist als die
        Antwort; der Adapter erneuert ihn bei jeder Abfrage, alle 30 bis 60 s. Ohne Rückfrage und
        für jeden anderen Aktor (die Werte von EVCS und Cerbo sind lokal) ist es sofort ``True``.
        Ob das Auto wach ist, entscheiden danach wie sonst die ``capabilities``.
        """
        signal = self._cloud_signal(actuator)
        answered = self._answered
        if signal is None or answered is None:
            return True

        def newer(snap: Snapshot) -> bool:
            reading = snap.get(signal)
            return reading is not None and reading.ts > answered

        return await self.wait_for(newer, timeout_s) is not None

    def _cloud_signal(self, actuator: str) -> str | None:
        """Das Signal, dessen Zeitstempel zeigt, dass Tessie sich gemeldet hat; sonst ``None``."""
        spec = self.limits.actuators.get(actuator)
        if spec is None or spec.family != "tesla":
            return None
        return f"{actuator}.{CLOUD_SIGNAL}"

    def found_problem(self, actuator: str, kind: CommandKind, value: CommandValue) -> str | None:
        """Warum der vorgefundene Wert ``value`` nicht zum Test taugt; ``None``, wenn er passt.

        Die Sitzung schreibt nur, was die Regeln 1–3 unverändert durchlassen, auch beim
        Wiederherstellen. Ein vorgefundener Wert, den sie klemmen oder ablehnen würden (ein Strom
        über ``max_a``, unter ``min_a`` oder nicht ganzzahlig), bliebe nach dem Test verstellt. Die
        Teile fragen darum in „bereit“, vor dem ersten Schreiben, und enden mit FEHLER.
        """
        problems = _range_problems(actuator, kind, value, self.limits)
        if problems is None:
            return None
        return (
            f"Ausgangswert {_value_text(kind, value)} außerhalb der Grenzen; er ließe sich nicht "
            f"wiederherstellen: {problems}"
        )

    def record(self, finding: Finding) -> None:
        """Hält einen Befund fest (``findings``) und zeigt ihn auf der Konsole."""
        self.findings.append(finding)
        _show(finding)

    async def wait_for(
        self, predicate: Callable[[Snapshot], bool], timeout_s: float
    ) -> float | None:
        """Wartet, bis ``predicate`` für den Zustand gilt: die Sekunden bis dahin, sonst ``None``.

        Geprüft wird sofort und dann im Abstand ``poll_s``, höchstens ``timeout_s`` lang.
        """
        started = self.monotonic()
        while True:
            if predicate(self.snapshot()):
                return self.monotonic() - started
            remaining = timeout_s - (self.monotonic() - started)
            if remaining <= 0:
                return None
            await asyncio.sleep(min(self._poll_s, remaining))

    async def observe(self, duration_s: float) -> list[Snapshot]:
        """Sammelt den Zustand über ``duration_s``: einmal sofort, dann im Abstand ``poll_s``.

        Der letzte Zustand entsteht nach Ablauf der Zeit. Mit ``duration_s`` 0 ist es einer.
        """
        started = self.monotonic()
        snapshots = [self.snapshot()]
        while (remaining := duration_s - (self.monotonic() - started)) > 0:
            await asyncio.sleep(min(self._poll_s, remaining))
            snapshots.append(self.snapshot())
        return snapshots

    async def set_and_confirm(
        self, actuator: str, kind: CommandKind, value: CommandValue, timeout_s: float
    ) -> float | None:
        """Schreibt ``value`` und bleibt dran, bis der Aktor ihn bestätigt oder die Frist endet.

        Nach einer Rückfrage wartet die Sitzung bei einem Tesla zuerst bis zur Frist auf neue Werte
        von Tessie (``fresh_after_question``) und sendet sonst nichts. Dann wartet sie innerhalb der
        Frist, bis der Aktor steuerbar ist, sendet, sendet erneut, wenn der Aktor den Auftrag
        ablehnt (etwa zwischen zwei Sitzungen zum Cerbo), und sendet denselben Wert noch einmal,
        wenn ``resend_s`` lang keine Bestätigung kam (der Auftrag kann mit einer endenden Sitzung
        verloren sein). Vor jedem erneuten Senden liest sie das Ergebnis: Erneut gesendet wird nur,
        solange es offen ist und der Aktor einen neuen Auftrag annimmt; der Tesla nimmt keinen an,
        solange sein Befehl läuft. Persistente Einstellungen (Entladegrenze, Min-SOC) gehen je
        Aufruf höchstens einmal hinaus und je Lauf höchstens ``MAX_PERSISTENT_WRITES``-mal; danach
        endet der Aufruf sofort mit ``PERSISTENT_LIMIT`` als Grund. An einen Aktor, der nicht
        steuerbar ist, geht nie etwas.

        Ergebnis sind die Sekunden vom ersten Senden bis zur Bestätigung, sonst ``None``; dann
        nennt ``failure`` den Grund: ging nichts hinaus, warum (Werte nach der Rückfrage nicht
        aktuell, nicht steuerbar, abgelehnt, vorgefundener Wert unbekannt), sonst die Frist. Meldet
        der Aktor ausdrücklich einen Fehler (Tesla: Tessie lehnt ab), endet das Warten sofort mit
        „Fehler: …“.

        Ein Aktor, eine Art oder ein Wert, den die Regeln 1–3 klemmen oder ablehnen würden, ist ein
        Fehler des Werkzeugs (``ValueError``), noch vor jedem Senden. Vor dem ersten angenommenen
        Senden je Aktor und Art merkt sich die Sitzung den vorgefundenen Wert für ``restore``.
        """
        device = self._checked(actuator, kind, value)
        key = (actuator, kind)
        self.failure = None
        if self._budget_used_up(kind):
            self.failure = PERSISTENT_LIMIT
            return None
        if not await self.fresh_after_question(actuator, timeout_s):
            self.failure = STALE_AFTER_QUESTION
            return None
        started = self.monotonic()
        first_sent: float | None = None
        last_sent = started
        blocked = ""  # warum noch nichts hinausging
        while True:
            if first_sent is None:
                problem = self._send(device, key, kind, value)
                if problem is None:
                    first_sent = last_sent = self.monotonic()
                else:
                    blocked = problem
            if first_sent is not None:
                # Erst das Ergebnis lesen, dann erst erneut senden: Ein neuer Auftrag ersetzte beim
                # Tesla den fertigen Befehl, dessen Ergebnis dann verloren wäre.
                state, text = device.confirm(kind, value, self.snapshot())
                if state == Confirmation.CONFIRMED:
                    self._confirmed[key] = value
                    return self.monotonic() - first_sent
                if state == Confirmation.FAILED:
                    self.failure = f"Fehler: {text}" if text else "Fehler"
                    return None
                now = self.monotonic()
                # noch offen: denselben Wert erneut, falls der Aktor ihn annimmt (der Tesla nimmt
                # keinen an, solange sein Befehl läuft)
                if self._resend_due(kind, now - last_sent):
                    if self._send(device, key, kind, value) is None:
                        last_sent = now
            remaining = timeout_s - (self.monotonic() - started)
            if remaining <= 0:
                late = f"nicht bestätigt nach {de(timeout_s)} s"
                self.failure = blocked if first_sent is None else late
                return None
            await asyncio.sleep(min(self._poll_s, remaining))

    async def restore(self, timeout_s: float = RESTORE_S) -> list[Finding]:
        """Stellt die vorgefundenen Werte wieder her, die nicht mehr stehen; je Wert ein Befund.

        Die Reihenfolge ist die umgekehrte des ersten Schreibens. Ein Wert, der schon wieder steht,
        wird nicht noch einmal geschrieben und ergibt keine Zeile: Das Rücklesen zeigt ihn, zuletzt
        gesendet wurde er selbst, und der letzte bestätigte Schreibvorgang endete bei ihm (falls
        einer bestätigt wurde). Einem Rücklesen, das hinterherhinkt (Tessie-Cache), traut die
        Sitzung nicht mehr als ihren Schreibvorgängen: Bestätigt Tessie „aus“ und lehnt „an“ ab,
        zeigt der Cache noch den vorgefundenen Zustand, und „an“ geht trotzdem noch einmal hinaus.

        Jeder Wert hat seine eigene Frist ``timeout_s`` (15 s), mit Warten und erneutem Senden wie
        in ``set_and_confirm``. Der Befund ist OK („Wiederhergestellt …“) oder FEHLER mit dem
        Grund; eine Ausnahme wird zum Befund. Ein vorgefundener Wert, den die Regeln 1–3 nicht
        unverändert durchlassen, wird nicht geschrieben, sondern einmal gemeldet. Die Befunde
        erscheinen auch auf der Konsole.
        """
        findings: list[Finding] = []
        for key, original in reversed(list(self._originals.items())):
            if key in self._unrestorable:
                continue
            finding = await self._restore(key, original, timeout_s)
            if finding is not None:
                _show(finding)
                findings.append(finding)
        return findings

    async def restore_after_step(self, timeout_s: float = RESTORE_S) -> list[Finding]:
        """Nach einem Schritt: ``restore``, die Zeilen gleich in ``findings`` (Präzisierung 11).

        So stehen sie im Bericht hinter ihrem Schritt, und der nächste Schritt beginnt mit den
        vorgefundenen Werten.
        """
        rows = await self.restore(timeout_s)
        self.findings.extend(rows)
        return rows

    def _checked(self, actuator: str, kind: CommandKind, value: CommandValue) -> Actuator:
        """Der Aktor zu ``actuator``, wenn Art und Wert die Regeln 1–3 unverändert passieren."""
        device = self.actuators.get(actuator)
        spec = self.limits.actuators.get(actuator)
        if device is None or spec is None:
            raise ValueError(f"Kein Aktor „{actuator}“ in dieser Sitzung.")
        if kind not in spec.kinds:
            raise ValueError(f"{spec.label} kennt die Befehlsart „{kind}“ nicht.")
        problems = _range_problems(actuator, kind, value, self.limits)
        if problems is not None:
            raise ValueError(f"{spec.label}: {value!r} für „{kind}“ nicht zulässig: {problems}")
        return device

    def _budget_used_up(self, kind: CommandKind) -> bool:
        """Wäre ein Auftrag dieser Art der fünfte persistente Schreibzugriff dieses Laufs?"""
        return kind in PERSISTENT_KINDS and self._persistent_writes >= MAX_PERSISTENT_WRITES

    def _resend_due(self, kind: CommandKind, since_last_s: float) -> bool:
        """Ist es Zeit, denselben Wert erneut zu senden? Nie bei persistenten Einstellungen."""
        return kind not in PERSISTENT_KINDS and since_last_s >= self._resend_s

    def _send(
        self, device: Actuator, key: _Key, kind: CommandKind, value: CommandValue
    ) -> str | None:
        """Sendet, wenn der Aktor steuerbar und der Ausgangswert bekannt ist; sonst der Grund."""
        snap = self.snapshot()
        capabilities = device.capabilities(snap, snap.ts)
        if not capabilities.controllable:
            return _not_controllable(capabilities.reasons)
        present = device.present(snap)
        first = key not in self._originals
        if first and kind not in present:
            return f"nicht gesendet: {UNKNOWN_ORIGINAL}"
        refusal = device.send(kind, value)
        if refusal is not None:
            return f"nicht gesendet: {refusal}"
        if first:
            self._originals[key] = present[kind]
        self._sent[key] = value
        if kind in PERSISTENT_KINDS:
            self._persistent_writes += 1
        return None

    def _in_place(self, key: _Key, original: CommandValue) -> bool:
        """Steht der vorgefundene Wert wieder?

        Nur wenn das Rücklesen ihn zeigt, er zuletzt gesendet wurde und der letzte bestätigte
        Schreibvorgang bei ihm endete (oder keiner bestätigt wurde). Das Rücklesen allein genügt
        nicht, denn der Tessie-Cache hinkt hinterher. Das letzte Senden genügt auch nicht: Lehnt das
        Gerät den Befehl ab, ist er nie angekommen, und bestätigt bleibt der Wert davor.
        """
        actuator, kind = key
        present = self.actuators[actuator].present(self.snapshot())
        last = self._sent.get(key, original)
        confirmed = self._confirmed.get(key, original)
        return (
            kind in present
            and matches(kind, original, present[kind])
            and matches(kind, original, last)
            and matches(kind, original, confirmed)
        )

    async def _restore(self, key: _Key, original: CommandValue, timeout_s: float) -> Finding | None:
        """Stellt einen Wert wieder her; ``None``, wenn er schon steht."""
        actuator, kind = key
        item = _restore_item(self.limits.actuators[actuator].family, kind)
        title = f"Wiederherstellen: {_value_text(kind, original)}"
        try:
            if self._in_place(key, original):
                return None
            problems = _range_problems(actuator, kind, original, self.limits)
            if problems is not None:
                self._unrestorable.add(key)
                detail = f"Ausgangswert außerhalb der Grenzen, nicht geschrieben: {problems}"
                return Finding(item, title, "fail", detail)
            if self._budget_used_up(kind):  # der Wert bliebe verstellt: einmal melden genügt
                self._unrestorable.add(key)
                return Finding(item, title, "fail", PERSISTENT_LIMIT)
            waited = await self.set_and_confirm(actuator, kind, original, timeout_s)
        except Exception as exc:  # ein Wert, der sich nicht herstellen lässt, ist ein Befund
            return Finding(item, title, "fail", f"{type(exc).__name__}: {exc}")
        if waited is None:
            return Finding(item, title, "fail", self.failure or "nicht wiederhergestellt")
        return Finding(item, title, "ok", f"Wiederhergestellt nach {seconds(waited)} s")


def build_session(
    cfg: Config, secrets: Secrets, *, ask: Callable[[str], bool], unattended: bool = False
) -> Part2Session:
    """Die Sitzung mit den echten Adaptern und den Aktoren wie im core.

    Adapter, alle nur lesend: Victron per MQTT, je EVCS ihr Modbus-Abbild, je Wall Connector seine
    lokale API (den Ladestrom am Wall Connector braucht der Teil Tesla) und je Fahrzeug mit
    ``tessie`` die Tessie-Cloud, falls ein Token da ist.

    Aktoren wie in ``CoreRuntime.build_actuators``: Victron und jede EVCS schreiben über
    ``VictronMqttAdapter.write`` und sind steuerbar, solange der Cerbo verbunden ist; die EVCS
    findet ihre Instanz über ihren Schlüssel in ``victron.instances.evcharger``. Je Fahrzeug mit
    ``tessie`` ein Tesla; ohne Token ist Tessie für ihn nie erreichbar. Der gemeinsame HTTP-Client
    schließt mit ``stop``. ``unattended`` gilt mit ``--yes`` (``Part2Session``).
    """
    clock = SystemClock()
    store = StateStore(clock)
    http = httpx.AsyncClient()
    mqtt = cfg.victron.mqtt
    transport = partial(AiomqttTransport, mqtt, secrets.mqtt_password)
    victron = VictronMqttAdapter(cfg.victron, secrets.mqtt_password, store, transport)
    adapters: list[Adapter] = [victron]
    for name, wallbox in cfg.wallboxes.items():
        if isinstance(wallbox, EvcsConfig):
            reader = partial(PymodbusReader, wallbox.host, wallbox.port, wallbox.unit_id)
            adapters.append(EvcsAdapter(name, wallbox, store, reader))
        else:
            adapters.append(TwcAdapter(name, wallbox, store, http))
    token = secrets.tessie_token
    for key, vehicle in cfg.vehicles.items():
        if vehicle.tessie is not None and token is not None:
            adapters.append(TessieAdapter(key, vehicle, cfg.site.location, token, store, http))

    def cerbo_connected() -> bool:
        return store.connected(VICTRON_SOURCE) is True

    def tessie_connected(key: str) -> bool:
        return token is not None and store.connected(f"tessie:{key}") is True

    actuators: dict[str, Actuator] = {VICTRON: VictronActuator(victron.write, cerbo_connected)}
    evcharger = cfg.victron.instances.evcharger
    for key, wallbox in cfg.wallboxes.items():
        if isinstance(wallbox, EvcsConfig):
            instance = next((gx for gx, name in evcharger.items() if name == key), None)
            evcs = EvcsActuator(key, wallbox, instance, victron.write, cerbo_connected)
            actuators[evcs.id] = evcs
    for key, vehicle in cfg.vehicles.items():
        if vehicle.tessie is None:
            continue
        car = TeslaActuator(
            key,
            vehicle,
            token if token is not None else SecretStr(""),  # ohne Token nie erreichbar
            http,
            partial(tessie_connected, key),
            clock=clock.now,
        )
        actuators[car.id] = car
    return Part2Session(
        cfg,
        store=store,
        adapters=adapters,
        actuators=actuators,
        ask=ask,
        cleanup=[http.aclose],
        unattended=unattended,
    )
