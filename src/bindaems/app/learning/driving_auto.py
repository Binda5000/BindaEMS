"""Automatik für Ziel und Abfahrt in „Geplant“ (Plan 5b, Entscheidung 8, Präzisierung 8).

Je Auto ein Schalter „Ziel und Abfahrt automatisch“ (``learning.driving.automation[key]``, ab Werk
aus). Ist er an, prüft die Automatik jede Minute und nach jedem Lauf des Fahrverhaltens der Reihe
nach:

1. Health ist vertrauenswürdig (``health_trusted``), sonst ``error``;
2. die Absicht nennt das Auto in „Geplant“, sonst ``inactive``;
3. keine geplante Ladung des Autos läuft (offene Zeile in ``planned_charge``), außer sie wurde vor
   höchstens ``AUTO_ADOPT`` geöffnet (``opened_at``, Ruling T8-13); sonst ``waiting``;
4. die Pause nach einer Bedienung ist vorbei (``paused_until``), sonst ``paused``;
5. die nächste erwartete Fahrt (``next_trip``) hat für Abfahrt oder Ziel mindestens „mittel“,
   sonst ``uncertain``.

Abfahrt (Uhrzeit und Tag der Fahrt) und Ziel gelten unabhängig: Übernommen wird nur ein Wert mit
Konfidenz ab „mittel“, der andere bleibt, wie er in der Absicht steht. Weichen die Werte von der
Absicht ab, sendet die Automatik ``SetTarget`` mit „Automatik“ als Urheber (``applied``), aber
nur, wenn seit dem Lesen der Absicht niemand Ziel oder Abfahrt bedient hat (Vorbedingung unter der
Sperre des ``IntentService``, Ruling T8-11; sonst bleibt der Zustand, die Pause gilt ab der
nächsten Prüfung); bei
``CoreDown``, ``Rejected`` oder einem zweiten Konflikt ``error`` mit dem Grund, der nächste Versuch
folgt in einer Minute. Den Modus ändert sie nie.

Bedient jemand Ziel oder Abfahrt eines Autos mit Automatik (``IntentService.on_manual_target``),
pausiert sie bis zu dieser Abfahrt (ohne Abfahrt bis Mitternacht). ``paused_until``,
``last_set_at`` und ``last_values`` stehen in ``driving_automation`` und überstehen einen Neustart.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import structlog
from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.audit import AuditLog, Source
from bindaems.app.charging.planned import AUTO_ADOPT, OpenCharge
from bindaems.app.control.intent import (
    WEEKDAYS,
    ConflictAgain,
    CoreDown,
    IntentService,
    PreconditionFailed,
    Rejected,
    SetTarget,
    UnknownTarget,
    health_trusted,
)
from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import driving_automation
from bindaems.app.learning.driving_profile import VehicleProfile, next_trip
from bindaems.app.learning.features import local_midnight
from bindaems.app.settings.service import SettingsService, VersionConflictError
from bindaems.shared.config import Config
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent, next_departure
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ensure_utc

log = structlog.get_logger(__name__)

AUTO_RECENT: Final = timedelta(minutes=2)  # so lange gilt ein Setzen als „gerade eben“
TICK_S: Final = 60.0
ACTOR: Final = "Automatik"
ACTION: Final = "control.automation"
CONFIDENT: Final = frozenset({"high", "medium"})  # Konfidenz ab „mittel“
SWITCH_TEXT: Final = {True: "Automatik an", False: "Automatik aus"}
# Gründe im Zustand ``error``
UNTRUSTED: Final = "Der Stand der Regelung ist unbekannt."
CORE_DOWN: Final = "core nicht erreichbar."
CONFLICT: Final = "Die Absicht wurde gleichzeitig geändert."
REJECTED: Final = "Der core lehnt die Absicht ab."
UNREADABLE: Final = "Zustand der Automatik nicht lesbar."
CHARGES_UNREADABLE: Final = "Geplante Ladungen nicht lesbar."

State = Literal["off", "inactive", "waiting", "paused", "uncertain", "applied", "error"]
Profiles = Callable[[str], VehicleProfile | None]
Charges = Callable[[], Mapping[str, OpenCharge]]  # ``PlannedCharges.open_charges`` (blockiert)


@dataclass(frozen=True)
class _Status:
    state: State
    text: str


@dataclass
class _Stored:
    """Eine Zeile in ``driving_automation``."""

    paused_until: datetime | None = None
    last_set_at: datetime | None = None
    last_values: dict[str, Any] | None = None


def _day_time(moment: datetime) -> str:
    """„{Tag} {HH:MM}“ in Ortszeit, etwa „Mo 07:15“."""
    local = ensure_utc(moment).astimezone(LOCAL_TZ)
    return f"{WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _stamp(moment: datetime) -> str:
    return f"{ensure_utc(moment).astimezone(LOCAL_TZ):%d.%m. %H:%M}"


def _off() -> _Status:
    return _Status("off", "Automatik aus.")


def _inactive() -> _Status:
    return _Status("inactive", "Automatik wartet: Das Auto steht nicht auf „Geplant“.")


def _waiting(departure_at: datetime) -> _Status:
    text = f"Automatik wartet: Eine geplante Ladung läuft bis {_day_time(departure_at)}."
    return _Status("waiting", text)


def _paused(until: datetime) -> _Status:
    text = (
        f"Automatik pausiert bis {_day_time(until)}, "
        "weil Ziel oder Abfahrt von Hand geändert wurden."
    )
    return _Status("paused", text)


def _uncertain() -> _Status:
    return _Status("uncertain", "Automatik wartet: Der Vorschlag ist noch zu unsicher.")


def _applied(vehicle: VehicleIntent, now: datetime, set_at: datetime | None) -> _Status:
    """Die Werte der Absicht; „(gesetzt …)“ nur, wenn die Automatik je gesetzt hat."""
    departure = next_departure(vehicle.departure, now, vehicle.departure_on)
    head = f"Automatik: {_day_time(departure)}, Ziel {vehicle.target_soc_pct} %"
    if set_at is None:
        return _Status("applied", f"{head}.")
    return _Status("applied", f"{head} (gesetzt {_stamp(set_at)}).")


def _error(reason: str) -> _Status:
    return _Status("error", f"Automatik fehlgeschlagen: {reason}")


def _next_midnight(now: datetime) -> datetime:
    return local_midnight(ensure_utc(now).astimezone(LOCAL_TZ).date() + timedelta(days=1))


class DrivingAutomation:
    """Setzt Ziel und Abfahrt der Autos in „Geplant“ nach der nächsten erwarteten Fahrt."""

    def __init__(
        self,
        *,
        intents: IntentService,
        live: LiveState,
        profiles: Profiles,
        charges: Charges,
        settings: SettingsService,
        audit: AuditLog,
        engine: Engine,
        cfg: Config,
        clock: Clock,
        interval_s: float = TICK_S,
    ) -> None:
        self._intents = intents
        self._live = live
        self._profiles = profiles
        self._charges = charges
        self._settings = settings
        self._audit = audit
        self._engine = engine
        self._cfg = cfg
        self._clock = clock
        self._interval_s = interval_s
        self._lock = asyncio.Lock()  # eine Prüfung nach der anderen
        self._wake = asyncio.Event()
        self._stored: dict[str, _Stored] = {key: _Stored() for key in cfg.vehicles}
        self._dirty: set[str] = set()  # noch nicht gespeicherte Zeilen
        self._loaded = False
        self._status: dict[str, _Status] = {}
        self._recent: dict[str, datetime] = {}  # letztes Setzen ohne spätere Bedienung
        # Bedienungen je Auto: ändert sich die Zahl zwischen Prüfung und Senden, sendet sie nicht
        self._manual: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._threads: set[asyncio.Future[Any]] = set()
        intents.on_manual_target(self.on_manual_target)

    # --- Zugriff --------------------------------------------------------------------------

    def enabled(self, key: str) -> bool:
        """Ob der Schalter des Autos an ist; Autos, die ``config.yaml`` nicht kennt, nie."""
        automation = self._settings.current().settings.learning.driving.automation
        return key in self._cfg.vehicles and automation.get(key, False)

    def state(self, key: str) -> dict[str, Any]:
        """Das Feld ``automation`` des Autos (Präzisierung 11); Zeiten als UTC-``datetime``."""
        stored = self._stored.get(key, _Stored())
        enabled = self.enabled(key)
        # vor der ersten Prüfung nach dem Start: „wartet“
        status = self._status.get(key, _inactive()) if enabled else _off()
        paused = stored.paused_until
        if not enabled or paused is None or self._clock.now() >= paused:
            paused = None
        return {
            "enabled": enabled,
            "state": status.state,
            "text": status.text,
            "paused_until": paused,
            "last_set_at": stored.last_set_at,
        }

    def auto_set_at(self, key: str, now: datetime) -> datetime | None:
        """Wann die Automatik das Auto gesetzt hat, wenn das höchstens ``AUTO_RECENT`` vor ``now``
        liegt und seither niemand Ziel oder Abfahrt bedient hat; sonst ``None`` (für
        ``PlannedCharges``, Präzisierung 9, Ruling T8-10)."""
        set_at = self._recent.get(key)
        if set_at is None or not timedelta(0) <= now - set_at <= AUTO_RECENT:
            return None
        return set_at

    # --- Rückmeldungen --------------------------------------------------------------------

    def on_manual_target(self, key: str, departure_at: datetime | None) -> None:
        """Bedienung von Ziel oder Abfahrt (``IntentService``, unter dessen Sperre: nur Speicher).

        Mit Automatik pausiert sie bis ``departure_at`` (ohne bis Mitternacht); gespeichert wird
        bei der nächsten Prüfung, die dieser Aufruf anstößt.
        """
        self._recent.pop(key, None)
        self._manual[key] = self._manual.get(key, 0) + 1
        if key not in self._stored or not self.enabled(key):
            return
        until = departure_at if departure_at is not None else _next_midnight(self._clock.now())
        self._stored[key].paused_until = ensure_utc(until)
        self._dirty.add(key)
        self._status[key] = _paused(until)
        log.info("Automatik pausiert nach einer Bedienung", vehicle=key, until=until.isoformat())
        self.request_tick()

    def request_tick(self) -> None:
        """Stößt eine Prüfung an (etwa nach einem Lauf des Fahrverhaltens)."""
        self._wake.set()

    # --- Schalter -------------------------------------------------------------------------

    async def set_enabled(self, key: str, on: bool, *, actor: str, source: Source) -> None:
        """Schaltet die Automatik eines Autos als neue Einstellungsversion mit Eintrag
        ``control.automation``. Einschalten beendet eine Pause und prüft sofort; Ausschalten gilt
        sofort, eine laufende Prüfung sendet danach nicht mehr."""
        if key not in self._cfg.vehicles:
            raise UnknownTarget(f"Unbekanntes Fahrzeug „{key}“.")
        changed = await self._offload(self._store_switch, key, on, actor, source)
        if not on:
            return
        # der Schalter ist gespeichert: ein Fehler danach zeigt sich im Zustand, nicht beim Aufrufer
        try:
            async with self._lock:
                if not self._loaded:  # erst laden, sonst käme die gelöschte Pause zurück
                    await self._load_stored()
                if changed:
                    self._stored[key].paused_until = None
                    self._dirty.add(key)
                self._status.pop(key, None)
            await self.tick()
        except Exception as exc:
            log.exception("Automatik nach dem Einschalten nicht geprüft", vehicle=key)
            self._status[key] = _error(str(exc) or type(exc).__name__)

    def _store_switch(self, key: str, on: bool, actor: str, source: Source) -> bool:
        """Speichert den Schalter (blockiert: Datenbank); ``False``, wenn er schon so stand.

        Bei einem Versionskonflikt wird einmal auf der neuen Version wiederholt."""
        for attempt in (1, 2):
            current = self._settings.current()
            driving = current.settings.learning.driving
            if driving.automation.get(key, False) == on:
                return False
            automation = {**driving.automation, key: on}
            learning = current.settings.learning.model_copy(
                update={"driving": driving.model_copy(update={"automation": automation})}
            )
            try:
                version = self._settings.update(
                    current.settings.model_copy(update={"learning": learning}),
                    base_version=current.version,
                    actor=actor,
                    source=source,
                    comment=SWITCH_TEXT[on],
                )
            except VersionConflictError:
                if attempt == 2:
                    raise
                continue
            details = {
                "from": SWITCH_TEXT[not on],
                "to": SWITCH_TEXT[on],
                "version": version.version,
            }
            self._audit.record(actor, source, ACTION, key, details)
            log.info("Automatik für Ziel und Abfahrt geschaltet", vehicle=key, on=on)
            return True
        raise AssertionError("unerreichbar")  # pragma: no cover - die Schleife kehrt zurück

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        """Prüft beim Start, dann jede Minute und auf Anstoß; ein Fehler kommt ins Log."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Automatik für Ziel und Abfahrt nicht geprüft")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval_s)
            self._wake.clear()

    async def close(self, timeout_s: float) -> bool:
        """Beim Herunterfahren: bricht die Schleife ab und wartet höchstens ``timeout_s`` auf
        Schreibzugriffe im Thread. ``True``, wenn keiner mehr läuft."""
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pending = set(self._threads)
        if not pending:
            return True
        done, _ = await asyncio.wait(pending, timeout=timeout_s)
        return len(done) == len(pending)

    async def _offload(self, func: Callable[..., Any], *args: Any) -> Any:
        """Blockierende Arbeit im Thread; ein Abbruch beendet nur das Warten."""
        work = asyncio.ensure_future(asyncio.to_thread(func, *args))
        self._threads.add(work)
        work.add_done_callback(self._thread_done)
        return await asyncio.shield(work)

    def _thread_done(self, work: asyncio.Future[Any]) -> None:
        self._threads.discard(work)
        if not work.cancelled():
            work.exception()  # holt einen Fehler ab, auf den nach einem Abbruch niemand wartet

    async def tick(self) -> None:
        """Eine Prüfung aller Autos (Präzisierung 8)."""
        async with self._lock:
            keys = [key for key in self._cfg.vehicles if self.enabled(key)]
            for key in self._cfg.vehicles:
                if key not in keys:
                    self._status[key] = _off()
            if not self._loaded:
                try:
                    await self._load_stored()
                except Exception:
                    log.exception("Zustand der Automatik nicht gelesen, sie sendet nicht")
                    for key in keys:
                        self._status[key] = _error(UNREADABLE)
                    return
            if keys:
                await self._check_all(keys)
            await self._flush()

    async def _check_all(self, keys: list[str]) -> None:
        # die Bedienungen vor dem Lesen der Absicht: jede spätere verhindert das Senden
        manual = {key: self._manual.get(key, 0) for key in keys}
        health = self._live.health if self._live.connected else None
        if not health_trusted(health):
            for key in keys:
                self._status[key] = _error(UNTRUSTED)
            return
        try:
            intent = await self._intents.current()
        except CoreDown:
            for key in keys:
                self._status[key] = _error(CORE_DOWN)
            return
        try:
            charges = await self._offload(self._charges)
        except Exception:
            log.exception("Geplante Ladungen für die Automatik nicht gelesen")
            for key in keys:
                self._status[key] = _error(CHARGES_UNREADABLE)
            return
        for key in keys:
            try:
                self._status[key] = await self._check(key, intent, charges, manual[key])
            except Exception as exc:
                log.exception("Automatik für ein Auto fehlgeschlagen", vehicle=key)
                self._status[key] = _error(str(exc) or type(exc).__name__)

    async def _check(
        self, key: str, intent: Intent | None, charges: Mapping[str, OpenCharge], manual: int
    ) -> _Status:
        now = self._clock.now()
        vehicle = (
            intent.vehicles.get(key, VehicleIntent()) if intent is not None else VehicleIntent()
        )
        if vehicle.mode is not ChargeMode.PLANNED:
            return _inactive()
        charge = charges.get(key)
        if charge is not None and now - charge.opened_at > AUTO_ADOPT:
            return _waiting(charge.departure_at)
        stored = self._stored[key]
        if stored.paused_until is not None:
            if now < stored.paused_until:
                return _paused(stored.paused_until)
            stored.paused_until = None
            self._dirty.add(key)
        profile = self._profiles(key)
        trip = next_trip(profile, now) if profile is not None else None
        if trip is None:
            return _uncertain()
        take_departure = trip.departure_confidence in CONFIDENT
        take_target = trip.target_soc_pct is not None and trip.target_confidence in CONFIDENT
        if not (take_departure or take_target):
            return _uncertain()
        # was nicht übernommen wird, bleibt, wie es in der Absicht steht (``None`` in ``SetTarget``)
        change = SetTarget(
            key,
            trip.target_soc_pct if take_target else None,
            trip.departure if take_departure else None,
            trip.date if take_departure else None,
        )
        wanted = (
            vehicle.target_soc_pct if change.target_soc_pct is None else change.target_soc_pct,
            vehicle.departure if change.departure is None else change.departure,
            vehicle.departure_on if change.departure is None else change.departure_on,
        )
        if wanted == (vehicle.target_soc_pct, vehicle.departure, vehicle.departure_on):
            return _applied(vehicle, now, stored.last_set_at)
        if not self.enabled(key):  # inzwischen ausgeschaltet: nichts mehr senden
            return _off()
        return await self._send(key, change, manual)

    def _unchanged(self, key: str, manual: int) -> bool:
        """Ob seit der Prüfung niemand Ziel oder Abfahrt des Autos bedient hat."""
        return self._manual.get(key, 0) == manual

    async def _send(self, key: str, change: SetTarget, manual: int) -> _Status:
        """Sendet die Änderung, wenn seit der Prüfung niemand bedient hat (unter der Sperre des
        ``IntentService``), und merkt sich, wann und was gesetzt wurde."""
        try:
            result = await self._intents.apply(
                change,
                actor=ACTOR,
                source="system",
                precondition=lambda: self._unchanged(key, manual),
            )
        except PreconditionFailed:
            # die Bedienung geht vor; ihre Pause gilt ab der nächsten Prüfung
            log.info("Automatik sendet nicht, weil gerade bedient wurde", vehicle=key)
            return self._status.get(key, _inactive())
        except CoreDown:
            return _error(CORE_DOWN)
        except Rejected as exc:
            return _error(" ".join(exc.details) or REJECTED)
        except ConflictAgain:
            return _error(CONFLICT)
        now = self._clock.now()
        sent = result.intent.vehicles.get(key, VehicleIntent())
        stored = self._stored[key]
        stored.last_set_at = now
        stored.last_values = {
            "target_soc_pct": sent.target_soc_pct,
            "departure": sent.departure,
            "departure_on": sent.departure_on.isoformat() if sent.departure_on else None,
        }
        if self._unchanged(key, manual):  # nach einer Bedienung übernimmt die Zählung nichts
            self._recent[key] = now
        self._dirty.add(key)
        log.info("Automatik hat Ziel und Abfahrt gesetzt", vehicle=key, **stored.last_values)
        return _applied(sent, now, now)

    # --- Ablage ---------------------------------------------------------------------------

    async def _load_stored(self) -> None:
        rows = await self._offload(self._load)
        for key, row in rows.items():
            if key not in self._stored:
                continue
            if key not in self._dirty:
                self._stored[key] = row
                continue
            # vor dem Laden geändert (eine Bedienung): gesetzte Felder gelten, die übrigen kommen
            # aus der Ablage, damit das Speichern sie nicht leert
            memory = self._stored[key]
            self._stored[key] = _Stored(
                paused_until=memory.paused_until or row.paused_until,
                last_set_at=memory.last_set_at or row.last_set_at,
                last_values=memory.last_values or row.last_values,
            )
        self._loaded = True

    def _load(self) -> dict[str, _Stored]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(driving_automation)).all()
        return {
            row.vehicle: _Stored(
                paused_until=None if row.paused_until is None else ensure_utc(row.paused_until),
                last_set_at=None if row.last_set_at is None else ensure_utc(row.last_set_at),
                last_values=row.last_values,
            )
            for row in rows
        }

    async def _flush(self) -> None:
        if not self._dirty:
            return
        rows = {key: _Stored(**vars(self._stored[key])) for key in self._dirty}
        self._dirty.clear()
        try:
            await self._offload(self._save, rows)
        except Exception:
            log.exception("Zustand der Automatik nicht gespeichert")
            self._dirty |= rows.keys()  # beim nächsten Mal noch einmal

    def _save(self, rows: Mapping[str, _Stored]) -> None:
        with self._engine.begin() as conn:
            for key, row in rows.items():
                values = {
                    "paused_until": row.paused_until,
                    "last_set_at": row.last_set_at,
                    "last_values": row.last_values,
                }
                statement = sqlite_insert(driving_automation).values(vehicle=key, **values)
                conn.execute(
                    statement.on_conflict_do_update(index_elements=["vehicle"], set_=values)
                )
