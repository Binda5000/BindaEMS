"""Dienst für das Fahrverhalten (Plan 5b, Entscheidungen 13 und 14, Rulings T6-1 bis T6-5).

Jede Nacht um 03:40 Uhr Ortszeit (nach dem Lastmodell um 03:15 Uhr), beim Start und auf die Taste
„Jetzt neu berechnen“ liest der Dienst je konfiguriertem Auto die Fahrdaten der letzten 56 Tage,
rechnet Tagesdatensätze und Profil in einem Thread und legt das Profil als Zeile in
``driving_profile`` ab; behalten werden 70 Tage. Scheitert das Lesen oder Rechnen für ein Auto,
gilt dessen letzter Stand weiter, und die Komponente ``driving`` nennt den Fehler. Einträge der
Einstellungen für Autos, die ``config.yaml`` nicht kennt, übergeht der Dienst.

Die Trefferquote der letzten 28 Tage entsteht nach jedem gelungenen Lauf aus den abgelegten Ständen
und den Tagesdatensätzen und bleibt im Speicher; nach einem Neustart fehlt sie bis zum ersten Lauf.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any, Final

import structlog
from pydantic import ValidationError
from sqlalchemy import Engine, delete, insert, select

from bindaems.app.db.schema import driving_profile
from bindaems.app.history.influx import InfluxReader
from bindaems.app.learning.driving_data import DrivingDataError, VehicleHistory, load_history
from bindaems.app.learning.driving_days import WINDOW_DAYS, DayRecord, day_records
from bindaems.app.learning.driving_profile import (
    VehicleProfile,
    WeekdayProfile,
    build_profile,
    next_trip,
    presence_at,
    with_settings,
)
from bindaems.app.learning.driving_quality import HitRates, hit_rates
from bindaems.app.learning.features import local_midnight
from bindaems.app.schedule import next_local
from bindaems.app.settings.service import SettingsService, SettingsVersion
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config
from bindaems.shared.settings import DrivingSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, ensure_utc, slot_start

log = structlog.get_logger(__name__)

RUN_AT: Final = time(3, 40)
COMPONENT: Final = "driving"
KEEP_DAYS: Final = 70
QUALITY_DAYS: Final = 28
PRESENCE_HOURS: Final = 48  # Anwesenheitsprognose im Status: die nächsten 48 Stunden

Snapshot = tuple[datetime, VehicleProfile]


def _build(
    history: VehicleHistory, settings: DrivingSettings, today: date
) -> tuple[list[DayRecord], VehicleProfile]:
    """Tagesdatensätze und Profil aus den gelesenen Daten (blockiert: im Thread)."""
    records = day_records(history, today=today)
    profile = build_profile(
        history.key, history.source, records, history.sessions, settings, today=today
    )
    return records, profile


def _hhmm(minutes: int | None) -> str | None:
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def _weekday_json(wp: WeekdayProfile) -> dict[str, Any]:
    return {
        "weekday": wp.weekday,
        "basis": wp.basis,
        "days": wp.days,
        "trips": wp.trips,
        "trip_pct": round(wp.trip_share * 100),
        "departure": _hhmm(wp.departure_min),
        "departure_p50": _hhmm(wp.departure_p50_min),
        "departure_p80": _hhmm(wp.departure_p80_min),
        "departure_confidence": wp.departure_confidence,
        "arrival_p50": _hhmm(wp.arrival_p50_min),
        "arrival_soc_p50": wp.arrival_soc_p50,
        "consumption_p50_pp": wp.consumption_p50_pp,
        "consumption_p80_pp": wp.consumption_p80_pp,
        "target_soc_pct": wp.target_soc_pct,
        "target_clamped": wp.target_clamped,
        "target_confidence": wp.target_confidence,
    }


def _quality_json(rates: HitRates) -> dict[str, Any]:
    return {
        "days": QUALITY_DAYS,
        "departure_n": rates.departure_n,
        "departure_hit_pct": rates.departure_pct,
        "target_n": rates.target_n,
        "target_hit_pct": rates.target_pct,
    }


def _without_switches(settings: DrivingSettings) -> DrivingSettings:
    """Die Einstellungen ohne die Schalter der Automatik: Nur sie zu schalten rechnet nicht neu."""
    return settings.model_copy(update={"automation": {}})


def _stamp(moment: datetime) -> str:
    return f"{ensure_utc(moment).astimezone(LOCAL_TZ):%d.%m. %H:%M}"


class DrivingService:
    COMPONENT = COMPONENT

    def __init__(
        self,
        *,
        reader: InfluxReader,
        engine: Engine,
        cfg: Config,
        settings: SettingsService,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._reader = reader
        self._engine = engine
        self._cfg = cfg
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._running = False
        self._profiles: dict[str, VehicleProfile] = {}
        self._stored_at: dict[str, datetime] = {}  # ``computed_at`` des gültigen Stands
        self._quality: dict[str, HitRates] = {}
        self._errors: dict[str, str] = {}  # Fehler des letzten Laufs je Auto
        self._tasks: set[asyncio.Task[Any]] = set()  # Aufgaben in ``run`` und ``recompute``
        # Rechnungen und Schreibzugriffe im Thread; nach einem Abbruch laufen sie weiter, bis sie
        # wirklich enden (``close`` wartet darauf, bevor die Datenbank schließt)
        self._threads: set[asyncio.Future[Any]] = set()
        self._run_done: list[Callable[[], None]] = []
        # Geänderte Einstellungen des Fahrverhaltens rechnen gleich neu (Ruling F-2); läuft schon
        # ein Lauf mit den alten, folgt danach einer (``_again``)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._seen_lock = threading.Lock()
        self._seen = _without_switches(settings.current().settings.learning.driving)
        self._again = False
        settings.on_change(self._on_settings)

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        """Beim Start die abgelegten Stände laden und neu rechnen; dann täglich um 03:40 Uhr
        Ortszeit."""
        self._track()
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.get_ident()
        await self.load_stored()
        await self._recompute_logged()
        while True:
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == (RUN_AT.hour, RUN_AT.minute))
            await self._sleep((due - now).total_seconds())
            await self._recompute_logged()

    def _track(self) -> None:
        """Merkt sich die laufende Aufgabe, damit ``close`` sie abbrechen kann."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _recompute_logged(self) -> None:
        try:
            if not await self.recompute():
                log.warning("Berechnung des Fahrverhaltens übersprungen, weil schon eine läuft")
        except Exception:  # der Tagesrhythmus darf nicht abreißen
            log.exception("Berechnung des Fahrverhaltens fehlgeschlagen")

    def running(self) -> bool:
        return self._running

    def on_run_done(self, callback: Callable[[], None]) -> None:
        """Meldet einen Rückruf an, den jeder beendete Lauf ruft (die Automatik prüft dann neu).

        Auch ein Lauf, in dem einzelne Autos scheiterten, ist beendet; ein abgebrochener nicht.
        Ein Fehler in einem Rückruf wird protokolliert, die übrigen laufen weiter.
        """
        self._run_done.append(callback)

    def _on_settings(self, version: SettingsVersion) -> None:
        """Rechnet neu, wenn sich ``learning.driving`` ändert, außer allein die Schalter der
        Automatik (Ruling F-2). Die Ziele gelten ohnehin sofort (``profile``); neu rechnen muss
        der Dienst für Puffer (Trefferquote) und eine geänderte Quelle der Anwesenheit. Ruft der
        Einstellungsdienst aus einem anderen Thread, läuft der Lauf in der Ereignisschleife."""
        new = _without_switches(version.settings.learning.driving)
        with self._seen_lock:
            old, self._seen = self._seen, new
        loop = self._loop
        if new == old or loop is None or loop.is_closed():
            return  # ohne laufenden Dienst rechnet der Start ohnehin
        if threading.get_ident() == self._loop_thread:
            self._settings_changed()
        else:
            with contextlib.suppress(RuntimeError):  # Schleife inzwischen geschlossen
                loop.call_soon_threadsafe(self._settings_changed)

    def _settings_changed(self) -> None:
        if self.start_recompute():
            log.info("Fahrverhalten wird nach geänderten Einstellungen neu berechnet")
        else:
            self._again = True

    async def recompute(self) -> bool:
        """Rechnet alle Autos neu; ``False``, wenn schon ein Lauf läuft."""
        if self._running:
            return False
        self._running = True
        await self._run_claimed()
        return True

    def start_recompute(self) -> bool:
        """Rechnet im Hintergrund neu (Taste „Jetzt neu berechnen“); ``False``, wenn schon ein Lauf
        läuft. Der Lauf gilt sofort als laufend, auch bevor die Aufgabe beginnt. Nur aus der
        Ereignisschleife aufrufen."""
        if self._running:
            return False
        self._running = True
        task = asyncio.get_running_loop().create_task(
            self._run_claimed_logged(), name="driving-recompute"
        )
        self._tasks.add(task)  # ``close`` bricht sie ab, auch bevor sie begonnen hat
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run_claimed_logged(self) -> None:
        try:
            await self._run_claimed()
        except Exception:  # eine Taste darf nichts abreißen
            log.exception("Berechnung des Fahrverhaltens fehlgeschlagen")

    async def _run_claimed(self) -> None:
        """Der Lauf, nachdem ``_running`` gesetzt wurde; danach rufen die Rückmeldungen."""
        self._track()
        try:
            await self._compute_all()
        finally:
            self._running = False
        for callback in self._run_done:
            try:
                callback()
            except Exception:
                log.exception("Rückmeldung nach dem Lauf des Fahrverhaltens fehlgeschlagen")
        if self._again:  # die Einstellungen änderten sich während des Laufs
            self._again = False
            self._settings_changed()

    async def _offload(self, func: Callable[..., Any], *args: Any) -> Any:
        """Rechnet ``func`` in einem Thread; ein Abbruch beendet nur das Warten (``close`` wartet
        auf den Thread)."""
        work = asyncio.ensure_future(asyncio.to_thread(func, *args))
        self._threads.add(work)
        work.add_done_callback(self._thread_done)
        return await asyncio.shield(work)

    def _thread_done(self, work: asyncio.Future[Any]) -> None:
        self._threads.discard(work)
        if not work.cancelled():
            work.exception()  # holt einen Fehler ab, auf den nach einem Abbruch niemand wartet

    async def close(self, timeout_s: float) -> bool:
        """Beim Herunterfahren: bricht laufende Läufe ab und wartet höchstens ``timeout_s`` auf
        Rechnungen und Schreibzugriffe im Thread. ``True``, wenn keiner mehr läuft."""
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pending = set(self._threads)
        if not pending:
            return True
        done, _ = await asyncio.wait(pending, timeout=timeout_s)
        return len(done) == len(pending)

    async def _compute_all(self) -> None:
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        settings = self._settings.current().settings.learning.driving
        # nur konfigurierte Autos: Einträge der Einstellungen für andere bleiben unbeachtet
        for key in self._cfg.vehicles:
            try:
                await self._compute_vehicle(key, settings, now, today)
            except DrivingDataError as exc:
                log.warning(
                    "Fahrdaten nicht gelesen, der letzte Stand gilt", vehicle=key, error=str(exc)
                )
                self._errors[key] = str(exc)
            except Exception as exc:
                log.exception("Fahrverhalten nicht berechnet, der letzte Stand gilt", vehicle=key)
                self._errors[key] = str(exc) or type(exc).__name__
        log.info(
            "Fahrverhalten berechnet", vehicles=len(self._cfg.vehicles), failed=len(self._errors)
        )

    async def _compute_vehicle(
        self, key: str, settings: DrivingSettings, now: datetime, today: date
    ) -> None:
        start = local_midnight(today - timedelta(days=WINDOW_DAYS))
        history = await load_history(
            self._reader, self._engine, self._cfg, settings, key, start=start, end=now
        )
        records, profile = await self._offload(_build, history, settings, today)
        await self._offload(self._store, profile, now)
        self._profiles[key], self._stored_at[key] = profile, now
        self._errors.pop(key, None)
        try:
            snapshots = await self._offload(self._snapshots, key)
            self._quality[key] = hit_rates(
                records, snapshots, settings.return_reserve_pct, today=today, days=QUALITY_DAYS
            )
        except Exception:  # der Stand ist abgelegt; nur die Trefferquote fehlt
            log.exception("Trefferquote des Fahrverhaltens nicht berechnet", vehicle=key)

    # --- Ablage ---------------------------------------------------------------------------

    def _store(self, profile: VehicleProfile, now: datetime) -> None:
        """Legt den Stand ab und löscht in derselben Transaktion, was älter als 70 Tage ist."""
        with self._engine.begin() as conn:
            conn.execute(
                delete(driving_profile).where(
                    driving_profile.c.computed_at < now - timedelta(days=KEEP_DAYS)
                )
            )
            conn.execute(
                insert(driving_profile).values(
                    vehicle=profile.key,
                    computed_at=now,
                    data_from=profile.data_from,
                    data_to=profile.data_to,
                    source=profile.source,
                    profile=profile.model_dump(mode="json"),
                )
            )

    def _rows(self, key: str, *, newest_first: bool) -> Iterator[tuple[datetime, Any]]:
        order = driving_profile.c.computed_at
        statement = (
            select(driving_profile.c.computed_at, driving_profile.c.profile)
            .where(driving_profile.c.vehicle == key)
            .order_by(order.desc() if newest_first else order)
        )
        with self._engine.connect() as conn:
            for row in conn.execute(statement):
                yield ensure_utc(row.computed_at), row.profile

    def _snapshots(self, key: str) -> list[Snapshot]:
        """Alle lesbaren abgelegten Stände eines Autos, älteste zuerst."""
        result: list[Snapshot] = []
        for computed_at, data in self._rows(key, newest_first=False):
            try:
                result.append((computed_at, VehicleProfile.model_validate(data)))
            except ValidationError:
                log.warning("Ein abgelegter Stand des Fahrverhaltens ist unlesbar", vehicle=key)
        return result

    def _newest(self, key: str) -> Snapshot | None:
        """Der jüngste lesbare Stand; ältere Zeilen werden gar nicht erst geprüft. Nur Zeilen, die
        jünger sind als der gewählte Stand und unlesbar, melden eine Warnung."""
        for computed_at, data in self._rows(key, newest_first=True):
            try:
                return computed_at, VehicleProfile.model_validate(data)
            except ValidationError:
                log.warning("Ein abgelegter Stand des Fahrverhaltens ist unlesbar", vehicle=key)
        return None

    def _latest(self) -> dict[str, Snapshot]:
        found: dict[str, Snapshot] = {}
        for key in self._cfg.vehicles:
            newest = self._newest(key)
            if newest is not None:
                found[key] = newest
        return found

    async def load_stored(self) -> None:
        """Lädt den jüngsten abgelegten Stand je konfiguriertem Auto (beim Start)."""
        try:
            latest = await self._offload(self._latest)
        except Exception:
            log.exception("Abgelegte Stände des Fahrverhaltens nicht gelesen")
            return
        for key, (computed_at, profile) in latest.items():
            self._profiles[key], self._stored_at[key] = profile, computed_at
        log.info("Stände des Fahrverhaltens geladen", vehicles=len(latest))

    # --- Zugriff --------------------------------------------------------------------------

    def profile(self, key: str) -> VehicleProfile | None:
        """Der gültige Stand des Autos mit den Zielen nach den aktuellen Einstellungen."""
        profile = self._profiles.get(key)
        return None if profile is None else with_settings(profile, self._driving_settings())

    def profiles(self) -> Mapping[str, VehicleProfile]:
        """Die gültigen Stände aller Autos wie ``profile``."""
        settings = self._driving_settings()
        return MappingProxyType(
            {key: with_settings(profile, settings) for key, profile in self._profiles.items()}
        )

    def _driving_settings(self) -> DrivingSettings:
        """Ziele gelten nach den aktuellen Einstellungen, nicht nach denen des letzten Laufs
        (Ruling F-2): Automatik, API und HA lesen die Stände nur über ``profile(s)``."""
        return self._settings.current().settings.learning.driving

    # --- Status ---------------------------------------------------------------------------

    def _error(self) -> str | None:
        return "; ".join(dict.fromkeys(self._errors.values())) or None

    def _has_data(self) -> bool:
        return any(profile.days > 0 for profile in self._profiles.values())

    def _last_computed(self) -> datetime | None:
        return max(self._stored_at.values(), default=None)

    def component_status(self) -> ComponentStatus:
        error = self._error()
        if error is not None:
            return ComponentStatus(COMPONENT, False, f"Fahrverhalten fehlgeschlagen: {error}")
        if not self._has_data():
            return ComponentStatus(COMPONENT, True, "Noch keine Fahrdaten")
        return ComponentStatus(COMPONENT, True, "Fahrverhalten aktuell")

    def _status_text(self) -> str:
        error, computed = self._error(), self._last_computed()
        if error is not None:
            text = f"Fahrverhalten fehlgeschlagen: {error}"
            if computed is None:
                return text
            return f"{text.rstrip('.')}. Es gilt der Stand vom {_stamp(computed)}."
        if computed is None or not self._has_data():
            return "Noch keine Fahrdaten."
        return f"Fahrverhalten berechnet am {_stamp(computed)}."

    def status(self) -> dict[str, Any]:
        """Zustand für ``GET /api/learning/driving`` (Präzisierung 11, ohne ``automation``)."""
        now = self._clock.now()
        computed = self._last_computed()
        return {
            "computed_at": ensure_utc(computed).isoformat() if computed is not None else None,
            "running": self._running,
            "status": {"ok": self.component_status().ok, "text": self._status_text()},
            "vehicles": [self._vehicle_json(key, now) for key in self._cfg.vehicles],
        }

    def _vehicle_json(self, key: str, now: datetime) -> dict[str, Any]:
        name = self._cfg.vehicles[key].name
        profile = self.profile(key)
        quality = _quality_json(self._quality.get(key, HitRates(0, 0, 0, 0)))
        if profile is None:  # ein Auto ohne Stand fehlt nicht: keine Tage, leere Wochentage
            return {
                "key": key,
                "name": name,
                "source": None,
                "data_from": None,
                "data_to": None,
                "days": 0,
                "weekdays": [],
                "next_trip": None,
                "presence": [],
                "quality": quality,
            }
        trip = next_trip(profile, now)
        first = slot_start(now)
        slots = [first + SLOT * i for i in range(PRESENCE_HOURS * 4)]
        return {
            "key": key,
            "name": name,
            "source": profile.source,
            "data_from": ensure_utc(profile.data_from).isoformat(),
            "data_to": ensure_utc(profile.data_to).isoformat(),
            "days": profile.days,
            "weekdays": [_weekday_json(wp) for wp in profile.weekdays],
            "next_trip": trip.model_dump(mode="json") if trip is not None else None,
            "presence": [
                {"start": slot.isoformat(), "p": round(presence_at(profile, slot), 2)}
                for slot in slots
            ],
            "quality": quality,
        }
