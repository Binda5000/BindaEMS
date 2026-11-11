"""Open-Meteo je PV-Fläche: Einstrahlung auf die geneigte Fläche, Lufttemperatur sowie globale und
diffuse Strahlung (für die Klarheit), 15 min.

Der Wert zum Zeitpunkt T ist das Mittel über [T − 15 min, T) und gehört zum Slot, der bei
T − 15 min beginnt (Plan, Präzisierung 6).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bindaems.app.fetch import RawResponse, get_raw
from bindaems.shared.config import LatLon, PvPlane
from bindaems.shared.timeutil import SLOT, Clock

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "open_meteo"
VARIABLES = (
    "global_tilted_irradiance",
    "temperature_2m",
    "shortwave_radiation",
    "diffuse_radiation",
)
GTI_UNIT = "W/m²"
RADIATION_UNIT = "W/m²"
TEMPERATURE_UNIT = "°C"
SNOW_VARIABLES = "snowfall,snow_depth"
SNOWFALL_UNIT = "cm"
SNOW_DEPTH_UNIT = "m"
HISTORY_DAYS = 92  # so weit zurück reicht die Vorhersage-Schnittstelle von Open-Meteo
REASON_MAX = 200


@dataclass(frozen=True)
class WeatherSlot:
    slot_start: datetime
    gti_w_m2: float
    temp_c: float
    ghi_w_m2: float | None = None
    diffuse_w_m2: float | None = None


@dataclass(frozen=True)
class SnowHour:
    """Schneefall und Schneehöhe einer Stunde; ``hour_start`` (UTC) ist der Beginn der Stunde,
    auf die sich der Schneefall bezieht (der Wert zu T gehört zur Stunde ab T − 1 h). Die
    Schneehöhe ist bei Open-Meteo ein Momentanwert zu T, gilt also am Ende dieser Stunde."""

    hour_start: datetime
    snowfall_cm: float
    snow_depth_m: float | None


class ForecastParseError(Exception):
    """Antwort von Open-Meteo ist ein Fehler oder hat nicht die erwartete Form."""


class OpenMeteoUnavailable(ForecastParseError):
    """HTTP 5xx: Open-Meteo ist kurz nicht verfügbar, ein neuer Versuch lohnt sich."""


def http_error(raw: RawResponse) -> ForecastParseError:
    """Fehler zu einer Antwort mit HTTP-Fehlerstatus, mit dem ``reason`` von Open-Meteo, falls
    vorhanden („Open-Meteo HTTP 429: Daily API request limit exceeded …“)."""
    message = f"Open-Meteo HTTP {raw.status}"
    try:
        data = json.loads(raw.body)
    except ValueError:
        data = None
    reason = data.get("reason") if isinstance(data, dict) else None
    if isinstance(reason, str) and reason.strip():
        message += f": {reason.strip()[:REASON_MAX]}"
    return OpenMeteoUnavailable(message) if raw.status >= 500 else ForecastParseError(message)


async def fetch_plane(
    http: httpx.AsyncClient,
    clock: Clock,
    location: LatLon,
    plane: PvPlane,
    *,
    snow: bool = False,
) -> RawResponse:
    """Prognose einer Fläche; mit ``snow`` zusätzlich Schneefall und Schneehöhe (stündlich)."""
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
    if snow:
        params["hourly"] = SNOW_VARIABLES
    return await get_raw(http, clock, SOURCE, OPEN_METEO_URL, params)


async def fetch_temperature_history(
    http: httpx.AsyncClient, clock: Clock, location: LatLon
) -> RawResponse:
    """Lufttemperatur am Standort der letzten 92 Tage bis zum Ende des heutigen Tages."""
    params: dict[str, str | int | float] = {
        "latitude": location.lat,
        "longitude": location.lon,
        "minutely_15": "temperature_2m",
        "forecast_days": 1,
        "past_days": HISTORY_DAYS,
        "timezone": "GMT",
        "timeformat": "unixtime",
    }
    return await get_raw(http, clock, SOURCE, OPEN_METEO_URL, params)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _optional_series(
    series: dict[str, Any], units: dict[str, Any], name: str, length: int
) -> list[Any] | None:
    """Liste einer Strahlungsvariablen; fehlt sie, bleibt nur die Klarheit leer (``None``)."""
    values = series.get(name)
    if values is None:
        return None
    unit = units.get(name)
    if unit != RADIATION_UNIT:
        raise ForecastParseError(f"Einheit von {name} muss {RADIATION_UNIT} sein, nicht „{unit}“")
    if not isinstance(values, list) or len(values) != length:
        raise ForecastParseError(f"{name} hat nicht die erwartete Länge")
    return values


def _load_series(body: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Einheiten und Reihen aus ``minutely_15``; eine Fehlerantwort oder falsche Form wirft."""
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
    return units, series


