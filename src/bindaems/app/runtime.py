"""Laufzeit der app (Phase 1b, nur lesend): baut alle Dienste, die HTTP-API und die
Hintergrundaufgaben und fährt sie geordnet herunter.

Datenbankarbeit aus Hintergrundaufgaben läuft über ``asyncio.to_thread``; jede Aufgabe fängt
Fehler ab, protokolliert sie und läuft weiter.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

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
from bindaems.app.charging.routes import charging_router
from bindaems.app.charging.sessions import ChargingSession, SessionService
from bindaems.app.charging.tracker import ChargingTracker
from bindaems.app.consumers.days import FILL_DAYS, ConsumerDays
from bindaems.app.consumers.energy import EnergyCache
from bindaems.app.consumers.routes import consumers_router
from bindaems.app.consumers.service import ConsumerService
from bindaems.app.consumers.values import HaRef, HaValueCache
from bindaems.app.control.decisions import DecisionCollector, DecisionStore
from bindaems.app.control.intent import CoreDown, IntentService
from bindaems.app.control.routes import control_router
from bindaems.app.core_link import CoreClient, LiveFeed, LiveState
from bindaems.app.db.engine import DB_FILENAME, migrate, open_database
from bindaems.app.forecast.archive import ForecastArchive
from bindaems.app.forecast.calibration import pv_threshold_w
from bindaems.app.forecast.load import LoadForecastService
from bindaems.app.forecast.quality import ForecastQuality
from bindaems.app.forecast.routes import forecast_router
from bindaems.app.forecast.service import ForecastService
from bindaems.app.ha.commands import HaCommandHandler, LogRejections
from bindaems.app.ha.entities import HaInputs, build_entities, build_inputs, support_of
from bindaems.app.ha.notify import AlarmNotifications, HaNotifier, websocket_service_call
from bindaems.app.ha.publisher import AiomqttHaTransport, HaPublisher
from bindaems.app.history.influx import InfluxQueryError, InfluxReader
from bindaems.app.history.routes import history_router
from bindaems.app.history.series import build_catalog
from bindaems.app.ledger.checks import LedgerChecks
from bindaems.app.ledger.costing import LedgerCosting
from bindaems.app.ledger.estimate import GapEstimator
from bindaems.app.ledger.routes import ledger_router
from bindaems.app.ledger.service import LedgerService
from bindaems.app.planning.routes import planning_router
from bindaems.app.planning.sender import PlanSender
from bindaems.app.planning.service import PlanningService
from bindaems.app.planning.store import PlanStore
from bindaems.app.prices.pipeline import PricePipeline
from bindaems.app.prices.routes import prices_router
from bindaems.app.prices.scheduler import PriceScheduler
from bindaems.app.prices.service import PriceService
from bindaems.app.prices.sources import ReferenceSource, SmartEnergySource, make_reference
from bindaems.app.prices.store import PriceStore
from bindaems.app.schedule import next_local
from bindaems.app.settings.routes import settings_router
from bindaems.app.settings.service import SettingsService
from bindaems.app.stats.routes import stats_router
from bindaems.app.stats.service import StatsService, TodayStats
from bindaems.app.status import ComponentStatus, StatusSource
from bindaems.shared.config import Config, Secrets
from bindaems.shared.control import Intent
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.influx.spool import DiskSpool
from bindaems.shared.influx.writer import InfluxWriter
from bindaems.shared.timeutil import LOCAL_TZ, Clock, SystemClock, local_day_slots

log = structlog.get_logger(__name__)

SPOOL_MAX_BYTES = 50 * 1024 * 1024
SPOOL_MAX_AGE = timedelta(days=30)
BACKFILL_HOURLY = timedelta(hours=6)
HOUR_S = 3600.0
HA_VALUES_S = 15.0
ENERGY_S = 60.0  # Energie der Verbraucher seit Mitternacht
HA_INPUTS_S = 5.0
STATS_S = 15 * 60.0  # Statistik für HA: bei jedem neuen Slot und spätestens alle 15 min
STATS_SESSION_DAYS = 90  # so weit zurück sucht HA die letzte Ladesession eines Fahrzeugs
RESTART_S = 10.0
COSTING_STOP_S = 5.0  # so lange wartet das Beenden auf den Block einer laufenden Nachführung
BACKUP_AT = (3, 15)  # Ortszeit
FORECAST_NIGHTLY_AT = (2, 30)  # Ortszeit
FORECAST_KEEP = timedelta(days=60)
LEDGER_NIGHTLY_AT = (2, 45)  # Ortszeit, nach der Prognose um 02:30
LEDGER_CHECK_START_DAYS = 35  # Abgleich beim Start: die letzten 35 vollständigen Tage
LEDGER_CHECK_NIGHTLY_DAYS = 7  # nachts: die letzten 7
CONSUMER_DAYS_NIGHTLY = 3  # nachts: fehlende Tage der letzten 3 (gestern, Fehler nachholen)
LEDGER_ESTIMATE_DAYS = 7  # vor dem Abgleich: Lücken der letzten 7 Tage nachrechnen
LEDGER_BACKFILL_WAIT_S = (
    600.0  # so lange wartet der Abgleich beim Start höchstens auf den ersten Nachtrag
)
FORECAST_QUALITY_START: tuple[int, ...] = (
    0,
    1,
    2,
)  # beim Start: heute, gestern, vorgestern (Tage zurück)
FORECAST_QUALITY_NIGHTLY: tuple[int, ...] = (1, 2)  # nachts: gestern und vorgestern
FORECAST_CALIBRATION_WAIT_S = (
    60.0  # so lange wartet der erste PV-Abruf höchstens auf die Kalibrierung
)


@dataclass(frozen=True)
class StatsInputs:
    """Zwischenspeicher der Statistik-Werte für Home Assistant (nicht im 5-s-Takt gerechnet)."""

    stats: TodayStats | None
    sessions: Mapping[str, ChargingSession | None]  # je Fahrzeug die laufende oder letzte
    month_kwh: Mapping[str, float | None]  # je Fahrzeug die Ladeenergie im Monat


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
        self.forecast_archive = ForecastArchive(self.engine)
        self.forecast = ForecastService(
            self._http, cfg, self.settings, self._clock, self._sink, self.forecast_archive
        )
        self.ledger = LedgerService(
            self.engine, self._clock, self.prices, self.settings, self.audit, self._sink
        )
        self.costing = LedgerCosting(
            self.engine, self.ledger, self.settings, usable_kwh=cfg.battery.usable_kwh
        )
        self.ledger.on_slots_changed(self.costing.mark_dirty)
        # weckt die Nachführung des Einstandspreises (``_costing_loop``); gesetzt, damit sie beim
        # Start rechnet, was ``LedgerCosting`` als veraltet erkannt hat
        self._costing_wake = asyncio.Event()
        self._costing_wake.set()
        self._costing_waiter: asyncio.AbstractEventLoop | None = None  # Loop von ``_costing_loop``
        self.costing.on_dirty(self._wake_costing)  # auch Neubewerten und Verschleiß wecken sie
        self.ledger_checks = LedgerChecks(self.engine, self.ledger, self.reader, self._clock, cfg)
        self.gap_estimator = GapEstimator(self.ledger, self.reader, self._clock, cfg)
        self.load_forecast = LoadForecastService(
            self.ledger, cfg, self.reader, self.forecast_archive, self._clock, self._sink
        )
        self.plan_store = PlanStore(self.engine)
        self.forecast_quality = ForecastQuality(
            self.engine, self.forecast_archive, self.ledger, pv_threshold_w(cfg)
        )
        self._forecast_calibrated = asyncio.Event()  # gesetzt nach der ersten Kalibrierung
        self._ledger_backfilled = asyncio.Event()  # gesetzt nach dem ersten Nachtrag (auch Fehler)
        # Zeitraum, den der Nachtrag lückenlos gelesen hat; nur dort schätzt die Nachrechnung
        self._backfill_covered: tuple[datetime, datetime] | None = None
        core_client = CoreClient(cfg.app.core_url, secrets.internal_token, self._core_http)
        self.decisions = DecisionStore(self.engine, self._clock)
        self.collector = DecisionCollector(self.decisions, core_client, self._sink, self._clock)
        self.live_feed = LiveFeed(
            core_client,
            self.live,
            self._clock,
            slot_flows_handlers=[self._on_slot_flows],
            decision_handlers=[self.collector.on_decision],
            connect_handlers=[self.collector.catch_up],
        )
        self.intents = IntentService(
            core_client, self.live, self.live_feed, self.audit, self._clock, cfg
        )
        # nach der Lastprognose und dem core-Zustand; hört selbst auf geänderte Einstellungen
        self.planning = PlanningService(
            cfg,
            self.settings,
            self.prices,
            self.forecast,
            self.load_forecast,
            self.ledger,
            self.live,
            self.plan_store,
            self._clock,
            support_pct=self._support_soc_pct,
        )
        # nach Planung und Absicht; hört selbst auf neue Pläne und geänderte Einstellungen
        self.plan_sender = PlanSender(
            self.intents, self.planning, self.settings, self.live, self._clock
        )
        self.live_feed.connect_handlers.append(self._core_connected)
        self.live_feed.health_handlers.append(self.plan_sender.wake)
        # Steckzustand und Belegung nach jeder Abfrage von Health (Plan 4a, Task 3)
        self.charging = ChargingTracker(self.engine, self._clock, cfg, self.live)
        self.live_feed.health_handlers.append(self.charging.wake)
        self.charging_sessions = SessionService(
            self.charging, self.ledger, self.costing, self._clock
        )
        self.ha_values = HaValueCache(self.reader, ha_database) if ha_database is not None else None
        self.energy = EnergyCache(self.reader, self._clock, ha_database)
        self.consumer_days = ConsumerDays(self.engine, self.energy, self.consumers, self._clock)
        self.stats = StatsService(
            self.ledger,
            self.charging_sessions,
            self.consumers,
            self.consumer_days,
            self.energy,
            self.ledger_checks,
            self.settings,
            cfg,
            self._clock,
        )
        self.ha_publisher: HaPublisher | None = None
        self._ha_inputs: HaInputs | None = None
        self._stats_inputs = StatsInputs(None, {}, {})
        self._stats_day: date | None = None  # Ortstag der letzten gelungenen Rechnung von heute
        self._stats_wake = asyncio.Event()  # ein neuer Slot (aus jedem Thread) weckt die Statistik
        self._stats_loop_ref: asyncio.AbstractEventLoop | None = None
        self._ha_ready = asyncio.Event()  # erste Werte für HA bestimmt
        mqtt = cfg.homeassistant.mqtt if cfg.homeassistant is not None else None
        self.ha_notifier: HaNotifier | None = None
        self.alarm_notifications: AlarmNotifications | None = None
        self._notify_status: ComponentStatus | None = None
        if cfg.homeassistant is not None:
            if secrets.ha_token is not None:
                self.ha_notifier = HaNotifier(
                    websocket_service_call(cfg.homeassistant.url, secrets.ha_token),
                    cfg.homeassistant.notify_service,
                    self._clock,
                )
                self.alarm_notifications = AlarmNotifications(
                    self.live, self.ha_notifier, self._clock
                )
            else:
                self._notify_status = ComponentStatus(
                    "ha-notify", False, "Benachrichtigungen aus: BINDAEMS_HA_TOKEN fehlt."
                )
        if mqtt is not None:
            self.ha_publisher = HaPublisher(
                mqtt,
                build_entities(cfg),
                self._current_ha_inputs,
                transport_factory=partial(AiomqttHaTransport, mqtt, secrets.ha_mqtt_password),
                commands=HaCommandHandler(
                    cfg,
                    self.intents,
                    self.ha_notifier or LogRejections(),
                    self._clock,
                    self.planning,
                ),
            )
            self.ledger.on_slots_changed(self._on_stats_slots)
        self.settings.on_change(lambda _version: self.pipeline.republish())

        sources: list[StatusSource] = [
            self.live_feed.component_status,
            self.pipeline.component_status,
            self.forecast.component_status,
            self.load_forecast.component_status,
            self.planning.component_status,
            self.ledger.component_status,
            self._influx_status,
        ]
        if self.ha_publisher is not None:
            sources.append(self.ha_publisher.component_status)
        if self._notify_status is not None:
            status = self._notify_status
            sources.append(lambda: status)
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
                forecast_router(
                    self.forecast, self.load_forecast, self.forecast_quality, self._clock, guard
                ),
                ledger_router(self.ledger, guard, self.ledger_checks),
                charging_router(self.charging_sessions, cfg, self.settings, guard),
                stats_router(self.stats, guard),
                planning_router(
                    self.planning,
                    self.settings,
                    self.live,
                    self._clock,
                    guard,
                    ledger=self.ledger,
                    store=self.plan_store,
                ),
                control_router(
                    self.intents,
                    self.decisions,
                    core_client,
                    self.auth,
                    self.audit,
                    guard,
                    settings=self.settings,
                    ledger=self.ledger,
                    cfg=cfg,
                    clock=self._clock,
                    live=self.live,
                ),
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

    async def _core_connected(self) -> None:
        """Nach jeder Verbindung: Health sofort abfragen, dann den Plan abgleichen.

        Ohne Health, dem die app traut, sendet der Sender nicht; Health der alten Verbindung hat
        ``LiveFeed`` bei der Trennung verworfen. Jede Abfrage von Health stößt ihn wieder an.
        """
        self.live_feed.refresh_health()
        await self.plan_sender.connected()

    def _support_soc_pct(self) -> float:
        """Unterstützungsschwelle für die Planung: die der Absicht, sonst die Konfiguration."""
        cached = self.intents.cached()
        if cached is not None and cached.support.soc_pct is not None:
            return cached.support.soc_pct
        return self._cfg.control.support_soc_pct

    def _current_ha_inputs(self) -> HaInputs:
        if self._ha_inputs is None:  # der Publisher startet erst nach den ersten Werten
            raise RuntimeError("Werte für Home Assistant noch nicht bestimmt")
        cached = self.intents.cached()  # nach einem Befehl gleich die neue Absicht zeigen
        if cached is None or cached == self._ha_inputs.intent:
            return self._ha_inputs
        allowed, soc_pct = support_of(cached, self._cfg.control.support_soc_pct)
        return replace(
            self._ha_inputs, intent=cached, support_allowed=allowed, support_soc_pct=soc_pct
        )

    def _build_ha_inputs(self, intent: Intent | None) -> HaInputs:
        """Läuft im Worker-Thread (``_ha_inputs_loop``): die Datenbankabfrage blockiert nicht."""
        now = self._clock.now()
        settings = self.settings.current().settings
        cached = self._stats_inputs  # Statistik: nur der Cache, keine Abfrage im 5-s-Takt
        return build_inputs(
            self.live,
            self.prices,
            self.price_store,
            self.forecast,
            now,
            intent,
            settings.wallbox_names,
            self.ledger.month_peak(now.astimezone(LOCAL_TZ).date()),
            self.planning.current(),
            settings.planning.grid_charge_allowed,
            support_default_pct=self._cfg.control.support_soc_pct,
            stats=cached.stats,
            sessions=cached.sessions,
            month_kwh=cached.month_kwh,
        )

    async def _ha_intent(self) -> Intent | None:
        """Die Absicht für HA: ohne gültige die Basis, bei Störung die zuletzt bekannte."""
        try:
            return await self.intents.current() or Intent(version=1)
        except CoreDown:
            return self.intents.cached()
        except Exception:
            log.exception("Absicht für Home Assistant nicht bestimmbar")
            return self.intents.cached()

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        self._sink.bind(asyncio.get_running_loop())
        try:
            await asyncio.to_thread(self.pipeline.restore)
        except Exception:
            log.exception("Brutto/Netto aus gespeicherten Preisen nicht bestimmbar")
        jobs: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("core-link", self.live_feed.run),
            ("decisions", self.collector.run),
            ("prices", self.price_scheduler.run),
            ("forecast", self._forecast_run),
            ("load-forecast", self.load_forecast.run),
            ("planning", self.planning.run),
            ("plan-sender", self.plan_sender.run),
            ("charging", self.charging.run),
            ("influx-writer", self.influx_writer.run),
            ("ledger", self._ledger_loop),
            ("costing", self._costing_loop),
            ("maintenance", self._maintenance_loop),
            ("backup", self._backup_loop),
            ("forecast-nightly", self._forecast_nightly_loop),
            ("ledger-nightly", self._ledger_nightly_loop),
            ("consumer-energy", self._energy_loop),
        ]
        if self.ha_values is not None:
            jobs.append(("ha-values", partial(self._ha_values_loop, self.ha_values)))
        if self.ha_publisher is not None:
            jobs += [
                ("ha-stats", self._stats_loop),
                ("ha-inputs", self._ha_inputs_loop),
                ("ha", partial(self._ha_loop, self.ha_publisher)),
            ]
        if self.alarm_notifications is not None:
            jobs.append(("ha-notify", self.alarm_notifications.run))
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

    async def _on_slot_flows(self, data: dict[str, Any]) -> None:
        """Speichert den Slot; der Einstandspreis folgt im eigenen Job (``_costing_loop``), damit
        eine lange Neuberechnung den Live-Strom des core nicht aufhält. Geweckt wird er über
        ``LedgerCosting.on_dirty``, sobald der Slot gespeichert ist."""
        await self.ledger.handle_stream_message(data)

    def _wake_costing(self) -> None:
        """Weckt die Nachführung; was neu zu rechnen ist, hat ``LedgerCosting.mark_dirty``. Aus
        einem Worker-Thread (Route, Einstellungen, ``to_thread``) über den Loop des Jobs."""
        loop = self._costing_waiter
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            self._costing_wake.set()
            return
        try:
            loop.call_soon_threadsafe(self._costing_wake.set)
        except RuntimeError:
            pass  # Loop schon geschlossen (Herunterfahren): nichts mehr nachzuführen

    async def _costing_loop(self) -> None:
        """Führt den Einstandspreis im Worker-Thread nach, sobald etwas ihn weckt. Ein Wecken
        während der Rechnung löst gleich die nächste aus; ``LedgerCosting`` hält mit seinem Lock
        ohnehin nur eine Nachführung zur Zeit."""
        self._costing_waiter = asyncio.get_running_loop()
        while True:
            await self._costing_wake.wait()
            self._costing_wake.clear()
            try:
                await asyncio.to_thread(self.costing.catch_up)
            except Exception:
                log.exception("Einstandspreis des Akkus nicht berechenbar")

    async def _ledger_loop(self) -> None:
        """Beim Start ab Mitternacht vor 7 Tagen nachtragen (mindestens das Fenster der
        Nachrechnung, ``LEDGER_ESTIMATE_DAYS``), danach stündlich die letzten 6 h. Sind
        Nachträge gescheitert, beginnt der nächste am Ende des lückenlos gelesenen Zeitraums
        (höchstens am Anfang des Fensters), damit keine Stunde ungelesen und ungeschätzt bleibt."""
        first = True
        while True:
            now = self._clock.now()
            today = now.astimezone(LOCAL_TZ).date()
            window = local_day_slots(today - timedelta(days=LEDGER_ESTIMATE_DAYS))[0]
            start = window if first else now - BACKFILL_HOURLY
            covered = self._backfill_covered
            if not first and covered is not None:
                start = max(min(start, covered[1]), window)
            try:
                taken = await self.ledger.backfill(self.reader, start, now)
                self._note_backfill(start, now)
                priced = await asyncio.to_thread(self.ledger.fill_missing_prices)
                if taken or priced:
                    log.info("Abrechnung nachgetragen", slots=taken, prices=priced)
                first = False  # erst nach Erfolg kürzer
            except InfluxQueryError as exc:
                log.warning("Nachtrag der Abrechnung nicht möglich", error=str(exc))
            except Exception:
                log.exception("Nachtrag der Abrechnung fehlgeschlagen")
            finally:
                self._ledger_backfilled.set()  # auch nach einem Fehler: der Abgleich läuft trotzdem
            self._wake_costing()
            await asyncio.sleep(HOUR_S)

    def _note_backfill(self, start: datetime, end: datetime) -> None:
        """Merkt sich den lückenlos nachgetragenen Zeitraum; nach einer Lücke beginnt er neu."""
        covered = self._backfill_covered
        if covered is not None and start <= covered[1]:
            self._backfill_covered = (min(covered[0], start), max(covered[1], end))
        else:
            self._backfill_covered = (start, end)

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
                intent = await self._ha_intent()
                self._ha_inputs = await asyncio.to_thread(self._build_ha_inputs, intent)
                self._ha_ready.set()
            except Exception:
                log.exception("Werte für Home Assistant nicht bestimmbar")
            await asyncio.sleep(HA_INPUTS_S)

    def _on_stats_slots(self, _start: object) -> None:
        """Nach neuen oder geänderten Slots (aus jedem Thread): die Statistik neu rechnen."""
        loop = self._stats_loop_ref
        if loop is None or loop.is_closed():
            return  # die Schleife rechnet beim Start ohnehin
        with contextlib.suppress(RuntimeError):  # Schleife gerade geschlossen
            loop.call_soon_threadsafe(self._stats_wake.set)

    async def _stats_loop(self) -> None:
        """Ersparnis, Autarkie und Ladesessions für HA: beim Start, bei jedem neuen Slot und alle
        15 min (``STATS_S``). Die Datenbank wird im Worker-Thread gelesen."""
        self._stats_loop_ref = asyncio.get_running_loop()
        while True:
            self._stats_wake.clear()  # ein Slot während der Rechnung löst gleich die nächste aus
            try:
                await asyncio.to_thread(self._refresh_stats)
            except Exception:
                log.exception("Statistik für Home Assistant nicht berechenbar")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stats_wake.wait(), timeout=STATS_S)

    def _refresh_stats(self) -> None:
        """Rechnet die Statistik neu (blockierend, im Worker-Thread). Jeder Teil, der scheitert,
        behält seinen letzten Wert: die Sensoren zeigen weiter diesen oder „unbekannt“."""
        previous = self._stats_inputs
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        stats = previous.stats
        try:
            stats = self.stats.today()
            self._stats_day = today
        except Exception:
            log.exception("Ersparnis und Autarkie für Home Assistant nicht berechenbar")
            if self._stats_day != today:  # Werte von gestern gelten nicht als „heute“
                stats = None
        sessions = dict(previous.sessions)
        for key in self._cfg.vehicles:
            try:
                # die offene Session gezielt (egal wie alt), sonst die neueste beendete
                found = self.charging_sessions.open_session(key)
                if found is None:
                    latest = self.charging_sessions.sessions(
                        today - timedelta(days=STATS_SESSION_DAYS), today, vehicle=key, limit=1
                    )
                    found = latest[0] if latest else None
                sessions[key] = found
            except Exception:
                log.exception("Ladesession für Home Assistant nicht bestimmbar", vehicle=key)
        month_kwh = dict(previous.month_kwh)
        try:
            split = self.charging_sessions.by_vehicle(local_day_slots(today.replace(day=1))[0], now)
            month_kwh = {key: split[key].kwh if key in split else 0.0 for key in self._cfg.vehicles}
        except Exception:
            log.exception("Ladeenergie im Monat für Home Assistant nicht berechenbar")
        self._stats_inputs = StatsInputs(stats, sessions, month_kwh)

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
            try:
                removed = await self.collector.cleanup(self._cfg.app.decisions_keep_days)
                if removed:
                    log.info("Alte Entscheidungen gelöscht", decisions=removed)
            except Exception:
                log.exception("Pflege der Entscheidungen fehlgeschlagen")
            try:
                deleted, trimmed = await asyncio.to_thread(
                    self.plan_store.cleanup, self._clock.now()
                )
                if deleted or trimmed:
                    log.info("Alte Pläne aufgeräumt", deleted=deleted, trimmed=trimmed)
            except Exception:
                log.exception("Pflege der Pläne fehlgeschlagen")
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

    async def _forecast_run(self) -> None:
        """PV-Prognose erst nach der ersten Kalibrierung abrufen, höchstens eine Minute lang
        warten: sonst entstünde die erste Ausgabe mit dem neutralen Modell und bliebe so im
        Archiv, und eine langsame Datenbank hielte die Prognose nicht auf."""
        try:
            await asyncio.wait_for(
                self._forecast_calibrated.wait(), timeout=FORECAST_CALIBRATION_WAIT_S
            )
        except TimeoutError:
            log.warning("Kalibrierung der PV-Prognose dauert zu lange, der Abruf beginnt ohne sie")
        await self.forecast.run()

    async def _forecast_nightly_loop(self) -> None:
        """Beim Start und täglich um 02:30 Uhr: Güte bewerten, PV kalibrieren, Archiv pflegen."""
        back = FORECAST_QUALITY_START
        while True:
            await self._forecast_nightly(back)
            back = FORECAST_QUALITY_NIGHTLY
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == FORECAST_NIGHTLY_AT)
            await asyncio.sleep((due - now).total_seconds())

    async def _forecast_nightly(self, days_back: tuple[int, ...]) -> None:
        """Güte der Tage ``heute − days_back`` bewerten, dann kalibrieren und das Archiv pflegen."""
        today = self._clock.now().astimezone(LOCAL_TZ).date()
        for back in days_back:
            day: date = today - timedelta(days=back)
            try:
                await asyncio.to_thread(self.forecast_quality.update, day)
            except Exception:
                log.exception("Kennzahlen der Prognosegüte fehlgeschlagen", day=day.isoformat())
        try:
            await asyncio.to_thread(self.forecast.recalibrate, self.ledger)
        except Exception:
            log.exception("Kalibrierung der PV-Prognose fehlgeschlagen")
        finally:
            self._forecast_calibrated.set()  # auch nach einem Fehler: das neutrale Modell gilt
        try:
            removed = await asyncio.to_thread(
                self.forecast_archive.cleanup, self._clock.now() - FORECAST_KEEP
            )
            if removed:
                log.info("Alte Prognosen aus dem Archiv gelöscht", records=removed)
        except Exception:
            log.exception("Pflege des Prognosearchivs fehlgeschlagen")

    async def _ledger_nightly_loop(self) -> None:
        """Beim Start (35 Tage) und täglich um 02:45 Uhr (7 Tage): Lücken nachrechnen, dann die
        Abrechnung gegen die Zähler abgleichen und die Energie der Verbraucher je Tag speichern.

        Der Lauf beim Start wartet auf den ersten Nachtrag der Abrechnung (``_ledger_loop``), sonst
        prüft er Tage, die der Nachtrag noch füllt, und ihre Zeilen blieben bis zum nächsten Start
        veraltet. Länger als ``LEDGER_BACKFILL_WAIT_S`` wartet er nicht.
        """
        try:
            await asyncio.wait_for(self._ledger_backfilled.wait(), timeout=LEDGER_BACKFILL_WAIT_S)
        except TimeoutError:
            log.warning("Nachtrag der Abrechnung dauert zu lange, der Abgleich beginnt ohne ihn")
        days = LEDGER_CHECK_START_DAYS
        start = True
        while True:
            await self._ledger_nightly(days, start=start)
            start = False
            days = LEDGER_CHECK_NIGHTLY_DAYS
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == LEDGER_NIGHTLY_AT)
            await asyncio.sleep((due - now).total_seconds())

    async def _ledger_nightly(self, days: int, *, start: bool = False) -> None:
        """Rechnet die Lücken der letzten 7 Tage nach und gleicht dann die letzten ``days``
        vollständigen Tage (bis gestern) mit den Zählern ab, geschätzte Slots eingeschlossen.
        Danach speichert er die Energie der Verbraucher: nachts die fehlenden Tage der letzten 3
        (gestern, und ein Fehler der Vornacht wird nachgeholt), beim ``start`` die der letzten 31.

        Ein Fehler wird protokolliert; die nächste Nacht versucht es wieder, und jeder Schritt läuft
        auch, wenn ein anderer scheitert.
        """
        today = self._clock.now().astimezone(LOCAL_TZ).date()
        try:
            estimated = await self._estimate_gaps(today)
            if estimated:
                self._wake_costing()  # der Einstandspreis des Akkus folgt den Schätzungen
        except InfluxQueryError as exc:
            log.warning("Nachrechnung der Lücken nicht möglich", error=str(exc))
        except Exception:
            log.exception("Nachrechnung der Lücken fehlgeschlagen")
        try:
            checked = await self.ledger_checks.run(
                today - timedelta(days=days), today - timedelta(days=1)
            )
            log.info("Abrechnung mit den Zählern abgeglichen", days=checked)
        except InfluxQueryError as exc:
            log.warning("Abgleich mit den Zählern nicht möglich", error=str(exc))
        except Exception:
            log.exception("Abgleich mit den Zählern fehlgeschlagen")
        try:
            stored = await self.consumer_days.fill_missing(
                today, FILL_DAYS if start else CONSUMER_DAYS_NIGHTLY
            )
            if stored:
                log.info("Energie der Verbraucher je Tag gespeichert", days=stored)
        except Exception:
            log.exception("Energie der Verbraucher je Tag nicht gespeichert")

    async def _estimate_gaps(self, today: date) -> int:
        """Rechnet die Lücken der letzten 7 Tage bis gestern nach, aber nur in dem Teil, den der
        Nachtrag schon aus InfluxDB gelesen hat: sonst entstünden Schätzungen, wo echte Daten
        liegen (die echten ersetzen sie zwar später, aber nur, wenn ein Nachtrag sie noch sieht)."""
        start = local_day_slots(today - timedelta(days=LEDGER_ESTIMATE_DAYS))[0]
        end = local_day_slots(today)[0]
        covered = self._backfill_covered
        if covered is None:
            log.info("Nachrechnung der Lücken wartet auf den ersten Nachtrag der Abrechnung")
            return 0
        start, end = max(start, covered[0]), min(end, covered[1])
        if end <= start:
            return 0
        return await self.gap_estimator.run(start, end)

    async def shutdown(self) -> None:
        """Server und Hintergrundaufgaben beenden, Puffer schreiben, Verbindungen schließen."""
        if self._server is not None:
            self._server.should_exit = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        # ``to_thread`` des abgebrochenen Jobs ``costing`` rechnet weiter: vor ``dispose`` abwarten
        if not await asyncio.to_thread(self.costing.close, COSTING_STOP_S):
            log.warning("Nachführung des Einstandspreises läuft beim Beenden noch")
        try:
            await self.influx_writer.flush_now()
        except Exception:
            log.exception("Puffer für InfluxDB nicht geschrieben")
        await self._http.aclose()
        await self._core_http.aclose()
        self.engine.dispose()
