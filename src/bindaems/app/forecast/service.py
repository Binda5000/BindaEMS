"""PV-Prognose (P50): Abruf je Fläche, Modell, Summe und Veröffentlichung in InfluxDB.

Die Prognose liegt nur im Speicher; nach einem Neustart wird sie sofort neu abgerufen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

import httpx
import structlog

from bindaems.app.fetch import FetchError
from bindaems.app.forecast.openmeteo import SOURCE, ForecastParseError, fetch_plane, parse_plane
from bindaems.app.forecast.pv_model import combine, plane_power_w
from bindaems.app.history.influx import RP_LONG
from bindaems.app.schedule import next_local
from bindaems.app.settings.service import SettingsService
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots, slot_start

log = structlog.get_logger(__name__)

FETCH_MINUTE = 7
RETRY_S = 600.0
SLOT_HOURS = 0.25


@dataclass(frozen=True)
class PvForecast:
    issued_at: datetime
    source: str
    p50_w: Mapping[datetime, float]

    def day_energy_kwh(self, day: date) -> float | None:
        """Energie des lokalen Tages aus den vorhandenen Slots; ohne Slots ``None``."""
        values = [self.p50_w[slot] for slot in local_day_slots(day) if slot in self.p50_w]
        return sum(value * SLOT_HOURS / 1000.0 for value in values) if values else None

    def power_at(self, ts: datetime) -> float | None:
        return self.p50_w.get(slot_start(ts))


class ForecastService:
    def __init__(
        self,
        http: httpx.AsyncClient,
        cfg: Config,
        settings: SettingsService,
        clock: Clock,
        sink: PointSink | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._cfg = cfg
        self._settings = settings
        self._clock = clock
        self._sink = sink
        self._sleep = sleep
        self._forecast: PvForecast | None = None
        self._error: str | None = None

    def latest(self) -> PvForecast | None:
        return self._forecast

    async def refresh(self) -> PvForecast | None:
        """Neue Prognose; scheitert eine Fläche, bleibt die bisherige."""
        model = self._settings.current().settings.pv_model
        planes: list[dict[datetime, float]] = []
        for plane in self._cfg.pv.planes:
            try:
                raw = await fetch_plane(self._http, self._clock, self._cfg.site.location, plane)
                if not raw.ok:
                    raise ForecastParseError(f"Open-Meteo HTTP {raw.status}")
                weather = parse_plane(raw.body)
            except (FetchError, ForecastParseError) as exc:
                self._error = f"PV-Prognose für Fläche {plane.name} fehlgeschlagen: {exc}"
                log.warning("pv_forecast_failed", plane=plane.name, error=str(exc))
                return self._forecast
            planes.append(
                {
                    slot.slot_start: plane_power_w(slot.gti_w_m2, slot.temp_c, plane.kwp, model)
                    for slot in weather
                }
            )
        forecast = PvForecast(
            self._clock.now(), SOURCE, combine(planes, self._cfg.pv.inverter_ac_max_w)
        )
        self._forecast, self._error = forecast, None
        self._publish(forecast)
        return forecast

    def _publish(self, forecast: PvForecast) -> None:
        if self._sink is None:
            return
        tags = {"kind": "pv", "quantile": "p50"}
        for slot, power_w in sorted(forecast.p50_w.items()):
            self._sink.write(Point("forecast", tags, {"kw": power_w / 1000.0}, slot, rp=RP_LONG))

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
        if self._forecast is None:
            return ComponentStatus("forecast", False, "Noch keine PV-Prognose")
        issued = self._forecast.issued_at.astimezone(LOCAL_TZ)
        return ComponentStatus("forecast", True, f"PV-Prognose von {issued:%H:%M} Uhr")