def _slot_start(stamp: Any, seconds: float) -> datetime:
    """Slotbeginn zum Zeitstempel (Ende des Slots)."""
    try:
        end = datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ForecastParseError(f"ungültiger Zeitstempel: {stamp!r}") from exc
    return end - SLOT


def parse_plane(body: str) -> list[WeatherSlot]:
    units, series = _load_series(body)
    unit = units.get("global_tilted_irradiance")
    if unit != GTI_UNIT:
        raise ForecastParseError(f"Einheit der Einstrahlung muss {GTI_UNIT} sein, nicht „{unit}“")
    times, gti, temp = (series.get(key) for key in ("time", *VARIABLES[:2]))
    if not isinstance(times, list) or not isinstance(gti, list) or not isinstance(temp, list):
        raise ForecastParseError("time, global_tilted_irradiance oder temperature_2m fehlt")
    if not len(times) == len(gti) == len(temp):
        raise ForecastParseError("Listen ungleich lang")
    ghi = _optional_series(series, units, "shortwave_radiation", len(times))
    diffuse = _optional_series(series, units, "diffuse_radiation", len(times))
    slots: list[WeatherSlot] = []
    for index, (stamp, irradiance, air) in enumerate(zip(times, gti, temp, strict=True)):
        if irradiance is None or air is None:
            continue
        seconds, irradiance_w, air_c = _number(stamp), _number(irradiance), _number(air)
        if seconds is None or irradiance_w is None or air_c is None:
            raise ForecastParseError(f"ungültiger Eintrag: {stamp!r}, {irradiance!r}, {air!r}")
        slots.append(
            WeatherSlot(
                _slot_start(stamp, seconds),
                irradiance_w,
                air_c,
                _number(ghi[index]) if ghi is not None else None,
                _number(diffuse[index]) if diffuse is not None else None,
            )
        )
    return slots


def parse_temperatures(body: str) -> dict[datetime, float]:
    """Lufttemperatur je Slotbeginn aus einer Antwort mit ``temperature_2m``; Lücken entfallen."""
    units, series = _load_series(body)
    unit = units.get("temperature_2m")
    if unit != TEMPERATURE_UNIT:
        raise ForecastParseError(
            f"Einheit der Temperatur muss {TEMPERATURE_UNIT} sein, nicht „{unit}“"
        )
    times, temp = series.get("time"), series.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temp, list):
        raise ForecastParseError("time oder temperature_2m fehlt")
    if len(times) != len(temp):
        raise ForecastParseError("Listen ungleich lang")
    found: dict[datetime, float] = {}
    for stamp, air in zip(times, temp, strict=True):
        if air is None:
            continue
        seconds, air_c = _number(stamp), _number(air)
        if seconds is None or air_c is None:
            raise ForecastParseError(f"ungültiger Eintrag: {stamp!r}, {air!r}")
        found[_slot_start(stamp, seconds)] = air_c
    return found


def parse_snow(body: str) -> list[SnowHour]:
    """Schneefall und Schneehöhe je Stunde aus ``hourly``. Fehlt der Block, eine Variable oder
    stimmt eine Einheit nicht (cm, m), ist das Ergebnis leer: dann gibt es keine Schneeerkennung,
    die Prognose läuft unverändert weiter. Wirft nie."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    units, block = data.get("hourly_units"), data.get("hourly")
    if not isinstance(units, dict) or not isinstance(block, dict):
        return []
    if units.get("snowfall") != SNOWFALL_UNIT or units.get("snow_depth") != SNOW_DEPTH_UNIT:
        return []
    times, snowfall, depth = (block.get(key) for key in ("time", "snowfall", "snow_depth"))
    if not isinstance(times, list) or not isinstance(snowfall, list) or not isinstance(depth, list):
        return []
    if not len(times) == len(snowfall) == len(depth):
        return []
    hours: list[SnowHour] = []
    for stamp, fall, deep in zip(times, snowfall, depth, strict=True):
        if fall is None:
            continue
        seconds, fall_cm = _number(stamp), _number(fall)
        if seconds is None or fall_cm is None:
            return []
        try:
            start = datetime.fromtimestamp(seconds, UTC) - timedelta(hours=1)
        except (OverflowError, OSError, ValueError):
            return []
        hours.append(SnowHour(start, fall_cm, _number(deep)))
    return hours
