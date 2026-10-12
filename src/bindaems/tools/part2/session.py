"""Sitzung des Prüfprotokolls Teil 2 (Spec 17.2): dieselben Adapter und Aktoren wie im core.

Das Werkzeug ``bindaems.tools.verify_part2`` schreibt selbst auf die Geräte, mit Rückfrage beim
Betreiber (Plan 2b, Präzisierung 11). Die Sitzung startet dafür die echten Adapter und schreibt
über die Aktoren des core (``build_session``). Für jeden Wert gilt:

- Vorher prüfen ihn die Regeln 1–3 der Safety-Schicht (``check_range``). Würden sie ihn klemmen
  oder ablehnen, ist das ein Fehler des Werkzeugs (``ValueError``); gesendet wird dann nichts.
- Gesendet wird nur an einen Aktor, der in diesem Moment steuerbar ist (``capabilities``). Einen
  schlafenden Tesla weckt die Sitzung so nie.
- Vor dem ersten Schreiben je Aktor und Art merkt sie sich den vorgefundenen Wert (``present``).
  Ist er unbekannt, schreibt sie nicht: Er ließe sich nicht wiederherstellen.
- ``restore`` stellt am Ende alle gemerkten Werte in umgekehrter Reihenfolge wieder her, auch nach
  Strg+C. Auch dabei schreibt sie nur Werte, die die Regeln 1–3 unverändert durchlassen.

Die Teile halten jeden Befund mit ``record`` fest, sobald er feststeht. So nennt der Bericht nach
einem Abbruch die Schritte bis dahin.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
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
from bindaems.core.control.actuator import Actuator, Confirmation
from bindaems.core.safety.limits import Family, HardLimits
from bindaems.core.safety.ranges import check_range
from bindaems.core.state.store import StateStore
from bindaems.shared.config import Config, EvcsConfig, Secrets
from bindaems.shared.control import VICTRON, Command, CommandKind, CommandValue
from bindaems.shared.domain import Snapshot
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.timeutil import SystemClock
from bindaems.tools.verify_checks import Finding

RESTORE_S: Final = 15.0  # Frist je wiederherzustellendem Wert, vom Senden bis zur Bestätigung
UNKNOWN_ORIGINAL: Final = "Ausgangswert unbekannt; er ließe sich nicht wiederherstellen."

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

    ``findings`` sind die Befunde, die die Teile mit ``record`` festhalten. ``failure`` nennt,
    warum der letzte Aufruf von ``set_and_confirm`` keinen bestätigten Wert ergab.
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
    ) -> None:
        self.cfg = cfg
        self.limits = HardLimits.from_config(cfg)
        self.adapters: tuple[Adapter, ...] = tuple(adapters)
        self.actuators: Mapping[str, Actuator] = dict(actuators)
        self.monotonic = monotonic
        self.findings: list[Finding] = []
        self.failure: str | None = None
        self._store = store
        self._ask = ask
        self._poll_s = poll_s
        self._cleanup = tuple(cleanup)
        self._tasks: list[asyncio.Task[None]] = []
        # die vorgefundenen Werte je Aktor und Art, in der Reihenfolge des ersten Schreibens
        self._originals: dict[_Key, CommandValue] = {}

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
        """Stellt dem Betreiber die Frage; ``True`` heißt Ja."""
        return self._ask(question)

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

    async def set_and_confirm(
        self, actuator: str, kind: CommandKind, value: CommandValue, timeout_s: float
    ) -> float | None:
        """Schreibt ``value`` über den Aktor und wartet höchstens ``timeout_s`` auf die Bestätigung.

        Ergebnis sind die Sekunden vom Senden bis zur Bestätigung, sonst ``None``; dann nennt
        ``failure`` den Grund: nicht steuerbar, nicht gesendet (der Aktor lehnt ab, oder der
        vorgefundene Wert ist unbekannt), ein Fehler beim Bestätigen oder die Frist.

        Ein Aktor, eine Art oder ein Wert, den die Regeln 1–3 klemmen oder ablehnen würden, ist ein
        Fehler des Werkzeugs (``ValueError``), noch vor jedem Senden. Vor dem ersten erfolgreichen
        Senden je Aktor und Art merkt sich die Sitzung den vorgefundenen Wert für ``restore``.
        """
        device = self._checked(actuator, kind, value)
        self.failure = None
        snap = self.snapshot()
        capabilities = device.capabilities(snap, snap.ts)
        if not capabilities.controllable:
            self.failure = _not_controllable(capabilities.reasons)
            return None
        key = (actuator, kind)
        present = device.present(snap)
        first = key not in self._originals
        if first and kind not in present:
            self.failure = f"nicht gesendet: {UNKNOWN_ORIGINAL}"
            return None
        started = self.monotonic()
        refusal = device.send(kind, value)
        if refusal is not None:
            self.failure = f"nicht gesendet: {refusal}"
            return None
        if first:
            self._originals[key] = present[kind]
        return await self._confirmation(device, kind, value, started, timeout_s)

    async def restore(self, timeout_s: float = RESTORE_S) -> list[Finding]:
        """Stellt die vorgefundenen Werte in umgekehrter Reihenfolge wieder her; je Wert ein Befund.

        Jeder Wert hat seine eigene Frist ``timeout_s`` (15 s). Der Befund ist OK
        („Wiederhergestellt …“) oder FEHLER mit dem Grund; eine Ausnahme wird zum Befund. Ein
        vorgefundener Wert, den die Regeln 1–3 nicht unverändert durchlassen, wird nicht
        geschrieben, sondern gemeldet. Die Befunde erscheinen auch auf der Konsole.
        """
        findings: list[Finding] = []
        for (actuator, kind), original in reversed(list(self._originals.items())):
            finding = await self._restore(actuator, kind, original, timeout_s)
            _show(finding)
            findings.append(finding)
        return findings

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

    async def _confirmation(
        self,
        device: Actuator,
        kind: CommandKind,
        value: CommandValue,
        started: float,
        timeout_s: float,
    ) -> float | None:
        """Fragt den Aktor alle ``poll_s``, bis er bestätigt, scheitert oder die Frist endet."""
        while True:
            state, text = device.confirm(kind, value, self.snapshot())
            elapsed = self.monotonic() - started
            if state == Confirmation.CONFIRMED:
                return elapsed
            if state == Confirmation.FAILED:
                self.failure = f"Fehler: {text}" if text else "Fehler"
                return None
            if elapsed >= timeout_s:
                self.failure = f"nicht bestätigt nach {de(timeout_s)} s"
                return None
            await asyncio.sleep(min(self._poll_s, timeout_s - elapsed))

    async def _restore(
        self, actuator: str, kind: CommandKind, original: CommandValue, timeout_s: float
    ) -> Finding:
        item = _restore_item(self.limits.actuators[actuator].family, kind)
        title = f"Wiederherstellen: {_value_text(kind, original)}"
        try:
            problems = _range_problems(actuator, kind, original, self.limits)
            if problems is not None:
                detail = f"Ausgangswert außerhalb der Grenzen, nicht geschrieben: {problems}"
                return Finding(item, title, "fail", detail)
            waited = await self.set_and_confirm(actuator, kind, original, timeout_s)
        except Exception as exc:  # ein Wert, der sich nicht herstellen lässt, ist ein Befund
            return Finding(item, title, "fail", f"{type(exc).__name__}: {exc}")
        if waited is None:
            return Finding(item, title, "fail", self.failure or "nicht wiederhergestellt")
        return Finding(item, title, "ok", f"Wiederhergestellt nach {seconds(waited)} s")


def build_session(cfg: Config, secrets: Secrets, *, ask: Callable[[str], bool]) -> Part2Session:
    """Die Sitzung mit den echten Adaptern und den Aktoren wie im core.

    Adapter, alle nur lesend: Victron per MQTT, je EVCS ihr Modbus-Abbild, je Wall Connector seine
    lokale API (den Ladestrom am Wall Connector braucht der Teil Tesla) und je Fahrzeug mit
    ``tessie`` die Tessie-Cloud, falls ein Token da ist.

    Aktoren wie in ``CoreRuntime.build_actuators``: Victron und jede EVCS schreiben über
    ``VictronMqttAdapter.write`` und sind steuerbar, solange der Cerbo verbunden ist; die EVCS
    findet ihre Instanz über ihren Schlüssel in ``victron.instances.evcharger``. Je Fahrzeug mit
    ``tessie`` ein Tesla; ohne Token ist Tessie für ihn nie erreichbar. Der gemeinsame HTTP-Client
    schließt mit ``stop``.
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
        cfg, store=store, adapters=adapters, actuators=actuators, ask=ask, cleanup=[http.aclose]
    )
