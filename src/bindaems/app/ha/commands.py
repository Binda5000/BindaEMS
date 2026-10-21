"""Befehle aus Home Assistant (Plan 2d, Präzisierung 15).

Home Assistant hat die Rechte von „Bedienen“: Regelbetrieb, Lademodus, Ziel-SOC und Abfahrt,
Boost, Zuordnung, Gast-Modus, Akku-Unterstützung und die Automatik für Ziel und Abfahrt.
Live-Schaltung (``SetActuators``) und Vorrang (``SetPriority``) gibt es hier nicht.
Jeder Befehl wird eine ``Change`` an den ``IntentService``; was Home Assistant sendet, kann nichts
außer diesen Änderungen auslösen. Ein ungültiger oder abgelehnter Befehl ändert nichts und wird
gemeldet (``Rejections``); keine Ausnahme verlässt den Handler.
"""

from __future__ import annotations

import asyncio
import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

import structlog

from bindaems.app.audit import Source
from bindaems.app.control.intent import (
    DEPARTURE_PATTERN,
    TARGET_SOC_MAX,
    TARGET_SOC_MIN,
    Change,
    ConflictAgain,
    CoreDown,
    IntentService,
    Rejected,
    SetAssignment,
    SetBoost,
    SetControl,
    SetGuestMode,
    SetMode,
    SetSupport,
    SetTarget,
    UnknownTarget,
)
from bindaems.app.control.routes import CONFLICT, CORE_DOWN
from bindaems.app.core_link import CoreUnavailableError
from bindaems.app.ha.entities import AUTOMATIC, GUEST_MODE_LABELS, MODE_LABELS, object_id
from bindaems.app.settings.service import SettingsVersion, VersionConflictError
from bindaems.shared.config import Config
from bindaems.shared.control import SupportIntent
from bindaems.shared.numbers import de
from bindaems.shared.plan import PlanTrigger
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

PAYLOAD_MAX: Final = 64  # so viele Zeichen der Nutzlast nennt eine Ablehnung

ACTOR: Final = "home-assistant"
SOURCE: Final = "ha"
RATE_S: Final = 1.0  # höchstens ein Befehl je Sekunde und Entität
ON: Final = "ON"
OFF: Final = "OFF"
PRESS: Final = "PRESS"


class PlanningCommands(Protocol):
    """Was die Planung für Home Assistant tut: Netzladen schalten und Neuplanung anfordern."""

    def set_grid_charge(self, on: bool, *, actor: str, source: Source) -> SettingsVersion: ...

    def request(self, trigger: PlanTrigger) -> None: ...


class AutomationCommands(Protocol):
    """Was die Automatik für Home Assistant tut: den Schalter eines Autos setzen."""

    async def set_enabled(self, key: str, on: bool, *, actor: str, source: Source) -> None: ...


@dataclass(frozen=True)
class SetGridCharge:
    """Schalter „Netzladen erlaubt“."""

    on: bool


@dataclass(frozen=True)
class Replan:
    """Taste „Neu planen“."""


@dataclass(frozen=True)
class SupportSwitch:
    """Schalter „Akku-Unterstützung erlaubt“; die Schwelle bleibt, wie sie ist."""

    on: bool


@dataclass(frozen=True)
class SupportThreshold:
    """Zahl „Unterstützungsschwelle“ in %, im Bereich USV-Reserve bis 100; der Schalter bleibt."""

    soc_pct: float


@dataclass(frozen=True)
class SetAutoTarget:
    """Schalter „Automatik Ziel und Abfahrt“ eines Fahrzeugs."""

    vehicle: str
    on: bool


# was ein Befehl aus Home Assistant bewirken kann
Command = Change | SetGridCharge | Replan | SupportSwitch | SupportThreshold | SetAutoTarget


