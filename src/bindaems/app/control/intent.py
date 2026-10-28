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
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

import structlog

from bindaems.app.audit import AuditLog, Source
from bindaems.app.core_link import CoreClient, CoreReply, CoreUnavailableError, LiveFeed, LiveState
from bindaems.shared.config import Config, EvcsConfig
from bindaems.shared.control import (
    TARGET_SOC_MAX,
    TARGET_SOC_MIN,
    ActuatorMode,
    ChargeMode,
    Intent,
    SupportIntent,
    SurplusPriority,
    VehicleIntent,
    next_departure,
)
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.numbers import de
from bindaems.shared.plan import IntentPlan
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

BOOST_DURATION: Final = timedelta(hours=12)  # der core klemmt ohnehin auf 12 h
ASSIGNMENT_OVERRIDE: Final = "override"  # ``vehicles[].assignment`` in Health
SUPPORT_TARGET: Final = "Akku-Unterstützung"  # Ziel im Änderungsprotokoll
DEPARTURE_PATTERN: Final = re.compile(r"([01]\d|2[0-3]):[0-5]\d")  # wie ``VehicleIntent.departure``
DEPARTURE_DATE_NEEDS_TIME: Final = "Ein Abfahrtstag verlangt eine Abfahrtszeit."
TARGET_NEEDS_A_FIELD: Final = "Ziel oder Abfahrt angeben."
NO_GUEST_LIMIT: Final = "Ohne EVCS gibt es kein Limit für Gäste."
WEEKDAYS: Final = (
    "Mo",
    "Di",
    "Mi",
    "Do",
    "Fr",
    "Sa",
    "So",
)  # ``date.weekday()``, Änderungsprotokoll


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
class SetTarget:
    """Ziel-SOC und Abfahrtszeit (``HH:MM``) eines Fahrzeugs für „Geplant“, optional an einem Tag.

    ``target_soc_pct`` und ``departure`` mit ``None`` bleiben unverändert. Gültig sind 20 bis 100 %
    und eine Uhrzeit von 00:00 bis 23:59 (``IntentService._check``). Ist ``departure`` gesetzt,
    wird auch ``departure_on`` übernommen (``None`` löscht den Tag, die Abfahrt gilt dann täglich);
    ohne ``departure`` bleibt der Tag stehen, und ``departure_on`` allein lehnt die app ab.
    """

    vehicle: str
    target_soc_pct: int | None
    departure: str | None
    departure_on: date | None = None


@dataclass(frozen=True)
class SetNowLimit:
    """Höchststrom eines Fahrzeugs in „Sofort“ in ganzen A je Phase; ``None`` ist volle Leistung.

    Gültig ist ``min_a`` bis ``max_a`` des Fahrzeugs (``IntentService._check``); der Höchststrom
    selbst wird als ``None`` gespeichert (Entscheidung 7, ``IntentService._normalised``).
    """

    vehicle: str
    max_a: int | None


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
class SetGuestNowLimit:
    """Höchststrom der Gäste in „Sofort“ in ganzen A je Phase; ``None`` ist volle Leistung.

    Gültig ist der Strombereich der EVCS (``guest_range``); ohne EVCS nur ``None``.
    """

    max_a: int | None


@dataclass(frozen=True)
class SetPriority:
    """Reihenfolge der Fahrzeuge und Vorrang beim Überschuss."""

    priority: list[str]
    surplus: SurplusPriority


@dataclass(frozen=True)
class SetSupport:
    """Akku-Unterstützung für Autos: erlaubt oder gesperrt und ihre Schwelle in % SOC.

    ``soc_pct`` ``None`` heißt „``control.support_soc_pct``“. Gültig sind USV-Reserve bis 100 %
    (``IntentService._check``).
    """

    allowed: bool
    soc_pct: float | None


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
    | SetTarget
    | SetNowLimit
    | SetBoost
    | SetAssignment
    | SetGuestMode
    | SetGuestNowLimit
    | SetPriority
    | SetSupport
    | SetActuators
    | SetPlan
)


