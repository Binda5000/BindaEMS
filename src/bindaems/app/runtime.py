"""Laufzeit der app (Phase 1b, nur lesend): baut alle Dienste, die HTTP-API und die
Hintergrundaufgaben und fährt sie geordnet herunter.

Datenbankarbeit aus Hintergrundaufgaben läuft über ``asyncio.to_thread``; jede Aufgabe fängt
Fehler ab, protokolliert sie und läuft weiter.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Generator
from datetime import timedelta
from functools import partial

import httpx
import structlog
import uvicorn
from fastapi import FastAPI

from bindaems.app.api.live import live_router
from bindaems.app.api.server import create_app
from bindaems.app.audit import AuditLog
from bindaems.app.auth.routes import audit_router, auth_router, users_router
from bindaems.app.auth.service import AuthService
from bindaems.app.auth.web import Guard
from bindaems.app.backup import backup_database
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.routes import consumers_router
from bindaems.app.consumers.service import ConsumerService
from bindaems.app.consumers.values import HaRef, HaValueCache
from bindaems.app.core_link import CoreClient, LiveFeed, LiveState
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database
from bindaems.app.forecast.routes import forecast_router
from bindaems.app.forecast.service import ForecastService
from bindaems.app.ha.entities import HaInputs, build_entities, build_inputs
from bindaems.app.ha.publisher import AiomqttHaTransport, HaPublisher
from bindaems.app.history.influx import InfluxQueryError, InfluxReader
from bindaems.app.history.routes import history_router
from bindaems.app.history.series import build_catalog
from bindaems.app.ledger.routes import ledger_router
from bindaems.app.ledger.service import LedgerService
from bindaems.app.prices.pipeline import PricePipeline
from bindaems.app.prices.routes import prices_router
from bindaems.app.prices.scheduler import PriceScheduler
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.sources import ReferenceSource, SmartEnergySource, make_reference
from bindaems.app.prices.store import PriceStore
from bindaems.app.schedule import next_local
from bindaems.app.settings.routes import settings_router
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus, StatusSource
from bindaems.shared.config import Config, Secrets
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
from bindaems.shared.timeutil import Clock, SystemClock

log = structlog.get_logger(__name__)

SPOOL_MAX_BYTES = 50 * 1024 * 1024
SPOOL_MAX_AGE = timedelta(days=30)
BACKFILL_FIRST = timedelta(days=7)
BACKFILL_HOURLY = timedelta(hours=6)
HOUR_S = 3600.0
HA_VALUES_S = 15.0
ENERGY_S = 60.0  # Energie der Verbraucher seit Mitternacht
HA_INPUTS_S = 5.0
RESTART_S = 10.0
BACKUP_AT = (3, 15)  # Ortszeit


class _AppServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield  # SIGTERM/SIGINT behandelt die Laufzeit (geordnetes Herunterfahren)


class LoopSink:
    """Leitet Punkte aus Worker-Threads in die Ereignisschleife (``InfluxWriter`` ist nicht
    threadsicher; Preise und Abrechnung schreiben aus ``asyncio.to_thread``)."""

    def __init__(self, sink: PointSink) -> None:
        self._sink = sink
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self._loop_thread = loop, threading.get_ident()

    def write(self, point: Point) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or threading.get_ident() == self._loop_thread:
            self._sink.write(point)
        else:
            loop.call_soon_threadsafe(self._sink.write, point)


class AppRuntime:
    def __init__(
        self,
        cfg: Config,
        secrets: Secrets,
        *,
        clock: Clock | None = None,
        serve: bool = True,
    ) -> None:
        self._cfg = cfg
        self._clock = clock or SystemClock()
        self._serve = serve
        self._db_path = cfg.app.data_dir / DB_FILENAME
        self.engine = open_database(self._db_path)
        migrate(self.engine)
        self.audit = AuditLog(self.engine, self._clock)
        self.auth = AuthService(self.engine, self._clock, self.audit)
        self.settings = SettingsService(self.engine, self._clock, self.audit)
        ha_database = cfg.influxdb.ha_database
        self.consumers = ConsumerService(
            self.engine, self._clock, self.audit, ha_enabled=ha_database is not None
        )

        self._http = httpx.AsyncClient()  # Preisquellen, Open-Meteo, InfluxDB
        self._core_http = httpx.AsyncClient(trust_env=False)  # core nie über einen Proxy
        self.live = LiveState()
        spool = DiskSpool(cfg.app.data_dir / "spool", SPOOL_MAX_BYTES, SPOOL_MAX_AGE, self._clock)
        self.influx_writer = InfluxWriter(cfg.influxdb, secrets.influx_password, self._http, spool)
        self._sink = LoopSink(self.influx_writer)
        self.reader = InfluxReader(cfg.influxdb, secrets.influx_password, self._http)

        self.price_store = PriceStore(self.engine, self._clock)
        self.prices = PriceService(self.price_store, self.settings)
        self.pipeline = PricePipeline(
            self.price_store,
            SmartEnergySource(self._http, self._clock),
            self._reference_source,
            self.settings,
            self.prices,
            self._clock,
            self._sink,
        )
        self.price_scheduler = PriceScheduler(self.pipeline, self.price_store, self._clock)
        self.forecast = ForecastService(self._http, cfg, self.settings, self._clock, self._sink)
        self.ledger = LedgerService(
            self.engine, self._clock, self.prices, self.settings, self.audit, self._sink
        )
        self.live_feed = LiveFeed(
            CoreClient(cfg.app.core_url, secrets.internal_token, self._core_http),
            self.live,
            self._clock,
            slot_flows_handlers=[self.ledger.handle_stream_message],
        )
        self.ha_values = HaValueCache(self.reader, ha_database) if ha_database is not None else None
        self.energy = EnergyCache(self.reader, self._clock, ha_database)
        self.ha_publisher: HaPublisher | None = None
        self._ha_inputs: HaInputs | None = None
        self._ha_ready = asyncio.Event()  # erste Werte für HA bestimmt
        mqtt = cfg.homeassistant.mqtt if cfg.homeassistant is not None else None
        if mqtt is not None:
            self.ha_publisher = HaPublisher(
                mqtt,
                build_entities(cfg),
                self._current_ha_inputs,
                transport_factory=partial(AiomqttHaTransport, mqtt, secrets.ha_mqtt_password),
            )
        self.settings.on_change(lambda _version: self.pipeline.republish())

        sources: list[StatusSource] = [
            self.live_feed.component_status,
            self.pipeline.component_status,
            self.forecast.component_status,
            self.ledger.component_status,
            self._influx_status,
        ]
        if self.ha_publisher is not None:
            sources.append(self.ha_publisher.component_status)
        guard = Guard(self.auth, cookie_secure=cfg.app.cookie_secure)
        self.app: FastAPI = create_app(
            [
                auth_router(self.auth, guard),
                users_router(self.auth, guard),
                audit_router(self.audit, guard),
                settings_router(self.settings, cfg, guard),
                live_router(self.live, guard, sources, self._warnings),
                history_router(
                    self.reader, build_catalog(cfg), self._clock, guard, self._series_labels
                ),
                consumers_router(
                    self.consumers, self.live, self.ha_values, self.reader, cfg, guard, self.energy
                ),
                prices_router(self.prices, self.pipeline, self._clock, guard),
                forecast_router(self.forecast, self._clock, guard),
                ledger_router(self.ledger, guard),
            ],
            ui_dir=cfg.app.ui_dir,
        )
        self._server: _AppServer | None = None
        self._tasks: list[asyncio.Task[None]] = []

    # --- Status ---------------------------------------------------------------------------

    def _reference_source(self, name: str) -> ReferenceSource:
        return make_reference(
            "awattar" if name == "awattar" else "energy_charts", self._http, self._clock
        )

    def _series_labels(self) -> dict[str, str]:
        """Eigene Namen der Ladestationen für den Verlauf."""
        names = self.settings.current().settings.wallbox_names
        return {f"wallbox.{key}": name for key, name in names.items()}

    def _warnings(self) -> list[str]:
        return self.settings.warnings() + self.auth.warnings()

    def _influx_status(self) -> ComponentStatus:
        stats = self.influx_writer.stats()
        if stats.spool_bytes == 0:
            return ComponentStatus("influx", True, "InfluxDB: alles übertragen")
        kib = stats.spool_bytes / 1024
        return ComponentStatus(
            "influx", False, f"InfluxDB nicht erreichbar: {kib:.0f} KiB warten im Puffer"
        )

    def _current_ha_inputs(self) -> HaInputs:
        if self._ha_inputs is None:  # der Publisher startet erst nach den ersten Werten
            raise RuntimeError("Werte für Home Assistant noch nicht bestimmt")
        return self._ha_inputs

    def _build_ha_inputs(self) -> HaInputs:
        return build_inputs(
            self.live, self.prices, self.price_store, self.forecast, self._clock.now()
        )

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        self._sink.bind(asyncio.get_running_loop())
        try:
            await asyncio.to_thread(self.pipeline.restore)
        except Exception:
            log.exception("Brutto/Netto aus gespeicherten Preisen nicht bestimmbar")
        jobs: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("core-link", self.live_feed.run),
            ("prices", self.price_scheduler.run),
            ("forecast", self.forecast.run),
            ("influx-writer", self.influx_writer.run),
            ("ledger", self._ledger_loop),
            ("maintenance", self._maintenance_loop),
            ("backup", self._backup_loop),
            ("consumer-energy", self._energy_loop),
        ]
        if self.ha_values is not None:
            jobs.append(("ha-values", partial(self._ha_values_loop, self.ha_values)))
        if self.ha_publisher is not None:
            jobs += [
                ("ha-inputs", self._ha_inputs_loop),
                ("ha", partial(self._ha_loop, self.ha_publisher)),
            ]
        async with asyncio.TaskGroup() as group:
            self._tasks = [
                group.create_task(self._supervise(name, job), name=name) for name, job in jobs
            ]
            if self._serve:
                self._tasks.append(group.create_task(self._serve_http(), name="http"))

    async def _supervise(self, name: str, job: Callable[[], Awaitable[None]]) -> None:
        """Startet eine Hintergrundaufgabe nach einem unerwarteten Ende neu."""
        while True:
            try:
                await job()
                log.warning("Hintergrundaufgabe beendet", task=name)
            except Exception:
                log.exception("Hintergrundaufgabe fehlgeschlagen", task=name)
            await asyncio.sleep(RESTART_S)

    async def _serve_http(self) -> None:
        trusted = self._cfg.app.trusted_proxies
        config = uvicorn.Config(
            self.app,
            host=self._cfg.app.host,
            port=self._cfg.app.port,
            proxy_headers=bool(trusted),
            forwarded_allow_ips=",".join(trusted) if trusted else None,
            log_config=None,
            access_log=False,
            lifespan="off",
        )
        self._server = _AppServer(config)
        await self._server.serve()

    async def _ledger_loop(self) -> None:
        """Beim Start die letzten 7 Tage nachtragen, danach stündlich die letzten 6 h."""
        window = BACKFILL_FIRST
        while True:
            now = self._clock.now()
            try:
                taken = await self.ledger.backfill(self.reader, now - window, now)
                priced = await asyncio.to_thread(self.ledger.fill_missing_prices)
                if taken or priced:
                    log.info("Abrechnung nachgetragen", slots=taken, prices=priced)
                window = BACKFILL_HOURLY  # erst nach Erfolg kürzer
            except InfluxQueryError as exc:
                log.warning("Nachtrag der Abrechnung nicht möglich", error=str(exc))
            except Exception:
                log.exception("Nachtrag der Abrechnung fehlgeschlagen")
            await asyncio.sleep(HOUR_S)

    async def _ha_values_loop(self, cache: HaValueCache) -> None:
        while True:
            try:
                consumers = await asyncio.to_thread(self.consumers.list)
                refs = [HaRef.from_consumer(c) for c in consumers if c.source_kind == "ha"]
                await cache.refresh(refs)
            except Exception:
                log.exception("HA-Werte der Verbraucher nicht lesbar")
            await asyncio.sleep(HA_VALUES_S)

    async def _energy_loop(self) -> None:
        while True:
            try:
                consumers = await asyncio.to_thread(self.consumers.list)
                await self.energy.refresh(consumers)
            except Exception:
                log.exception("Energie der Verbraucher nicht bestimmbar")
            await asyncio.sleep(ENERGY_S)

    async def _ha_inputs_loop(self) -> None:
        """Hält die Werte für Home Assistant aktuell (Datenbank im Worker-Thread)."""
        while True:
            try:
                self._ha_inputs = await asyncio.to_thread(self._build_ha_inputs)
                self._ha_ready.set()
            except Exception:
                log.exception("Werte für Home Assistant nicht bestimmbar")
            await asyncio.sleep(HA_INPUTS_S)

    async def _ha_loop(self, publisher: HaPublisher) -> None:
        """Verbindet erst, wenn die ersten Werte bestimmt sind."""
        await self._ha_ready.wait()
        await publisher.run()

    async def _maintenance_loop(self) -> None:
        while True:
            try:
                removed = await asyncio.to_thread(self.auth.cleanup)
                if removed:
                    log.info("Abgelaufene Sitzungen gelöscht", sessions=removed)
            except Exception:
                log.exception("Pflege der Sitzungen fehlgeschlagen")
            await asyncio.sleep(HOUR_S)

    async def _backup_loop(self) -> None:
        while True:
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == BACKUP_AT)
            await asyncio.sleep((due - now).total_seconds())
            try:
                path = await asyncio.to_thread(
                    backup_database, self._db_path, self._cfg.app.backup_dir, self._clock.now()
                )
                log.info("Datenbank gesichert", path=str(path))
            except Exception:
                log.exception("Sicherung der Datenbank fehlgeschlagen")

    async def shutdown(self) -> None:
        """Server und Hintergrundaufgaben beenden, Puffer schreiben, Verbindungen schließen."""
        if self._server is not None:
            self._server.should_exit = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        try:
            await self.influx_writer.flush_now()
        except Exception:
            log.exception("Puffer für InfluxDB nicht geschrieben")
        await self._http.aclose()
        await self._core_http.aclose()
        self.engine.dispose()