class Refused(Exception):
    """Der Befehl ist gültig geformt, aber nicht erlaubt; ``text`` ist die Meldung."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class Rejections(Protocol):
    """Meldet einen abgelehnten Befehl (mit Token der ``HaNotifier``, sonst ``LogRejections``)."""

    async def reject(self, text: str) -> None: ...


class LogRejections:
    """Ohne HA-Token: schreibt die Ablehnung nur ins Log."""

    async def reject(self, text: str) -> None:
        log.warning("Befehl aus Home Assistant abgelehnt", text=text)


def _shown(payload: str) -> str:
    """Nutzlast für eine Meldung: ohne Steuerzeichen, auf ``PAYLOAD_MAX`` Zeichen gekürzt."""
    text = "".join(char for char in payload if unicodedata.category(char)[0] != "C")
    return text if len(text) <= PAYLOAD_MAX else text[:PAYLOAD_MAX] + "…"


@dataclass(frozen=True)
class _Command:
    """Eine Entität mit Befehl: ihr Name für die Meldung und die Zuordnung der Nutzlast."""

    name: str
    # ``None``: Nutzlast unbekannt; ``Refused``: bekannt, aber nicht erlaubt
    parse: Callable[[str], Command | None]
    option: bool  # ``select`` (Option) oder Schalter und Taste (Befehl)


def _whole_number(payload: str) -> float | None:
    """Die Nutzlast als ganze Zahl (``"45"``, ``"45.0"``); sonst ``None``. ``float`` nähme auch
    ``"4_5"`` als 45 an, deshalb fällt der Unterstrich vorher weg."""
    if "_" in payload:
        return None
    try:
        number = float(payload)
    except ValueError:
        return None
    return number if math.isfinite(number) and number.is_integer() else None


def _table(cfg: Config, planning: bool, automation: bool) -> dict[str, _Command]:
    by_label = {label: mode for mode, label in MODE_LABELS.items()}
    guest_by_label = {label: mode for mode, label in GUEST_MODE_LABELS.items()}

    def mode(key: str) -> Callable[[str], Change | None]:
        return lambda payload: SetMode(key, by_label[payload]) if payload in by_label else None

    def target_soc(key: str) -> Callable[[str], Change | None]:
        """Nur das Ziel; die Abfahrt bleibt (``None``). Eine ganze Zahl von 20 bis 100."""

        def parse(payload: str) -> Change | None:
            soc = _whole_number(payload)
            if soc is None:
                return None
            if not TARGET_SOC_MIN <= soc <= TARGET_SOC_MAX:
                raise Refused(f"Ziel-SOC „{_shown(payload)}“ liegt außerhalb von 20–100 %.")
            return SetTarget(key, int(soc), None)

        return parse

    def departure(key: str) -> Callable[[str], Change | None]:
        """Nur die Abfahrt (``HH:MM``); das Ziel bleibt (``None``)."""

        def parse(payload: str) -> Change | None:
            if DEPARTURE_PATTERN.fullmatch(payload) is None:
                raise Refused(f"Abfahrt „{_shown(payload)}“ ist keine Uhrzeit (HH:MM).")
            return SetTarget(key, None, payload)

        return parse

    def assignment(wallbox: str) -> Callable[[str], Change | None]:
        vehicles = {vehicle.name: key for key, vehicle in cfg.vehicles.items()}

        def parse(payload: str) -> Change | None:
            if payload == AUTOMATIC:
                return SetAssignment(wallbox, None)
            return SetAssignment(wallbox, vehicles[payload]) if payload in vehicles else None

        return parse

    def press(change: Change) -> Callable[[str], Change | None]:
        return lambda payload: change if payload == PRESS else None

    def guest(payload: str) -> Change | None:
        return SetGuestMode(guest_by_label[payload]) if payload in guest_by_label else None

    def regulation(payload: str) -> Change | None:
        return SetControl(payload == ON) if payload in (ON, OFF) else None

    def grid_charge(payload: str) -> SetGridCharge | None:
        return SetGridCharge(payload == ON) if payload in (ON, OFF) else None

    def auto_target(key: str) -> Callable[[str], SetAutoTarget | None]:
        return lambda payload: SetAutoTarget(key, payload == ON) if payload in (ON, OFF) else None

    reserve = cfg.battery.reserve_soc_pct

    def support_switch(payload: str) -> SupportSwitch | None:
        return SupportSwitch(payload == ON) if payload in (ON, OFF) else None

    def support_threshold(payload: str) -> SupportThreshold | None:
        """Eine ganze Zahl von der USV-Reserve bis 100, wie beim Ziel-SOC."""
        soc = _whole_number(payload)
        if soc is None:
            return None
        if not reserve <= soc <= 100:
            shown, low = _shown(payload), de(reserve)
            raise Refused(f"Unterstützungsschwelle „{shown}“ liegt außerhalb von {low}–100 %.")
        return SupportThreshold(soc)

    table = {
        "regulation": _Command("Regelbetrieb", regulation, option=False),
        "guest_mode": _Command("Gast-Lademodus", guest, option=True),
        "battery_support": _Command("Akku-Unterstützung erlaubt", support_switch, option=False),
        "support_soc": _Command("Unterstützungsschwelle", support_threshold, option=False),
    }
    if planning:
        table["grid_charge"] = _Command("Netzladen erlaubt", grid_charge, option=False)
        table["replan"] = _Command(
            "Neu planen", lambda payload: Replan() if payload == PRESS else None, option=False
        )
    for key in cfg.vehicles:
        oid = object_id(key)
        table[f"{oid}_mode"] = _Command("Lademodus", mode(key), option=True)
        table[f"{oid}_target_soc"] = _Command("Ziel-SOC", target_soc(key), option=False)
        table[f"{oid}_departure"] = _Command("Abfahrtszeit", departure(key), option=False)
        table[f"{oid}_boost"] = _Command("Boost", press(SetBoost(key, True)), option=False)
        table[f"{oid}_boost_stop"] = _Command(
            "Boost beenden", press(SetBoost(key, False)), option=False
        )
        if automation:
            table[f"{oid}_auto_target"] = _Command(
                "Automatik Ziel und Abfahrt", auto_target(key), option=False
            )
    for key in cfg.wallboxes:
        table[f"{object_id(key)}_assignment"] = _Command("Zuordnung", assignment(key), option=True)
    return table


class HaCommandHandler:
    """Setzt Befehle aus Home Assistant in Änderungen der Absicht um."""

    def __init__(
        self,
        cfg: Config,
        intents: IntentService,
        rejections: Rejections,
        clock: Clock,
        planning: PlanningCommands | None = None,
        automation: AutomationCommands | None = None,
    ) -> None:
        # ohne Planung kennt der Handler „Netzladen erlaubt“ und „Neu planen“ nicht, ohne
        # Automatik den Schalter „Automatik Ziel und Abfahrt“ nicht
        self._commands = _table(cfg, planning is not None, automation is not None)
        self._intents = intents
        self._planning = planning
        self._automation = automation
        self._rejections = rejections
        self._clock = clock
        self._last: dict[str, datetime] = {}

    async def __call__(self, object_id: str, payload: str) -> None:
        command = self._commands.get(object_id)
        if command is None:
            log.debug("Befehl für unbekannte Entität", object_id=object_id)
            return
        now = self._clock.now()
        last = self._last.get(object_id)
        # eine zurückgesprungene Uhr (negativer Abstand) zählt als abgelaufen
        if last is not None and timedelta(0) <= now - last < timedelta(seconds=RATE_S):
            log.info("Befehl verworfen: zu schnell", object_id=object_id)
            return
        self._last[object_id] = now
        try:
            change = command.parse(payload)
        except Refused as exc:
            await self._reject(exc.text)
            return
        if change is None:
            unknown = "Unbekannte Option" if command.option else "Unbekannter Befehl"
            await self._reject(f"{unknown} „{_shown(payload)}“ für „{command.name}“.")
            return
        try:
            await self._execute(change)
        except VersionConflictError as exc:
            await self._reject(str(exc))
        except Rejected as exc:
            await self._reject(" ".join(exc.details))
        except UnknownTarget as exc:
            await self._reject(exc.text)
        except (CoreDown, CoreUnavailableError):
            await self._reject(CORE_DOWN)
        except ConflictAgain:
            await self._reject(CONFLICT)
        except Exception:
            log.exception("Befehl aus Home Assistant fehlgeschlagen", object_id=object_id)

    async def _execute(self, change: Command) -> None:
        planning, automation = self._planning, self._automation
        if isinstance(change, SupportSwitch | SupportThreshold):
            await self._intents.apply(await self._support(change), actor=ACTOR, source=SOURCE)
        elif isinstance(change, SetAutoTarget):
            if automation is not None:
                # speichert die Einstellung selbst (Thread) und schreibt den Eintrag ins Protokoll
                await automation.set_enabled(change.vehicle, change.on, actor=ACTOR, source=SOURCE)
        elif isinstance(change, SetGridCharge) and planning is not None:
            # blockiert (Datenbank): nicht in der Ereignisschleife
            await asyncio.to_thread(planning.set_grid_charge, change.on, actor=ACTOR, source=SOURCE)
        elif isinstance(change, Replan) and planning is not None:
            planning.request("manual")
        elif not isinstance(change, SetGridCharge | Replan):
            await self._intents.apply(change, actor=ACTOR, source=SOURCE)

    async def _support(self, change: SupportSwitch | SupportThreshold) -> SetSupport:
        """Die Änderung der Unterstützung auf Basis der gültigen Absicht des core.

        Der Schalter behält die Schwelle, die Zahl den Schalter (ohne Absicht: erlaubt, Schwelle
        aus der Konfiguration).
        """
        current = await self._intents.current()
        support = current.support if current is not None else SupportIntent()
        if isinstance(change, SupportSwitch):
            return SetSupport(change.on, support.soc_pct)
        return SetSupport(support.allowed, change.soc_pct)

    async def _reject(self, reason: str) -> None:
        text = f"Befehl aus Home Assistant abgelehnt: {reason}"
        try:
            await self._rejections.reject(text)
        except Exception:
            log.exception("Ablehnung nicht gemeldet", text=text)
