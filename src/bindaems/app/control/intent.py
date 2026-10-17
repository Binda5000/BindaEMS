"""Absicht aus der Bedienung (Spec 5.3, Plan 2d Präzisierungen 2 und 5).

Jede Bedienung ist eine Änderung an der gültigen Absicht. ``derive`` leitet sie rein ab: Basis ist
die gültige Absicht des core (ohne sie ``Intent(version=1)``), davon werden ein abgelaufener Boost
und eine beendete Übersteuerung entfernt, dann kommt die Änderung, zuletzt die Version. Der
``IntentService`` holt die Absicht, sendet die abgeleitete und führt Änderungen nacheinander aus.
Den Plan (``SetPlan``, Plan 3c) setzt die Planung; er kommt nicht ins Änderungsprotokoll.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import structlog

from bindaems.app.audit import AuditLog, Source
from bindaems.app.core_link import CoreClient, CoreReply, CoreUnavailableError, LiveFeed, LiveState
from bindaems.shared.config import Config
from bindaems.shared.control import (
    ActuatorMode,
    ChargeMode,
    Intent,
    SurplusPriority,
    VehicleIntent,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.plan import IntentPlan
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

BOOST_DURATION: Final = timedelta(hours=12)  # der core klemmt ohnehin auf 12 h
ASSIGNMENT_OVERRIDE: Final = "override"  # ``vehicles[].assignment`` in Health


@dataclass(frozen=True)
class SetControl:
    """Regelbetrieb an oder aus."""

    on: bool


@dataclass(frozen=True)
class SetMode:
    """Lademodus eines Fahrzeugs."""

    vehicle: str
    mode: ChargeMode


@dataclass(frozen=True)
class SetBoost:
    """Boost eines Fahrzeugs an (jetzt + 12 h) oder aus."""

    vehicle: str
    on: bool


@dataclass(frozen=True)
class SetAssignment:
    """Zuordnung an einer Wallbox: ein Fahrzeug übersteuert, ``None`` ist „Automatisch“."""

    wallbox: str
    vehicle: str | None


@dataclass(frozen=True)
class SetGuestMode:
    """Lademodus eines Gastes."""

    mode: ChargeMode


@dataclass(frozen=True)
class SetPriority:
    """Reihenfolge der Fahrzeuge und Vorrang beim Überschuss."""

    priority: list[str]
    surplus: SurplusPriority


@dataclass(frozen=True)
class SetActuators:
    """Betriebsart der genannten Aktoren; andere Aktoren bleiben unverändert.

    ``authorized_live`` sind die Aktoren, für die die Route Passwort und Bestätigung geprüft hat.
    Nur sie dürfen auf „live“ wechseln; ein Aktor, der in der Basis schon live ist, bleibt es ohne.
    """

    modes: dict[str, ActuatorMode]
    authorized_live: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SetPlan:
    """Bindender Teil des Akkuplans (Plan 3c, Entscheidung 1); ``None`` entfernt den Plan.

    Ohne Prüfung gegen die Konfiguration, ohne Freigabe und ohne Eintrag im Änderungsprotokoll:
    Der Nachweis ist das Plan-Archiv der app.
    """

    plan: IntentPlan | None


Change = (
    SetControl
    | SetMode
    | SetBoost
    | SetAssignment
    | SetGuestMode
    | SetPriority
    | SetActuators
    | SetPlan
)


def boost_until(now: datetime) -> datetime:
    """Ende eines Boosts, der jetzt beginnt."""
    return now + BOOST_DURATION


def _health_vehicles(health: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    raw = health.get("vehicles") if health is not None else None
    if not isinstance(raw, list):
        return {}
    return {item["id"]: item for item in raw if isinstance(item, dict) and "id" in item}


def _trusted(current: Intent, health: Mapping[str, Any] | None) -> bool:
    """Ob Health den Stand der Regelung mit der gültigen Absicht oder einer neueren zeigt.

    Health kommt aus dem letzten Regelzyklus und trägt die Version der Absicht, mit der dieser
    regelte (``intent_version``). Ist sie älter als die gültige Absicht, kennt Health Boost und
    Übersteuerung der letzten Bedienung noch nicht; ohne Version (Regelbetrieb aus, Health fehlt)
    ist der Stand unbekannt.
    """
    version = health.get("intent_version") if health is not None else None
    return isinstance(version, int) and version >= current.version


def _clean(
    key: str, vehicle: VehicleIntent, shown: Mapping[str, Any] | None, now: datetime
) -> VehicleIntent:
    """Entfernt Boost und Übersteuerung, die der core nicht mehr führt (Präzisierung 2).

    ``shown`` ist der Eintrag des Fahrzeugs in einem vertrauenswürdigen Health (``_trusted``),
    sonst ``None``. Ohne Eintrag oder mit ``mode`` null ist der Stand der Regelung unbekannt: Ein
    Boost in der Zukunft und die Übersteuerung bleiben erhalten. Mit Stand bleibt der Boost nur,
    wenn Health einen zeigt oder er auf seinen Ansteckvorgang wartet (``boost_pending``), die
    Übersteuerung nur an genau dieser Wallbox oder wartend (``override_pending``). Ein Boost in
    der Vergangenheit entfällt immer.
    """
    boost, wallbox = vehicle.boost_until, vehicle.wallbox
    if boost is not None and boost <= now:
        boost = None
    if shown is not None and shown.get("mode") is not None:
        if shown.get("boost_until") is None and not shown.get("boost_pending"):
            boost = None
        active = shown.get("assignment") == ASSIGNMENT_OVERRIDE and shown.get("wallbox") == wallbox
        if not (active or shown.get("override_pending")):
            wallbox = None
    if boost == vehicle.boost_until and wallbox == vehicle.wallbox:
        return vehicle
    if boost != vehicle.boost_until:
        log.info("Boost nicht zurückgeschickt", vehicle=key, boost_until=str(vehicle.boost_until))
    if wallbox != vehicle.wallbox:
        log.info("Übersteuerung nicht zurückgeschickt", vehicle=key, wallbox=vehicle.wallbox)
    return vehicle.model_copy(update={"boost_until": boost, "wallbox": wallbox})


def _base(current: Intent | None, health: Mapping[str, Any] | None, now: datetime) -> Intent:
    """Die gültige Absicht ohne abgelaufenen Boost und beendete Übersteuerung."""
    if current is None:
        return Intent(version=1)
    shown = _health_vehicles(health) if _trusted(current, health) else {}
    vehicles = {
        key: _clean(key, item, shown.get(key), now) for key, item in current.vehicles.items()
    }
    return current.model_copy(update={"vehicles": vehicles})


def _apply(change: Change, base: Intent, now: datetime) -> Intent:
    vehicles = dict(base.vehicles)

    def edit(key: str, **fields: Any) -> None:
        vehicles[key] = vehicles.get(key, VehicleIntent()).model_copy(update=fields)

    match change:
        case SetControl(on=on):
            return base.model_copy(update={"control": on})
        case SetMode(vehicle=key, mode=mode):
            edit(key, mode=mode)
        case SetBoost(vehicle=key, on=on):
            edit(key, boost_until=boost_until(now) if on else None)
        case SetAssignment(wallbox=wallbox, vehicle=key):
            for other, item in base.vehicles.items():
                if item.wallbox == wallbox:
                    edit(other, wallbox=None)
            if key is not None:
                edit(key, wallbox=wallbox)
        case SetGuestMode(mode=mode):
            return base.model_copy(update={"guest": base.guest.model_copy(update={"mode": mode})})
        case SetPriority(priority=priority, surplus=surplus):
            return base.model_copy(update={"priority": list(priority), "surplus_priority": surplus})
        case SetActuators(modes=modes):
            return base.model_copy(update={"actuators": {**base.actuators, **modes}})
        case SetPlan(plan=plan):
            return base.model_copy(update={"plan": plan})
    return base.model_copy(update={"vehicles": vehicles})


def derive(
    current: Intent | None,
    health: Mapping[str, Any] | None,
    change: Change,
    now: datetime,
) -> Intent:
    """Die neue Absicht aus der gültigen, dem Health-Stand und der Änderung (rein).

    Die Version ist ``max(Millisekunden seit 1970 nach ``now``, gültige Version + 1)``.
    """
    return _derive_from(current, _base(current, health, now), change, now)


def _derive_from(current: Intent | None, base: Intent, change: Change, now: datetime) -> Intent:
    """Wie ``derive``, aber auf der schon bereinigten Basis ``base``."""
    derived = _apply(change, base, now)
    version = max(epoch_ms(now), (current.version + 1) if current is not None else 1)
    return derived.model_copy(update={"version": version})


@dataclass(frozen=True)
class ChangeResult:
    """Ergebnis einer angenommenen Änderung; ``intent`` ist die gesendete Absicht."""

    result: Literal["accepted", "clamped"]
    version: int
    details: list[str]
    intent: Intent


class CoreDown(Exception):
    """Der core ist nicht erreichbar."""


class ConflictAgain(Exception):
    """Die Absicht wurde auch beim zweiten Versuch gleichzeitig geändert."""


class Rejected(Exception):
    """Der core lehnt die Absicht ab; ``details`` sind seine Sätze."""

    def __init__(self, details: list[str]) -> None:
        super().__init__(" ".join(details))
        self.details = details


class LiveNotAuthorized(Exception):
    """Ein Aktor ginge auf „live“, ohne dass Passwort und Bestätigung für ihn geprüft sind.

    Das passiert, wenn ihn nach der Prüfung der Route jemand auf Dry-Run gestellt hat (auch beim
    zweiten Versuch nach einem 409); ``actuators`` sind diese Aktoren.
    """

    def __init__(self, actuators: list[str]) -> None:
        super().__init__(", ".join(actuators))
        self.actuators = actuators


class UnknownTarget(Exception):
    """Fahrzeug oder Wallbox gibt es in der Konfiguration nicht; ``text`` ist die 404-Meldung."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _vehicle(intent: Intent, key: str) -> VehicleIntent:
    return intent.vehicles.get(key, VehicleIntent())


