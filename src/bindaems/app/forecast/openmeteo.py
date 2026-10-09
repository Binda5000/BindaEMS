"""Open-Meteo je PV-Fläche: Einstrahlung auf die geneigte Fläche und Lufttemperatur, 15 min.

Der Wert zum Zeitpunkt T ist das Mittel über [T − 15 min, T) und gehört zum Slot, der bei
T − 15 min beginnt (Plan, Präzisierung 6).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from bindaems.app.fetch import RawResponse, get_raw
from bindaems.shared.config import LatLon, PvPlane
from bindaems.shared.timeutil import SLOT, Clock

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "open_meteo"
VARIABLES = ("global_tilted_irradiance", "temperature_2m")
GTI_UNIT = "W/m²"


@dataclass(frozen=True)
class WeatherSlot:
    slot_start: datetime
    gti_w_m2: float
    temp_c: float


class ForecastParseError(Exception):
    """Antwort von Open-Meteo ist ein Fehler oder hat nicht die erwartete Form."""


async def fetch_plane(
    http: httpx.AsyncClient, clock: Clock, location: LatLon, plane: PvPlane
) -> RawResponse:
    params: dict[str, str | int | float] = {
        "latitude": location.lat,
        "longitude": location.lon,
        "minutely_15": ",".join(VARIABLES),
        "tilt": plane.tilt_deg,
        "azimuth": plane.azimuth_deg,  # wie in config.yaml: 0 = Süd, −90 = Ost, 90 = West
        "forecast_days": 3,
        "past_days": 1,
        "timezone": "GMT",
        "timeformat": "unixtime",
    }
    return await get_raw(http, clock, SOURCE, OPEN_METEO_URL, params)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def parse_plane(body: str) -> list[WeatherSlot]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ForecastParseError("Antwort ist kein JSON") from exc
    if not isinstance(data, dict):
        raise ForecastParseError("Antwort ist kein JSON-Objekt")
    if data.get("error"):
        raise ForecastParseError(str(data.get("reason", "unbekannter Fehler")))
    units, series = data.get("minutely_15_units"), data.get("minutely_15")
    if not isinstance(units, dict) or not isinstance(series, dict):
        raise ForecastParseError("minutely_15 fehlt")
    unit = units.get("global_tilted_irradiance")
    if unit != GTI_UNIT:
        raise ForecastParseError(f"Einheit der Einstrahlung muss {GTI_UNIT} sein, nicht „{unit}“")
    times, gti, temp = (series.get(key) for key in ("time", *VARIABLES))
    if not isinstance(times, list) or not isinstance(gti, list) or not isinstance(temp, list):
        raise ForecastParseError("time, global_tilted_irradiance oder temperature_2m fehlt")
    if not len(times) == len(gti) == len(temp):
        raise ForecastParseError("Listen ungleich lang")
    slots: list[WeatherSlot] = []
    for stamp, irradiance, air in zip(times, gti, temp, strict=True):
        if irradiance is None or air is None:
            continue
        seconds, irradiance_w, air_c = _number(stamp), _number(irradiance), _number(air)
        if seconds is None or irradiance_w is None or air_c is None:
            raise ForecastParseError(f"ungültiger Eintrag: {stamp!r}, {irradiance!r}, {air!r}")
        try:
            end = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ForecastParseError(f"ungültiger Zeitstempel: {stamp!r}") from exc
        slots.append(WeatherSlot(end - SLOT, irradiance_w, air_c))
    return slots
