"""Endpunkte der Bedienung und der Entscheidungen (Plan 2d, Präzisierungen 3 bis 6).

Ohne ``from __future__ import annotations`` (siehe ``auth/routes.py``).
"""

import asyncio
import re
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from bindaems.app.audit import AuditLog
from bindaems.app.auth.service import (
    AuthService,
    InvalidCredentialsError,
    LockedError,
    SessionInfo,
)
from bindaems.app.auth.web import Guard
from bindaems.app.control.boost_cost import boost_cost
from bindaems.app.control.decisions import DecisionQuery, DecisionStore, StoredDecision
from bindaems.app.control.intent import (
    Change,
    ChangeResult,
    ConflictAgain,
    CoreDown,
    IntentService,
    LiveNotAuthorized,
    Rejected,
    SetActuators,
    SetAssignment,
    SetBoost,
    SetControl,
    SetGuestMode,
    SetGuestNowLimit,
    SetMode,
    SetNowLimit,
    SetPriority,
    SetSupport,
    SetTarget,
    UnknownTarget,
)
from bindaems.app.core_link import CoreClient, CoreUnavailableError, LiveState
from bindaems.app.learning.driving_auto import DrivingAutomation
from bindaems.app.ledger.service import LedgerService
from bindaems.app.settings.service import SettingsService, VersionConflictError
from bindaems.shared.config import Config
from bindaems.shared.control import ActuatorMode, ChargeMode, SurplusPriority
from bindaems.shared.decisions import DecisionResult, decision_to_json
from bindaems.shared.timeutil import LOCAL_TZ, Clock, SystemClock

LATEST_WINDOW = timedelta(hours=24)
LIMIT_MAX = 500
CORE_DOWN = "core nicht erreichbar."
CONFLICT = "Die Absicht wurde gleichzeitig geändert. Bitte erneut versuchen."
NEEDS_CONFIRMATION = "Bitte bestätige Prüfprotokoll Teil 2."
WRONG_PASSWORD = "Passwort falsch."  # noqa: S105 (Fehlertext, kein Passwort)
NO_ACTUATORS = "Keine Aktoren gewählt."
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OnBody(_Body):
    on: bool


class ModeBody(_Body):
    mode: ChargeMode


class TargetBody(_Body):
    """Ziel-SOC und Abfahrt (``HH:MM``); ``null`` lässt das Feld unverändert. Den Bereich prüft
    der ``IntentService`` mit den Sätzen der Ablehnung. ``departure_on`` (``YYYY-MM-DD``) setzt die
    Abfahrt auf einen Tag; ohne ihn oder mit ``null`` gilt sie täglich. Er braucht ``departure``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_soc_pct: int | None
    departure: str | None
    departure_on: Annotated[date | None, Field(strict=False)] = None

    @field_validator("departure_on", mode="before")
    @classmethod
    def _only_a_date(cls, value: object) -> object:
        """Nur ``YYYY-MM-DD``: pydantic nähme sonst auch einen Zeitstempel oder eine Zahl."""
        if value is not None and not (isinstance(value, str) and DATE_PATTERN.fullmatch(value)):
            raise ValueError("Abfahrtstag muss ein Datum sein (YYYY-MM-DD).")
        return value


class NowLimitBody(_Body):
    """Höchststrom in „Sofort“ in ganzen A je Phase; ``null`` heißt volle Leistung. Den Bereich
    prüft der ``IntentService``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_a: int | None


class AssignmentBody(_Body):
    vehicle: str | None


class SupportBody(_Body):
    """Akku-Unterstützung; ``soc_pct`` ``null`` heißt „Schwelle aus der Konfiguration“."""

    model_config = ConfigDict(extra="forbid", strict=True)

    allowed: bool
    soc_pct: float | None


class PriorityBody(_Body):
    priority: list[str]
    surplus_priority: SurplusPriority


class ActuatorsBody(_Body):
    modes: dict[str, ActuatorMode]
    password: str | None = None
    confirmed: bool = False


def _decision_json(item: StoredDecision) -> dict[str, Any]:
    return {**decision_to_json(item.decision), "cursor": item.cursor}


