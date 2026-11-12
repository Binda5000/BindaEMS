"""PV-Prognose (P50): Abruf je Fläche, Modell, Summe und Veröffentlichung in InfluxDB.

Die Prognose liegt im Speicher; nach einem Neustart wird sie sofort neu abgerufen. Jede Ausgabe geht
zusätzlich ins Archiv (Plan 3a, Präzisierung 7), aus dem später Kalibrierung und Kennzahlen kommen.

Schnee auf den Modulen (Plan „Schnee auf den Modulen“): P25, P50 und P75 werden je Slot mit dem
Faktor aus ``snow_cut`` gekürzt, ``raw_w`` und ``uncut_p50_w`` bleiben ungekürzt. Der Abruf rechnet
die Kürzung mit den zuletzt gelesenen Ist-Werten; ``check_snow`` prüft sie jede Viertelstunde neu
und gibt der Prognose bei einer Änderung eine neue ``revision`` (Entscheidung 8). Ins Archiv geht
nur die Ausgabe des Abrufs. Gespeichert wird nur der Handschalter (``pv_snow_override.json``).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import structlog

from bindaems.app.audit import AuditLog, Source
from bindaems.app.fetch import FetchError
from bindaems.app.forecast.archive import (
    Actual,
    Clearness,
    ForecastArchive,
    ForecastRecord,
    RecordValue,
    SlotSource,
    actuals,
    horizon_class,
)
from bindaems.app.forecast.calibration import (
    FACTOR_RANGE,
    NEUTRAL,
    NEUTRAL_BANDS,
    Bands,
    Calibration,
    CalPair,
    ErrPair,
    apply_band,
    calibrate,
    clearness_class,
    fit_bands,
    outlier_days,
    pv_threshold_w,
)
from bindaems.app.forecast.openmeteo import (
    SOURCE,
    ForecastParseError,
    OpenMeteoUnavailable,
    SnowHour,
    WeatherSlot,
    fetch_plane,
    http_error,
    parse_plane,
    parse_snow,
)
from bindaems.app.forecast.pv_model import combine, plane_power_w
from bindaems.app.forecast.snow import (
    NO_CUT,
    OVERRIDE_MAX,
    Observation,
    Override,
    SnowCut,
    snow_cut,
)
from bindaems.app.history.influx import RP_LONG
from bindaems.app.learning.weather import WeatherArchive
from bindaems.app.schedule import next_local
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config, PvPlane
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ensure_utc, local_day_slots, slot_start

log = structlog.get_logger(__name__)

FETCH_MINUTE = 7
RETRY_S = 600.0
PLANE_ATTEMPTS = 3  # je Fläche und Abruf; kurze Ausfälle (HTTP 503) überbrückt das
PLANE_RETRY_S = 30.0
PLANE_KEEP = timedelta(minutes=30)  # so lange zählt eine Fläche aus einem gescheiterten Abruf
SLOT_HOURS = 0.25
CALIBRATION_WINDOW = timedelta(days=30)
SNOW_WINDOW = timedelta(hours=36)  # so weit zurück liest die Prüfung auf Schnee die Abrechnung
CHANGE_MIN = 0.05  # ab dieser Änderung eines Faktors gibt es eine neue ``revision``
OVERRIDE_ACTION = "forecast.snow_override"
NO_FORECAST = "Noch keine PV-Prognose"
SNOW_INFO = "PV-Prognose gekürzt: vermutlich Schnee auf den Modulen"  # Entscheidung 11


@dataclass(frozen=True)
class PvForecast:
    issued_at: datetime
    source: str
    p50_w: Mapping[datetime, float]
    raw_w: Mapping[datetime, float]  # unkalibriert, aus dem Modell
    p25_w: Mapping[datetime, float]
    p75_w: Mapping[datetime, float]
    clearness: Mapping[datetime, Clearness | None]
    uncut_p50_w: Mapping[datetime, float]  # P50 ohne Kürzung bei Schnee
    revision: datetime  # ``issued_at`` beim Abruf, die Zeit der Prüfung bei neuer Kürzung
    snow: SnowCut = NO_CUT

    def day_energy_kwh(self, day: date) -> float | None:
        """Energie des lokalen Tages aus den vorhandenen Slots; ohne Slots ``None``."""
        values = [self.p50_w[slot] for slot in local_day_slots(day) if slot in self.p50_w]
        return sum(value * SLOT_HOURS / 1000.0 for value in values) if values else None

    def power_at(self, ts: datetime) -> float | None:
        return self.p50_w.get(slot_start(ts))


@dataclass(frozen=True)
class _Issued:
    """Ausgegebene Prognose mit dem, was eine neue Kürzung braucht; wird nur als Ganzes ersetzt
    (``refresh`` in der Event-Loop, ``check_snow`` im Worker-Thread)."""

    forecast: PvForecast
    uncut_p25_w: Mapping[datetime, float]
    uncut_p75_w: Mapping[datetime, float]
    weather: Sequence[SnowHour]  # Schneestunden der Antwort für die erste Fläche
    temperatures: Mapping[datetime, float]


def snow_info(forecast: PvForecast | None, now: datetime) -> list[str]:
    """Hinweis der Übersicht (Stufe ``info``), solange die Anlage jetzt als bedeckt gilt
    (``vermutet`` oder ``gemessen``); „seit“ in Ortszeit, vor heute mit dem Tag. Die Kürzung für
    künftigen Schneefall zeigen Zeitplan und Prognosen, nicht die Übersicht."""
    if forecast is None or forecast.snow.state == "frei":
        return []
    since = forecast.snow.since
    if since is None:
        return [SNOW_INFO]
    local = since.astimezone(LOCAL_TZ)
    shown = f"{local:%H:%M}"
    if local.date() < now.astimezone(LOCAL_TZ).date():
        shown = f"{local:%d.%m.} {shown}"
    return [f"{SNOW_INFO} (seit {shown})"]


def _cut(series: Mapping[datetime, float], cut: SnowCut) -> dict[datetime, float]:
    return {slot: value * cut.factors.get(slot, 1.0) for slot, value in series.items()}


def _snow_changed(old: SnowCut, new: SnowCut, now: datetime) -> bool:
    """Neuer Zustand oder ein Faktor ab dem laufenden Slot um mehr als ``CHANGE_MIN`` anders."""
    if old.state != new.state:
        return True
    first = slot_start(now)
    slots = {s for s in (*old.factors, *new.factors) if s >= first}
    return any(abs(old.factors.get(s, 1.0) - new.factors.get(s, 1.0)) > CHANGE_MIN for s in slots)


class ForecastService:
    def __init__(
        self,
        http: httpx.AsyncClient,
        cfg: Config,
        settings: SettingsService,
        clock: Clock,
        sink: PointSink | None = None,
        archive: ForecastArchive | None = None,
        weather: WeatherArchive | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        audit: AuditLog | None = None,
        override_path: Path | None = None,
    ) -> None:
        self._http = http
        self._cfg = cfg
        self._settings = settings
        self._clock = clock
        self._sink = sink
        self._archive = archive
        self._weather = weather
        self._sleep = sleep
        self._audit = audit
        self._override_path = override_path
        self._issued: _Issued | None = None
        self._error: str | None = None
        # Flächen eines gescheiterten Abrufs: Index → Wetter und Schneestunden (nur die erste
        # Fläche hat welche), mit dem Zeitpunkt des Abrufs
        self._kept: dict[int, tuple[datetime, list[WeatherSlot], list[SnowHour]]] = {}
        # beide Modelle gehören zusammen und werden nur gemeinsam ersetzt
        self._models: tuple[Calibration, Bands] = (NEUTRAL, NEUTRAL_BANDS)
        # Kürzung rechnen und Prognose ersetzen: ``check_snow`` und ``set_snow_override`` laufen in
        # Worker-Threads, ``refresh`` in der Event-Loop; die Sperre gilt nur für Speicher und
        # Rechnen, nie für Datei oder Datenbank (sonst wartete die Loop darauf)
        self._snow_lock = threading.Lock()
        self._override_lock = threading.Lock()  # Datei und Speicher des Knopfs in einer Reihenfolge
        self._actuals: dict[datetime, float] = {}  # Ist-PV (W) der letzten Prüfung, 36 h
        self._override = self._read_override()
        self._first = asyncio.Event()  # gesetzt mit der ersten Prognose
        # InfluxDB schreibt die Loop des Abrufs; ``_published`` zählt die ausgegebenen Prognosen,
        # ein Auftrag aus einem Worker-Thread schreibt nur, wenn keine neuere dazwischenkam
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._published = 0

    def latest(self) -> PvForecast | None:
        issued = self._issued
        return issued.forecast if issued is not None else None

    def temperatures(self) -> Mapping[datetime, float]:
        """Lufttemperatur je Slot aus der letzten erfolgreichen Antwort (leer vor der ersten)."""
        issued = self._issued
        return issued.temperatures if issued is not None else {}

    async def first_forecast(self) -> None:
        """Wartet, bis die erste Prognose vorliegt (ohne Abfrage in einer Schleife)."""
        await self._first.wait()

    async def refresh(self) -> PvForecast | None:
        """Neue Prognose; scheitert eine Fläche, bleibt die bisherige. Schon geholte Flächen
        behält der Dienst bis 30 min, die Wiederholung holt nur die fehlenden."""
        model = self._settings.current().settings.pv_model
        planes: list[dict[datetime, float]] = []
        first_plane: list[WeatherSlot] = []
        first_snow: list[SnowHour] = []
        clearness: dict[datetime, Clearness | None] = {}
        now = self._clock.now()
        self._kept = {
            index: kept
            for index, kept in self._kept.items()
            if timedelta(0) <= now - kept[0] < PLANE_KEEP  # auch nach einem Rücksprung der Uhr
        }
        for index, plane in enumerate(self._cfg.pv.planes):
            kept = self._kept.get(index)
            try:
                weather, snow = (
                    (kept[1], kept[2])
                    if kept is not None
                    else await self._fetch_plane(plane, snow=index == 0)
                )
            except (FetchError, ForecastParseError) as exc:
                self._error = f"PV-Prognose für Fläche {plane.name} fehlgeschlagen: {exc}"
                log.warning(
                    "PV-Prognose für Fläche fehlgeschlagen", plane=plane.name, error=str(exc)
                )
                return self.latest()
            if kept is None:  # der Stempel bleibt der des Abrufs, auch wenn die Fläche wiederkommt
                self._kept[index] = (self._clock.now(), weather, snow)
                if index == 0 and not snow:
                    log.info("Keine Schneedaten von Open-Meteo, Schneeerkennung entfällt")
            if not planes:  # Klarheit, Temperatur und Schnee gelten am Standort, aus der ersten
                first_plane, first_snow = weather, snow
                clearness = {
                    slot.slot_start: clearness_class(slot.ghi_w_m2, slot.diffuse_w_m2)
                    for slot in weather
                }
            planes.append(
                {
                    slot.slot_start: plane_power_w(slot.gti_w_m2, slot.temp_c, plane.kwp, model)
                    for slot in weather
                }
            )
        max_w = self._cfg.pv.inverter_ac_max_w
        raw_w = combine(planes, max_w)
        issued_at = self._clock.now()
        calibration, bands = self._models
        slot_clearness = {slot: clearness.get(slot) for slot in raw_w}
        p50_w: dict[datetime, float] = {}
        p25_w: dict[datetime, float] = {}
        p75_w: dict[datetime, float] = {}
        for slot, slot_raw_w in raw_w.items():
            kind = slot_clearness[slot]
            hour = slot.astimezone(LOCAL_TZ).hour
            p50 = min(max(slot_raw_w * calibration.factor(hour, kind), 0.0), max_w)
            # Slots vor der Ausgabe haben keinen Vorlauf; für sie gilt das kurze Band
            horizon = horizon_class(slot - issued_at) or "h0_6"
            p25_w[slot], p50_w[slot], p75_w[slot] = apply_band(
                p50, bands.band(horizon, kind), max_w
            )
        uncut = PvForecast(
            issued_at, SOURCE, p50_w, raw_w, p25_w, p75_w, slot_clearness, p50_w, issued_at
        )
        temperatures = {slot.slot_start: slot.temp_c for slot in first_plane}
        self._loop, self._loop_thread = asyncio.get_running_loop(), threading.get_ident()
        with self._snow_lock:
            cut = self._snow_cut(issued_at, uncut, first_snow, temperatures)
            forecast = replace(
                uncut,
                p50_w=_cut(p50_w, cut),
                p25_w=_cut(p25_w, cut),
                p75_w=_cut(p75_w, cut),
                snow=cut,
            )
            self._issued = _Issued(forecast, p25_w, p75_w, tuple(first_snow), temperatures)
            self._publish_newest(forecast)
        self._first.set()
        self._error = None
        self._kept = {}  # der nächste Abruf holt wieder alle Flächen
        await self._archive_forecast(forecast)
        # eine behaltene erste Fläche ist bis 30 min älter als issued_at; das Archiv nimmt dann
        # bis 30 min mehr als 6 h Vorlauf auf, der nächste Abruf ersetzt diese Werte
        await self._archive_temperatures(first_plane, issued_at)
        return forecast

    async def _fetch_plane(
        self, plane: PvPlane, *, snow: bool
    ) -> tuple[list[WeatherSlot], list[SnowHour]]:
        """Wetter einer Fläche, mit ``snow`` auch die Schneestunden derselben Antwort; bei
        Netzwerkfehler oder HTTP 5xx bis zu zweimal nach 30 s erneut.

        HTTP 429 (Limit) und andere Fehler gehen sofort an den Aufrufer: Ein schneller neuer
        Versuch hilft dort nicht.
        """
        for attempt in range(1, PLANE_ATTEMPTS):
            try:
                return await self._fetch_plane_once(plane, snow=snow)
            except (FetchError, OpenMeteoUnavailable) as exc:
                log.info(
                    "Open-Meteo nicht verfügbar, neuer Versuch",
                    plane=plane.name,
                    attempt=attempt,
                    error=str(exc),
                )
            await self._sleep(PLANE_RETRY_S)
        return await self._fetch_plane_once(plane, snow=snow)

    async def _fetch_plane_once(
        self, plane: PvPlane, *, snow: bool
    ) -> tuple[list[WeatherSlot], list[SnowHour]]:
        raw = await fetch_plane(self._http, self._clock, self._cfg.site.location, plane, snow=snow)
        if not raw.ok:
            raise http_error(raw)
        return parse_plane(raw.body), parse_snow(raw.body) if snow else []

    # --- Schnee auf den Modulen ---------------------------------------------------------------

    def check_snow(self, ledger: SlotSource) -> bool:
        """Kürzung mit den Ist-Werten der letzten 36 h neu rechnen; ``True``, wenn sich Zustand
        oder ein Faktor ab jetzt um mehr als ``CHANGE_MIN`` geändert hat. Dann ersetzt eine
        Prognose mit ``revision`` = jetzt die bisherige und geht neu nach InfluxDB, nicht ins
        Archiv (Entscheidung 8). Ohne Prognose ``False``.

        Synchron (Datenbankarbeit; der Aufrufer nimmt ``asyncio.to_thread``). Die Ist-Werte
        merkt sich der Dienst für den nächsten Abruf.
        """
        if self._issued is None:
            return False
        read_at = self._clock.now()
        found = {
            ensure_utc(slot): actual.pv_w
            for slot, actual in actuals(ledger, read_at - SNOW_WINDOW, read_at).items()
        }
        with self._snow_lock:
            self._actuals = found
            forecast = self._recut(self._clock.now())
        if forecast is None:
            return False
        log.info(
            "PV-Prognose bei Schnee neu gekürzt",
            state=forecast.snow.state,
            factor=forecast.snow.factor,
        )
        return True

    def set_snow_override(
        self,
        mode: Literal["covered", "free"] | None,
        *,
        actor: str,
        source: Source = "ui",
    ) -> bool:
        """Handschalter setzen (``None``: wieder Automatik) und die Kürzung sofort mit den
        gemerkten Ist-Werten neu rechnen; Eintrag im Änderungsprotokoll. Ohne Prognose ``False``
        (keine Datei, kein Eintrag), sonst ``True``.

        Synchron (Datei und Datenbank; der Aufrufer nimmt ``asyncio.to_thread``).
        """
        if self._issued is None:
            return False
        override = Override(mode, self._clock.now()) if mode is not None else None
        with self._override_lock:
            self._write_override(override)  # ohne ``_snow_lock``: ``fsync`` hält die Loop nicht an
            with self._snow_lock:
                self._override = override
                self._recut(self._clock.now())
        if self._audit is not None:
            self._audit.record(actor, source, OVERRIDE_ACTION, "pv", {"mode": mode or "auto"})
        log.info("Handschalter Schnee gesetzt", mode=mode, actor=actor)
        return True

    def snow_override(self) -> Literal["covered", "free"] | None:
        """Der gedrückte Knopf, solange er wirkt (höchstens ``OVERRIDE_MAX``); sonst ``None``
        (Automatik)."""
        override = self._override
        if override is None or self._clock.now() - ensure_utc(override.at) > OVERRIDE_MAX:
            return None
        return override.mode

    def _snow_cut(
        self,
        now: datetime,
        forecast: PvForecast,
        weather: Sequence[SnowHour],
        temperatures: Mapping[datetime, float],
    ) -> SnowCut:
        """Kürzung zu ``forecast`` (ungekürzte P50) mit den gemerkten Ist-Werten; mit der Sperre."""
        if not self._settings.current().settings.pv_model.snow_detection:
            return NO_CUT
        expected = forecast.uncut_p50_w
        observations = [
            Observation(slot, expected[slot], actual_w, temperatures.get(slot))
            for slot, actual_w in sorted(self._actuals.items())
            if slot in expected
        ]
        return snow_cut(
            now,
            sorted(expected),
            weather,
            observations,
            total_kwp=sum(plane.kwp for plane in self._cfg.pv.planes),
            override=self._override,
        )

    def _recut(self, now: datetime) -> PvForecast | None:
        """Neue Kürzung der aktuellen Prognose; bei einer Änderung die neue Prognose (schon
        ersetzt und für InfluxDB beauftragt), sonst ``None``. Ändert sich nur ``since``, ersetzt
        sie die Prognose mit dem neuen „seit“, ohne neue ``revision`` und ohne InfluxDB (die Werte
        bleiben). Nur mit der Sperre."""
        issued = self._issued
        if issued is None:
            return None
        current = issued.forecast
        cut = self._snow_cut(now, current, issued.weather, issued.temperatures)
        if not _snow_changed(current.snow, cut, now):
            if cut.since != current.snow.since:
                snow = replace(current.snow, since=cut.since)
                self._issued = replace(issued, forecast=replace(current, snow=snow))
            return None
        forecast = replace(
            current,
            p50_w=_cut(current.uncut_p50_w, cut),
            p25_w=_cut(issued.uncut_p25_w, cut),
            p75_w=_cut(issued.uncut_p75_w, cut),
            snow=cut,
            revision=now,
        )
        self._issued = replace(issued, forecast=forecast)
        self._publish_newest(forecast)
        return forecast

    def _publish_newest(self, forecast: PvForecast) -> None:
        """Mit der Sperre: InfluxDB schreibt immer die Loop. Aus der Loop sofort, aus einem
        Worker-Thread als Auftrag an die Loop, der nur schreibt, wenn bis dahin keine neuere
        Prognose ausgegeben wurde; so kommt die neueste immer zuletzt an."""
        self._published += 1
        loop = self._loop
        if loop is None or loop.is_closed() or threading.get_ident() == self._loop_thread:
            self._publish(forecast)
        else:
            loop.call_soon_threadsafe(self._publish_if_newest, forecast, self._published)

    def _publish_if_newest(self, forecast: PvForecast, number: int) -> None:
        if number == self._published:
            self._publish(forecast)

    def _read_override(self) -> Override | None:
        """Handschalter aus der Datei; ohne Datei oder bei unlesbarer Datei Automatik."""
        path = self._override_path
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mode, at = data["mode"], ensure_utc(datetime.fromisoformat(data["at"]))
            if mode not in ("covered", "free"):
                raise ValueError(f"unbekannte Art {mode!r}")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.warning("Handschalter Schnee unlesbar", path=str(path), error=str(exc))
            return None
        return Override(mode, at)

    def _write_override(self, override: Override | None) -> None:
        """Atomar schreiben (temporäre Datei, ``fsync``, ``os.replace``) oder löschen."""
        path = self._override_path
        if path is None:
            return
        if override is None:
            path.unlink(missing_ok=True)
            return
        text = json.dumps({"mode": override.mode, "at": override.at.isoformat()})
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)  # das Ziel entsteht erst mit dem letzten Schritt
            raise

    def recalibrate(self, ledger: SlotSource) -> None:
        """Faktoren und Bänder aus Archiv und Abrechnung der letzten 30 Tage neu bestimmen.

        Synchron (Datenbankarbeit; der Aufrufer nimmt ``asyncio.to_thread``). Erst wenn alles
        gelesen und gerechnet ist, werden beide Modelle gemeinsam ersetzt; bei einem Fehler
        bleiben die bisherigen.
        """
        if self._archive is None:
            return
        end = self._clock.now()
        start = end - CALIBRATION_WINDOW
        actual: dict[datetime, Actual] = {
            ensure_utc(slot): value for slot, value in actuals(ledger, start, end).items()
        }
        threshold = pv_threshold_w(self._cfg)
        matched: list[tuple[ForecastRecord, float]] = []
        for record in self._archive.records("pv", start, end):
            known = actual.get(ensure_utc(record.slot_start))
            if known is not None:
                matched.append((record, known.pv_w))
        excluded = outlier_days(
            [
                (ensure_utc(r.slot_start), r.raw_w, pv_w)
                for r, pv_w in matched
                if r.horizon == "h0_6"
            ],
            threshold,
        )
        cal_pairs: list[CalPair] = []
        err_pairs: list[ErrPair] = []
        for record, pv_w in matched:
            slot = ensure_utc(record.slot_start)
            local = slot.astimezone(LOCAL_TZ)
            if local.date() in excluded:
                continue
            if record.horizon == "h0_6":
                cal_pairs.append(CalPair(local.hour, record.clearness, record.raw_w, pv_w))
            # das Archiv hat die gekürzte P50 (Entscheidung 6); ungekürzt ist P50 ≥ 0,5 · roh
            # (kleinster Faktor, ``raw_w`` schon auf den Wechselrichter begrenzt), darunter war
            # die Ausgabe wegen Schnee gekürzt und taugt nicht für die Bänder, auch wenn die
            # Module frei waren
            if record.p50_w < FACTOR_RANGE[0] * record.raw_w:
                continue
            err_pairs.append(ErrPair(record.horizon, record.clearness, record.p50_w, pv_w))
        models = (calibrate(cal_pairs, threshold), fit_bands(err_pairs, threshold))
        self._models = models
        log.info(
            "PV-Prognose kalibriert",
            samples=models[0].samples,
            factors=len(models[0].factors),
            bands=len(models[1].by_class),
            excluded_days=len(excluded),
        )

    async def _archive_forecast(self, forecast: PvForecast) -> None:
        """Ins Archiv schreiben; ein Fehler dort lässt die neue Prognose bestehen."""
        if self._archive is None:
            return
        values = {
            slot: RecordValue(power_w, forecast.raw_w[slot], forecast.clearness.get(slot))
            for slot, power_w in forecast.p50_w.items()
        }
        try:
            await asyncio.to_thread(self._archive.record, "pv", forecast.issued_at, values)
        except Exception as exc:  # das Archiv darf die Prognose nicht verhindern
            log.warning("PV-Prognose konnte nicht archiviert werden", error=str(exc))

    async def _archive_temperatures(self, slots: list[WeatherSlot], issued_at: datetime) -> None:
        """Ins Wetterarchiv schreiben; ein Fehler dort lässt die neue Prognose bestehen."""
        if self._weather is None:
            return
        try:
            await asyncio.to_thread(self._weather.record, slots, issued_at)
        except Exception as exc:  # das Archiv darf die Prognose nicht verhindern
            log.warning("Temperatur konnte nicht archiviert werden", error=str(exc))

    def _publish(self, forecast: PvForecast) -> None:
        if self._sink is None:
            return
        for quantile, series in (
            ("p25", forecast.p25_w),
            ("p50", forecast.p50_w),
            ("p75", forecast.p75_w),
        ):
            tags = {"kind": "pv", "quantile": quantile}
            for slot, power_w in sorted(series.items()):
                self._sink.write(
                    Point("forecast", tags, {"kw": power_w / 1000.0}, slot, rp=RP_LONG)
                )

    async def run(self) -> None:
        """Sofort abrufen, dann zur Minute 7 jeder Stunde; nach Fehler spätestens nach 10 min."""
        while True:
            try:
                await self.refresh()
                succeeded = self._error is None
            except Exception:  # die Schleife darf nie stehen bleiben
                log.exception("PV-Prognose fehlgeschlagen")
                succeeded = False
            now = self._clock.now()
            wait = (next_local(now, lambda t: t.minute == FETCH_MINUTE) - now).total_seconds()
            if not succeeded:
                wait = min(wait, RETRY_S)
            await self._sleep(max(wait, 0.0))

    def component_status(self) -> ComponentStatus:
        if self._error is not None:
            return ComponentStatus("forecast", False, self._error)
        forecast = self.latest()
        if forecast is None:
            return ComponentStatus("forecast", False, NO_FORECAST)
        issued = forecast.issued_at.astimezone(LOCAL_TZ)
        message = f"PV-Prognose von {issued:%H:%M} Uhr"
        snow = forecast.snow
        if snow.state != "frei":  # die Komponente bleibt grün und nennt die Kürzung
            pct = round(snow.factor * 100)
            details = {
                "snow_state": snow.state,
                "snow_factor_pct": pct,
                "snow_since": snow.since.isoformat() if snow.since is not None else None,
            }
            message += f", gekürzt auf {pct} %: vermutlich Schnee auf den Modulen"
            return ComponentStatus("forecast", True, message, details=details)
        samples = self._models[0].samples
        if samples > 0:
            message += f", kalibriert aus {samples} Viertelstunden"
        return ComponentStatus("forecast", True, message)
