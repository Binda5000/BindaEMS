"""Interne API, mit Token geschützt: Gesundheit, Zustand, Absicht, Entscheidungen, Live-Stream."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import fields
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, status
from pydantic import BaseModel, Field, SecretStr

from bindaems.core.control.intent import IntentReply
from bindaems.core.state.derived import Derived
from bindaems.core.watchdog_client.heartbeat import HeartbeatPhase
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION, WatchdogStatus
from bindaems.shared.control import Intent
from bindaems.shared.decisions import Decision, decision_to_json
from bindaems.shared.domain import Snapshot

WS_UNAUTHORIZED = 4401

# Starlette hat den Namen für 422 geändert (ENTITY zu CONTENT); die Zahl gilt in jeder Version.
HTTP_UNPROCESSABLE = 422

# Entscheidungen je Abruf: Standard und Höchstwert von ``limit``, dazu die Texte der 422-Antworten
DECISIONS_LIMIT_DEFAULT = 1000
DECISIONS_LIMIT_MAX = 5000
SINCE_DETAIL = "since muss eine ISO-Zeit mit Zeitzone sein"
LIMIT_DETAIL = f"limit muss zwischen 1 und {DECISIONS_LIMIT_MAX} liegen"


class HealthReport(BaseModel):
    """Gesundheit des core; die Felder aus Plan 2a haben Defaults, ältere Aufrufer bleiben gültig.

    - ``mode``, ``mode_since``, ``mode_reasons``: Betriebszustand, ISO-Zeit des letzten Wechsels
      und die Gründe des Zustands
    - ``intent``: die gültige Absicht als JSON
    - ``actuators``: je Aktor in Katalogreihenfolge angeforderte und wirksame Betriebsart, Freigabe,
      Gründe und Sperrgründe (``locked``); dazu, ob er steuerbar ist (``controllable``), die Gründe,
      warum nicht (``capability_reasons``), die Arten mit offenem Schreibvorgang (``pending``) und
      ob er Werte des EMS halten kann, die bei der Übergabe zurückgesetzt werden (``owned``)
    - ``watchdog``: Watchdog am Cerbo und Heartbeat (``watchdog_to_json``)
    - ``inputs``: Ergebnis der Eingangsprüfung (Regel 7)
    """

    mode: Literal["START", "OBSERVE", "CONTROL", "DEGRADED", "SAFE"]
    version: str
    adapters: list[dict[str, Any]]
    selfcheck: list[dict[str, str]]
    alarms: list[dict[str, str]]
    cycle_ms_p95: float | None
    mode_since: str | None = None
    mode_reasons: list[str] = Field(default_factory=list)
    intent: dict[str, Any] | None = None
    actuators: list[dict[str, Any]] = Field(default_factory=list)
    watchdog: dict[str, Any] = Field(
        default_factory=lambda: watchdog_to_json(WatchdogStatus.unknown(), "off", required=False)
    )
    inputs: dict[str, Any] = Field(default_factory=lambda: {"ok": True, "problems": []})


class CurrentIntent(BaseModel):
    intent: Intent | None


class IntentResponse(BaseModel):
    result: Literal["accepted", "clamped", "rejected"]
    version: int | None
    details: list[str]


class CoreView(Protocol):
    def health(self) -> HealthReport: ...

    def state(self) -> dict[str, Any]: ...

    def intent(self) -> Intent | None: ...

    def submit_intent(self, intent: Intent) -> IntentReply: ...

    def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        """Die Entscheidungen mit ``ts`` nach ``since``, aufsteigend, und ob weitere folgen.

        Das Ergebnis kann ``limit`` überschreiten: Entscheidungen gleicher Zeit bleiben beisammen.
        Der Aufruf liest Dateien; die API ruft ihn in einem Arbeitsthread auf.
        """

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]: ...


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def snapshot_to_json(snap: Snapshot, derived: Derived) -> dict[str, Any]:
    return {
        "ts": snap.ts.isoformat(),
        "signals": {
            signal: {"v": reading.value, "ts": reading.ts.isoformat(), "q": reading.quality.value}
            for signal, reading in snap.readings.items()
        },
        "derived": {field.name: _plain(getattr(derived, field.name)) for field in fields(derived)},
    }


def watchdog_to_json(
    watchdog: WatchdogStatus, heartbeat: HeartbeatPhase, *, required: bool
) -> dict[str, Any]:
    """Status des Watchdogs mit der erwarteten Version, dazu Heartbeat und ``required``.

    ``required`` heißt: Der Betrieb braucht den Watchdog. Das gilt, solange ein Victron- oder
    EVCS-Aktor mit ``live_allowed`` im Regelbetrieb live gewünscht ist oder übergeben wird (Plan 2b,
    Präzisierung 2); ohne Regelbetrieb und ohne Übergabe nicht.
    """
    last_trip = watchdog.last_trip
    return {
        "state": watchdog.state.value,
        "trip_count": watchdog.trip_count,
        "last_trip": last_trip.isoformat() if last_trip is not None else None,
        "last_trip_failures": watchdog.last_trip_failures,
        "version": watchdog.version,
        "expected_version": WATCHDOG_VERSION,
        "heartbeat": heartbeat,
        "required": required,
    }


def _intent_status(reply: IntentReply) -> int:
    """200 für eine angenommene (auch geklemmte) Absicht, 422 für unbekannte Aktoren, sonst 409."""
    if reply.result != "rejected":
        return status.HTTP_200_OK
    if reply.rejection == "unknown_actuator":
        return HTTP_UNPROCESSABLE
    return status.HTTP_409_CONFLICT


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=HTTP_UNPROCESSABLE, detail=detail)


def _since(text: str | None) -> datetime:
    """Der Parameter ``since`` als Zeitpunkt in UTC; ohne ISO-Zeit mit Zeitzone ein 422.

    Ohne Zeitzone wäre der Zeitpunkt mehrdeutig, und der Spool lehnt ihn ab. Nur eine gültige
    Eingabe kehrt zurück; alles andere endet im 422 am Schluss.
    """
    if text is not None:
        try:
            moment = datetime.fromisoformat(text)
            if moment.utcoffset() is not None:
                return moment.astimezone(UTC)
        except (ValueError, OverflowError):
            pass  # keine ISO-Zeit, oder in UTC außerhalb von Jahr 1 bis 9999
    raise _unprocessable(SINCE_DETAIL)


def _limit(text: str | None) -> int:
    """Der Parameter ``limit``: ohne Angabe der Standard.

    Sonst muss er eine Ganzzahl von 1 bis zum Höchstwert sein, alles andere ist ein 422.
    """
    if text is None:
        return DECISIONS_LIMIT_DEFAULT
    try:
        count = int(text)
    except ValueError:
        raise _unprocessable(LIMIT_DETAIL) from None
    if not 1 <= count <= DECISIONS_LIMIT_MAX:
        raise _unprocessable(LIMIT_DETAIL)
    return count


def _bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, credentials = authorization.partition(" ")
    return credentials if scheme.lower() == "bearer" and credentials else None


def _matches(candidate: str | None, token: SecretStr) -> bool:
    if candidate is None:
        return False
    return hmac.compare_digest(candidate.encode(), token.get_secret_value().encode())


def create_api(view: CoreView, token: SecretStr) -> FastAPI:
    app = FastAPI(title="BindaEMS core", docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if not _matches(_bearer(authorization), token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token fehlt oder ist ungültig",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    def health() -> HealthReport:
        return view.health()

    @app.get("/v1/state", dependencies=[Depends(require_token)])
    def state() -> dict[str, Any]:
        return view.state()

    @app.get("/v1/intent", dependencies=[Depends(require_token)])
    def get_intent() -> CurrentIntent:
        return CurrentIntent(intent=view.intent())

    @app.put("/v1/intent", dependencies=[Depends(require_token)])
    def put_intent(intent: Intent, response: Response) -> IntentResponse:
        reply = view.submit_intent(intent)
        response.status_code = _intent_status(reply)
        return IntentResponse(
            result=reply.result, version=reply.version, details=list(reply.details)
        )

    @app.get("/v1/decisions", dependencies=[Depends(require_token)])
    def get_decisions(since: str | None = None, limit: str | None = None) -> dict[str, Any]:
        """Die Entscheidungen mit ``ts`` nach ``since`` (Pflicht), aufsteigend, bis ``limit``.

        ``more`` sagt, ob nach den gelieferten noch weitere folgen. Die Antwort kann ``limit``
        überschreiten, denn Entscheidungen gleicher Zeit (ein Zyklus) bleiben beisammen. Wer
        weiterliest, nimmt das ``ts`` der letzten gelieferten als neues ``since``. Im Query steht
        das ``+`` einer Zeitzone als ``%2B``.
        """
        # Beide Parameter als Text: FastAPI meldete einen Fehler sonst in eigener Form, und ein
        # ``datetime`` ließe auch Zeiten ohne Zeitzone zu. Als ``def`` läuft der Endpunkt in einem
        # Arbeitsthread; das Lesen der Dateien hält die Ereignisschleife (den Zyklus) nicht auf.
        moment, count = _since(since), _limit(limit)
        found, more = view.decisions(moment, count)
        return {"decisions": [decision_to_json(decision) for decision in found], "more": more}

    @app.websocket("/v1/stream")
    async def stream(websocket: WebSocket) -> None:
        # nur per Header: ein Token in der URL landete in den Zugriffslogs
        candidate = _bearer(websocket.headers.get("authorization"))
        # erst annehmen, dann schließen: sonst sähe ein echter Client nur HTTP 403 statt 4401
        await websocket.accept()
        if not _matches(candidate, token):
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        async with view.subscribe() as messages:
            async for message in messages:
                await websocket.send_json(message)
        await websocket.close()

    return app