def _actuator_order(live: LiveState | None) -> dict[str, int]:
    """Stelle jedes Aktors in Health (Katalogreihenfolge des core)."""
    health = live.health if live is not None else None
    raw = health.get("actuators") if health is not None else None
    if not isinstance(raw, list):
        return {}
    ids = [item["id"] for item in raw if isinstance(item, dict) and isinstance(item.get("id"), str)]
    return {actuator: index for index, actuator in enumerate(ids)}


def _locked(exc: LockedError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        str(exc),
        headers={"Retry-After": str(exc.retry_after_s)},
    )


def control_router(
    service: IntentService,
    store: DecisionStore,
    client: CoreClient,
    auth: AuthService,
    audit: AuditLog,
    guard: Guard,
    *,
    settings: SettingsService,
    ledger: LedgerService,
    cfg: Config,
    clock: Clock | None = None,
    automation: DrivingAutomation,
    live: LiveState | None = None,
) -> APIRouter:
    """Routen der Bedienung. ``clock`` und ``live`` brauchen nur „Warum?“: das Fenster der
    letzten 24 h und die Reihenfolge der Aktoren aus Health. ``settings`` (Tarif), ``ledger``
    (Monatsspitze) und ``cfg`` (Fahrzeuge, Netzspannung) brauchen die Mehrkosten des Boosts.
    ``automation`` (Plan 5b) schaltet die Automatik für Ziel und Abfahrt."""
    now = (clock or SystemClock()).now
    router = APIRouter()
    Viewer = Annotated[SessionInfo, Depends(guard.require("viewer"))]
    Operator = Annotated[SessionInfo, Depends(guard.require("operator"))]
    Admin = Annotated[SessionInfo, Depends(guard.require("admin"))]

    async def change(change_: Change, session: SessionInfo) -> dict[str, Any]:
        try:
            result: ChangeResult = await service.apply(
                change_, actor=session.user.username, source="ui"
            )
        except UnknownTarget as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.text) from exc
        except CoreDown as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CORE_DOWN) from exc
        except ConflictAgain as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, CONFLICT) from exc
        except Rejected as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(exc.details)
            ) from exc
        return {
            "result": result.result,
            "version": result.version,
            "details": result.details,
            "intent": result.intent.model_dump(mode="json"),
        }

    @router.get("/api/control")
    async def read(session: Viewer) -> dict[str, Any]:
        try:
            intent = await service.current()
        except CoreDown:
            return {"core_connected": False, "intent": None}
        return {
            "core_connected": True,
            "intent": intent.model_dump(mode="json") if intent is not None else None,
        }

    @router.get("/api/control/boost-cost")
    async def boost_costs(session: Viewer) -> dict[str, Any]:
        day = now().astimezone(LOCAL_TZ).date()
        peak = await asyncio.to_thread(ledger.month_peak, day)
        tariff = settings.current().settings.tariff
        return boost_cost(cfg, tariff, None if peak is None else peak.kw, day)

    @router.put("/api/control/regulation")
    async def regulation(body: OnBody, session: Operator) -> dict[str, Any]:
        return await change(SetControl(body.on), session)

    @router.put("/api/control/vehicles/{key}/mode")
    async def vehicle_mode(key: str, body: ModeBody, session: Operator) -> dict[str, Any]:
        return await change(SetMode(key, body.mode), session)

    @router.put("/api/control/vehicles/{key}/target")
    async def vehicle_target(key: str, body: TargetBody, session: Operator) -> dict[str, Any]:
        return await change(
            SetTarget(key, body.target_soc_pct, body.departure, body.departure_on), session
        )

    @router.put("/api/control/vehicles/{key}/now-limit")
    async def vehicle_now_limit(key: str, body: NowLimitBody, session: Operator) -> dict[str, Any]:
        return await change(SetNowLimit(key, body.max_a), session)

    @router.put("/api/control/vehicles/{key}/automation")
    async def vehicle_automation(key: str, body: OnBody, session: Operator) -> dict[str, Any]:
        # Der Eintrag ``control.automation`` im Änderungsprotokoll entsteht im Schalter selbst
        try:
            await automation.set_enabled(key, body.on, actor=session.user.username, source="ui")
        except UnknownTarget as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.text) from exc
        except VersionConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, CONFLICT) from exc
        return automation.state_json(key)

    @router.put("/api/control/vehicles/{key}/boost")
    async def vehicle_boost(key: str, body: OnBody, session: Operator) -> dict[str, Any]:
        return await change(SetBoost(key, body.on), session)

    @router.put("/api/control/wallboxes/{key}/vehicle")
    async def wallbox_vehicle(key: str, body: AssignmentBody, session: Operator) -> dict[str, Any]:
        return await change(SetAssignment(key, body.vehicle), session)

    @router.put("/api/control/guest")
    async def guest(body: ModeBody, session: Operator) -> dict[str, Any]:
        return await change(SetGuestMode(body.mode), session)

    @router.put("/api/control/guest/now-limit")
    async def guest_now_limit(body: NowLimitBody, session: Operator) -> dict[str, Any]:
        return await change(SetGuestNowLimit(body.max_a), session)

    @router.put("/api/control/support")
    async def support(body: SupportBody, session: Operator) -> dict[str, Any]:
        return await change(SetSupport(body.allowed, body.soc_pct), session)

    @router.put("/api/control/priority")
    async def priority(body: PriorityBody, session: Operator) -> dict[str, Any]:
        return await change(SetPriority(body.priority, body.surplus_priority), session)

    @router.put("/api/control/actuators")
    async def actuators(body: ActuatorsBody, session: Admin) -> dict[str, Any]:
        # Nur Aktoren, deren Betriebsart auf „live“ wechselt, verlangen Passwort und Häkchen
        # (Präzisierung 4); dafür zählt die gültige Absicht des core. Der IntentService prüft
        # jeden Versuch noch einmal gegen seine Basis (``authorized_live``).
        if not body.modes:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_ACTUATORS)
        try:
            current = await service.current()
        except CoreDown as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CORE_DOWN) from exc
        before = current.actuators if current is not None else {}
        going_live = frozenset(
            actuator
            for actuator, mode in body.modes.items()
            if mode is ActuatorMode.LIVE and before.get(actuator) is not ActuatorMode.LIVE
        )
        if going_live:
            try:
                if body.password is None:
                    raise InvalidCredentialsError
                await asyncio.to_thread(auth.check_password, session.user.id, body.password)
            except LockedError as exc:
                raise _locked(exc) from exc
            except InvalidCredentialsError as exc:
                raise HTTPException(status.HTTP_403_FORBIDDEN, WRONG_PASSWORD) from exc
            if not body.confirmed:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NEEDS_CONFIRMATION)
        try:
            return await change(SetActuators(body.modes, going_live), session)
        except LiveNotAuthorized as exc:
            # Ein Aktor ist inzwischen nicht mehr live: ohne geprüftes Passwort fehlt das
            # Passwort, mit ihm die Bestätigung für diesen Aktor.
            if going_live:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, NEEDS_CONFIRMATION
                ) from exc
            raise HTTPException(status.HTTP_403_FORBIDDEN, WRONG_PASSWORD) from exc

    @router.post("/api/control/locks/{actuator}/ack")
    async def acknowledge(actuator: str, session: Admin) -> JSONResponse:
        try:
            reply = await client.acknowledge(actuator)
        except CoreUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CORE_DOWN) from exc
        detail = str(reply.body.get("detail", ""))
        audit.record(
            session.user.username,
            "ui",
            "lock.acknowledge",
            actuator,
            {"status": reply.status, "detail": detail},
        )
        return JSONResponse({"detail": detail}, status_code=reply.status)

    @router.get("/api/decisions")
    async def decisions(
        session: Viewer,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        actuator: Annotated[str | None, Query(max_length=100)] = None,
        result: DecisionResult | None = None,
        dry_run: bool = True,
        text: Annotated[str | None, Query(max_length=200)] = None,
        before: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = 100,
    ) -> dict[str, Any]:
        query = DecisionQuery(
            since=since,
            until=until,
            actuator=actuator,
            result=result,
            dry_run=dry_run,
            text=text,
            before=before,
            limit=limit,
        )
        found, more = await asyncio.to_thread(store.query, query)
        return {"decisions": [_decision_json(item) for item in found], "more": more}

    @router.get("/api/decisions/latest")
    async def latest(session: Viewer) -> dict[str, Any]:
        found = await asyncio.to_thread(store.latest_per_actuator, now() - LATEST_WINDOW)
        order = _actuator_order(live)
        # sorted ist stabil: unbekannte Aktoren folgen am Ende in der Reihenfolge des Stores
        found.sort(key=lambda item: order.get(item.decision.actuator, len(order)))
        return {"decisions": [_decision_json(item) for item in found]}

    return router