def _audit(change: Change, base: Intent, new: Intent) -> tuple[str, str, Any, Any] | None:
    """Aktion, Ziel sowie alter und neuer Wert für das Änderungsprotokoll (Texte); ``None`` für
    eine Änderung ohne Eintrag (``SetPlan``)."""
    match change:
        case SetControl():
            return "intent.control", "regelbetrieb", base.control, new.control
        case SetMode(vehicle=key):
            return "intent.mode", key, _vehicle(base, key).mode.value, _vehicle(new, key).mode.value
        case SetBoost(vehicle=key):
            return (
                "intent.boost",
                key,
                _iso(_vehicle(base, key).boost_until),
                _iso(_vehicle(new, key).boost_until),
            )
        case SetAssignment(wallbox=wallbox, vehicle=key):
            before = next((k for k, v in base.vehicles.items() if v.wallbox == wallbox), None)
            return "intent.assignment", wallbox, before, key
        case SetGuestMode():
            return "intent.guest_mode", "gast", base.guest.mode.value, new.guest.mode.value
        case SetPriority():
            return (
                "intent.priority",
                "vorrang",
                {
                    "priority": base.priority,
                    "surplus": base.surplus_priority.model_dump(mode="json"),
                },
                {"priority": new.priority, "surplus": new.surplus_priority.model_dump(mode="json")},
            )
        case SetActuators(modes=modes):
            return (
                "intent.actuators",
                ",".join(sorted(modes)),
                {key: _mode(base, key) for key in sorted(modes)},
                {key: _mode(new, key) for key in sorted(modes)},
            )
        case SetPlan():
            return None