def guest_range(cfg: Config) -> tuple[int, int] | None:
    """Strombereich der Gäste: kleinstes ``min_a`` bis größtes ``max_a`` aller EVCS; ``None``
    ohne EVCS (am Wall Connector hat ein Gast keinen Aktor)."""
    evcs = [box for box in cfg.wallboxes.values() if isinstance(box, EvcsConfig)]
    if not evcs:
        return None
    return min(box.min_a for box in evcs), max(box.max_a for box in evcs)


def boost_until(now: datetime) -> datetime:
    """Ende eines Boosts, der jetzt beginnt."""
    return now + BOOST_DURATION


def health_vehicles(health: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    raw = health.get("vehicles") if health is not None else None
    if not isinstance(raw, list):
        return {}
    return {item["id"]: item for item in raw if isinstance(item, dict) and "id" in item}


def trusted(current: int, health: Mapping[str, Any] | None) -> bool:
    """Ob Health den Stand der Regelung mit der gültigen Absicht (Version ``current``) oder einer
    neueren zeigt.

    Health kommt aus dem letzten Regelzyklus und trägt die Version der Absicht, mit der dieser
    regelte (``intent_version``). Ist sie älter als die gültige Absicht, kennt Health Boost und
    Übersteuerung der letzten Bedienung noch nicht; ohne Version (Regelbetrieb aus, Health fehlt)
    ist der Stand unbekannt.
    """
    version = health.get("intent_version") if health is not None else None
    return isinstance(version, int) and version >= current


def health_trusted(health: Mapping[str, Any] | None) -> bool:
    """``trusted`` gegen die gültige Absicht, die Health selbst nennt (``intent.version``).

    Falsch ohne Health, ohne Absicht des core und solange er nicht mit ihr regelt (Neustart,
    OBSERVE, SAFE, Regelbetrieb aus). Der ``PlanSender`` sendet nur dann (Plan 3c, Gesamtreview
    I1): Jede neue Version bindet Boost und Übersteuerung im core neu und gilt als nach seinem
    Start angenommen; auf einer Basis ohne Stand behielte sie einen wiederhergestellten oder
    verbrauchten Boost, der dann beim nächsten Anstecken anspränge.
    """
    intent = health.get("intent") if health is not None else None
    version = intent.get("version") if isinstance(intent, dict) else None
    return isinstance(version, int) and trusted(version, health)


def _clean(
    key: str, vehicle: VehicleIntent, shown: Mapping[str, Any] | None, now: datetime
) -> VehicleIntent:
    """Entfernt Boost und Übersteuerung, die der core nicht mehr führt (Präzisierung 2).

    ``shown`` ist der Eintrag des Fahrzeugs in einem vertrauenswürdigen Health (``trusted``),
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
    shown = health_vehicles(health) if trusted(current.version, health) else {}
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
        case SetTarget(vehicle=key, target_soc_pct=soc, departure=departure, departure_on=day):
            if soc is not None:
                edit(key, target_soc_pct=soc)
            if departure is not None:
                edit(key, departure=departure, departure_on=day)
        case SetNowLimit(vehicle=key, max_a=max_a):
            edit(key, now_max_a=max_a)
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
        case SetGuestNowLimit(max_a=max_a):
            guest = base.guest.model_copy(update={"now_max_a": max_a})
            return base.model_copy(update={"guest": guest})
        case SetPriority(priority=priority, surplus=surplus):
            return base.model_copy(update={"priority": list(priority), "surplus_priority": surplus})
        case SetSupport(allowed=allowed, soc_pct=soc_pct):
            support = SupportIntent(allowed=allowed, soc_pct=soc_pct)
            return base.model_copy(update={"support": support})
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


class PreconditionFailed(Exception):
    """Die Vorbedingung der Änderung galt unter der Sperre nicht mehr; nichts wurde gesendet."""


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
        case SetTarget(vehicle=key):
            return (
                "intent.target",
                key,
                _target_comment(_vehicle(base, key)),
                _target_comment(_vehicle(new, key)),
            )
        case SetNowLimit(vehicle=key):
            return (
                "intent.now_limit",
                key,
                _now_limit_comment(_vehicle(base, key).now_max_a),
                _now_limit_comment(_vehicle(new, key).now_max_a),
            )
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
        case SetGuestNowLimit():
            return (
                "intent.guest_now_limit",
                "gast",
                _now_limit_comment(base.guest.now_max_a),
                _now_limit_comment(new.guest.now_max_a),
            )
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
        case SetSupport():
            return (
                "control.support",
                SUPPORT_TARGET,
                _support_comment(base.support),
                _support_comment(new.support),
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


def _support_comment(support: SupportIntent) -> str:
    """Kommentar im Änderungsprotokoll: „erlaubt, ab {soc} %“, „erlaubt, Schwelle aus der
    Konfiguration“ oder „gesperrt“."""
    if not support.allowed:
        return "gesperrt"
    if support.soc_pct is None:
        return "erlaubt, Schwelle aus der Konfiguration"
    return f"erlaubt, ab {de(support.soc_pct)} %"


def _now_limit_comment(max_a: int | None) -> str:
    """Kommentar im Änderungsprotokoll: „Sofort-Höchststrom {n} A“ oder „… volle Leistung“."""
    return "Sofort-Höchststrom volle Leistung" if max_a is None else f"Sofort-Höchststrom {max_a} A"


def _target_comment(vehicle: VehicleIntent) -> str:
    """Kommentar im Änderungsprotokoll: „Ziel {soc} %, Abfahrt {HH:MM}“, mit Tag
    „Ziel {soc} %, Abfahrt {Wochentag} {TT.MM.} {HH:MM}“."""
    day = vehicle.departure_on
    shown = f"{WEEKDAYS[day.weekday()]} {day:%d.%m.} " if day is not None else ""
    return f"Ziel {vehicle.target_soc_pct} %, Abfahrt {shown}{vehicle.departure}"


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

    Wer Ziel oder Abfahrt eines Fahrzeugs bedient (``SetTarget`` mit ``source`` ungleich
    ``"system"``), meldet sich über ``on_manual_target`` bei der Automatik (Plan 5b,
    Präzisierung 8).
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
        self._manual_target: list[Callable[[str, datetime | None], None]] = []

    def on_manual_target(self, callback: Callable[[str, datetime | None], None]) -> None:
        """Meldet einen Rückruf an, den jede angenommene Bedienung von Ziel oder Abfahrt ruft.

        Der Rückruf erhält das Fahrzeug und ``next_departure`` der neuen Absicht, auch wenn die
        Änderung Ziel und Abfahrt nicht verändert hat: Die Person hat gehandelt. Ein Rückruf
        gilt nicht für abgelehnte Änderungen, andere Änderungen und ``source == "system"``. Mehrere
        Rückrufe laufen in der Reihenfolge der Anmeldung; ein Fehler in einem wird protokolliert
        und ändert nichts an der angenommenen Änderung.
        """
        self._manual_target.append(callback)

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
        """Prüft Fahrzeuge, Wallbox, Unterstützungsschwelle, Ziel-SOC und Abfahrt der Änderung.

        Eine eingegebene Schwelle unter der USV-Reserve scheitert hier vor dem Senden und ohne
        Eintrag im Änderungsprotokoll. Eine gespeicherte, die nach einer höheren Reserve in
        ``config.yaml`` darunter liegt, hebt der core beim nächsten Senden mit Hinweis auf die
        Reserve (Gesamtreview, Minor 3).
        """
        keys: list[str] = []
        match change:
            case SetSupport(soc_pct=soc) if soc is not None:
                reserve = self._cfg.battery.reserve_soc_pct
                if not math.isfinite(soc):
                    raise Rejected(["Unterstützungsschwelle ist keine endliche Zahl."])
                if soc < reserve:
                    below = f"liegt unter der USV-Reserve ({de(reserve)} %)."
                    raise Rejected([f"Unterstützungsschwelle {de(soc)} % {below}"])
                if soc > 100:
                    raise Rejected([f"Unterstützungsschwelle {de(soc)} % liegt über 100 %."])
            case SetMode(vehicle=key) | SetBoost(vehicle=key):
                keys = [key]
            case SetTarget(vehicle=key, target_soc_pct=soc, departure=departure, departure_on=day):
                self._require_vehicle(key)  # vor den Bereichen: ein unbekanntes Fahrzeug ist 404
                if soc is not None and not TARGET_SOC_MIN <= soc <= TARGET_SOC_MAX:
                    raise Rejected([f"Ziel-SOC „{soc}“ liegt außerhalb von 20–100 %."])
                if departure is not None and DEPARTURE_PATTERN.fullmatch(departure) is None:
                    raise Rejected([f"Abfahrt „{departure}“ ist keine Uhrzeit (HH:MM)."])
                if day is not None and departure is None:
                    raise Rejected([DEPARTURE_DATE_NEEDS_TIME])
                if soc is None and departure is None:
                    raise Rejected([TARGET_NEEDS_A_FIELD])
            case SetNowLimit(vehicle=key, max_a=max_a):
                self._require_vehicle(key)  # vor dem Bereich: ein unbekanntes Fahrzeug ist 404
                if max_a is not None:
                    vehicle = self._cfg.vehicles[key]
                    self._check_now_limit(max_a, (vehicle.min_a, vehicle.max_a))
            case SetGuestNowLimit(max_a=max_a) if max_a is not None:
                span = guest_range(self._cfg)
                if span is None:
                    raise Rejected([NO_GUEST_LIMIT])
                self._check_now_limit(max_a, span)
            case SetAssignment(wallbox=wallbox, vehicle=key):
                if wallbox not in self._cfg.wallboxes:
                    raise UnknownTarget(f"Unbekannte Wallbox „{wallbox}“.")
                keys = [key] if key is not None else []
            case SetPriority(priority=priority):
                keys = priority
        for key in keys:
            self._require_vehicle(key)

    @staticmethod
    def _check_now_limit(max_a: int, span: tuple[int, int]) -> None:
        low, high = span
        if not low <= max_a <= high:
            raise Rejected([f"Sofort-Höchststrom „{max_a} A“ liegt außerhalb von {low}–{high} A."])

    def _normalised(self, change: Change) -> Change:
        """Der Höchststrom des Fahrzeugs (beim Gast: der EVCS) ist „volle Leistung“ und wird als
        ``None`` gespeichert (Entscheidung 7); die Prüfung ``_check`` ist vorausgegangen."""
        match change:
            case SetNowLimit(vehicle=key, max_a=max_a) if max_a is not None:
                if max_a >= self._cfg.vehicles[key].max_a:
                    return SetNowLimit(key, None)
            case SetGuestNowLimit(max_a=max_a) if max_a is not None:
                span = guest_range(self._cfg)
                if span is not None and max_a >= span[1]:
                    return SetGuestNowLimit(None)
        return change

    def _require_vehicle(self, key: str) -> None:
        if key not in self._cfg.vehicles:
            raise UnknownTarget(f"Unbekanntes Fahrzeug „{key}“.")

    async def apply(
        self,
        change: Change,
        *,
        actor: str,
        source: Source,
        precondition: Callable[[], bool] | None = None,
    ) -> ChangeResult:
        """Wendet die Änderung auf die gültige Absicht an und sendet sie.

        ``precondition`` prüft der Dienst unter seiner Sperre vor dem Senden; gilt sie nicht,
        wird nichts gesendet und nichts protokolliert (``PreconditionFailed``). So sieht die
        Automatik eine Bedienung, die zwischen ihrer Prüfung und dem Senden angenommen wurde.

        Fehler: ``UnknownTarget`` (Fahrzeug oder Wallbox unbekannt), ``CoreDown``,
        ``Rejected`` (422 des core), ``ConflictAgain`` (zweites 409), ``LiveNotAuthorized``
        (ein Aktor ginge ohne Prüfung live; die Absicht bleibt unverändert) und
        ``PreconditionFailed``.
        """
        self._check(change)
        change = self._normalised(change)
        async with self._lock:
            if precondition is not None and not precondition():
                raise PreconditionFailed
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
        if isinstance(change, SetTarget) and source != "system":
            self._notify_manual_target(change.vehicle, new)
        return ChangeResult(result, version, details, new)

    def _notify_manual_target(self, key: str, new: Intent) -> None:
        """Ruft die Rückrufe der Bedienung; ein Fehler darin bleibt im Protokoll."""
        vehicle = _vehicle(new, key)
        departure_at = next_departure(vehicle.departure, self._clock.now(), vehicle.departure_on)
        for callback in self._manual_target:
            try:
                callback(key, departure_at)
            except Exception:
                log.exception(
                    "Rückruf der Bedienung von Ziel und Abfahrt fehlgeschlagen", vehicle=key
                )
