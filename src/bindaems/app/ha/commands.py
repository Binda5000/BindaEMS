"""Befehle aus Home Assistant (Plan 2d, Präzisierung 15).

Home Assistant hat die Rechte von „Bedienen“: Regelbetrieb, Lademodus, Boost, Zuordnung und
Gast-Modus. Live-Schaltung (``SetActuators``) und Vorrang (``SetPriority``) gibt es hier nicht.
Jeder Befehl wird eine ``Change`` an den ``IntentService``; was Home Assistant sendet, kann nichts
außer diesen Änderungen auslösen. Ein ungültiger oder abgelehnter Befehl ändert nichts und wird
gemeldet (``Rejections``); keine Ausnahme verlässt den Handler.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol

import structlog

from bindaems.app.control.intent import (
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
    UnknownTarget,
)
from bindaems.app.control.routes import CONFLICT, CORE_DOWN
from bindaems.app.core_link import CoreUnavailableError
from bindaems.app.ha.entities import AUTOMATIC, MODE_LABELS, object_id
from bindaems.shared.config import Config
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

PAYLOAD_MAX: Final = 64  # so viele Zeichen der Nutzlast nennt eine Ablehnung

ACTOR: Final = "home-assistant"
SOURCE: Final = "ha"
RATE_S: Final = 1.0  # höchstens ein Befehl je Sekunde und Entität
ON: Final = "ON"
OFF: Final = "OFF"
PRESS: Final = "PRESS"


class Rejections(Protocol):
    """Meldet einen abgelehnten Befehl (ab Task 17 der ``HaNotifier``)."""

    async def reject(self, text: str) -> None: ...


class LogRejections:
    """Vorläufiger Ersatz: schreibt die Ablehnung ins Log."""

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
    parse: Callable[[str], Change | None]  # ``None``: Nutzlast unbekannt
    option: bool  # ``select`` (Option) oder Schalter und Taste (Befehl)


def _table(cfg: Config) -> dict[str, _Command]:
    by_label = {label: mode for mode, label in MODE_LABELS.items()}

    def mode(key: str) -> Callable[[str], Change | None]:
        return lambda payload: SetMode(key, by_label[payload]) if payload in by_label else None

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
        return SetGuestMode(by_label[payload]) if payload in by_label else None

    def regulation(payload: str) -> Change | None:
        return SetControl(payload == ON) if payload in (ON, OFF) else None

    table = {
        "regulation": _Command("Regelbetrieb", regulation, option=False),
        "guest_mode": _Command("Gast-Lademodus", guest, option=True),
    }
    for key in cfg.vehicles:
        oid = object_id(key)
        table[f"{oid}_mode"] = _Command("Lademodus", mode(key), option=True)
        table[f"{oid}_boost"] = _Command("Boost", press(SetBoost(key, True)), option=False)
        table[f"{oid}_boost_stop"] = _Command(
            "Boost beenden", press(SetBoost(key, False)), option=False
        )
    for key in cfg.wallboxes:
        table[f"{object_id(key)}_assignment"] = _Command("Zuordnung", assignment(key), option=True)
    return table


class HaCommandHandler:
    """Setzt Befehle aus Home Assistant in Änderungen der Absicht um."""

    def __init__(
        self, cfg: Config, intents: IntentService, rejections: Rejections, clock: Clock
    ) -> None:
        self._commands = _table(cfg)
        self._intents = intents
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
        change = command.parse(payload)
        if change is None:
            unknown = "Unbekannte Option" if command.option else "Unbekannter Befehl"
            await self._reject(f"{unknown} „{_shown(payload)}“ für „{command.name}“.")
            return
        try:
            await self._intents.apply(change, actor=ACTOR, source=SOURCE)
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

    async def _reject(self, reason: str) -> None:
        text = f"Befehl aus Home Assistant abgelehnt: {reason}"
        try:
            await self._rejections.reject(text)
        except Exception:
            log.exception("Ablehnung nicht gemeldet", text=text)