def _mode(intent: Intent, actuator: str) -> str:
    return intent.actuators.get(actuator, ActuatorMode.DRY_RUN).value


def _gate(change: Change, base: Intent) -> None:
    """Live nur mit Prüfung: gegen die Basis genau dieses Versuchs (Spec 8.7, Präzisierung 4)."""
    if not isinstance(change, SetActuators):
        return
    unchecked = sorted(
        actuator
        for actuator, mode in change.modes.items()
        if mode is ActuatorMode.LIVE
        and base.actuators.get(actuator) is not ActuatorMode.LIVE
        and actuator not in change.authorized_live
    )
    if unchecked:
        raise LiveNotAuthorized(unchecked)


class IntentService:
    """Leitet Bedienungen ab, sendet sie an den core und protokolliert sie (den Plan nicht).

    Änderungen laufen nacheinander (``asyncio.Lock``). Antwortet der core mit 409, weil die Absicht
    inzwischen anders lautet (etwa per ``curl``), holt der Dienst sie neu und wendet die Änderung
    genau einmal neu an.
    """

    def __init__(
        self,
        client: CoreClient,
        live: LiveState,
        feed: LiveFeed,
        audit: AuditLog,
        clock: Clock,
        cfg: Config,
    ) -> None:
        self._client = client
        self._live = live
        self._feed = feed
        self._audit = audit
        self._clock = clock
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._cached: Intent | None = None

    def cached(self) -> Intent | None:
        """Die zuletzt gesehene Absicht (für Home Assistant); ``None`` bis zur ersten."""
        return self._cached

    async def current(self) -> Intent | None:
        """Die gültige Absicht vom core."""
        try:
            intent = await self._client.intent()
        except CoreUnavailableError as exc:
            raise CoreDown from exc
        self._cached = intent
        return intent

    def _check(self, change: Change) -> None:
        """Prüft Fahrzeuge und Wallbox der Änderung gegen ``config.yaml``."""
        keys: list[str] = []
        match change:
            case SetMode(vehicle=key) | SetBoost(vehicle=key):
                keys = [key]
            case SetAssignment(wallbox=wallbox, vehicle=key):
                if wallbox not in self._cfg.wallboxes:
                    raise UnknownTarget(f"Unbekannte Wallbox „{wallbox}“.")
                keys = [key] if key is not None else []
            case SetPriority(priority=priority):
                keys = priority
        for key in keys:
            if key not in self._cfg.vehicles:
                raise UnknownTarget(f"Unbekanntes Fahrzeug „{key}“.")

    async def apply(self, change: Change, *, actor: str, source: Source) -> ChangeResult:
        """Wendet die Änderung auf die gültige Absicht an und sendet sie.

        Fehler: ``UnknownTarget`` (Fahrzeug oder Wallbox unbekannt), ``CoreDown``,
        ``Rejected`` (422 des core), ``ConflictAgain`` (zweites 409) und ``LiveNotAuthorized``
        (ein Aktor ginge ohne Prüfung live; die Absicht bleibt unverändert).
        """
        self._check(change)
        async with self._lock:
            try:
                for attempt in (1, 2):
                    current = await self._client.intent()
                    self._cached = current
                    now = self._clock.now()
                    base = _base(current, self._live.health, now)
                    _gate(change, base)
                    new = _derive_from(current, base, change, now)
                    reply = await self._client.submit_intent(new)
                    if reply.status != 409:
                        break
                    log.info("Absicht gleichzeitig geändert", attempt=attempt)
                else:
                    raise ConflictAgain
            except CoreUnavailableError as exc:
                raise CoreDown from exc
            result = self._finish(change, actor, source, base, new, reply)
        if result.result == "clamped":  # der core hat die Absicht geändert: seine zeigt der Cache
            with contextlib.suppress(CoreDown):
                await self.current()
        return result

    def _finish(
        self,
        change: Change,
        actor: str,
        source: Source,
        base: Intent,
        new: Intent,
        reply: CoreReply,
    ) -> ChangeResult:
        details = [str(item) for item in reply.body.get("details") or []]
        result = reply.body.get("result")
        if reply.status != 200 or result not in ("accepted", "clamped"):
            raise Rejected(details)
        version = reply.body.get("version")
        version = version if isinstance(version, int) else new.version
        entry = _audit(change, base, new)
        if entry is not None:
            action, target, before, after = entry
            self._audit.record(
                actor,
                source,
                action,
                target,
                {"from": before, "to": after, "version": version, "result": result},
            )
        self._cached = new
        self._feed.refresh_health()
        return ChangeResult(result, version, details, new)
