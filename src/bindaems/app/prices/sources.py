"""Preisquellen: smartENERGY (primär, Spec 3.8) und Energy-Charts oder aWATTar als Referenz.

``parse`` prüft die Form der Antwort streng; jede Abweichung wird zu ``PriceParseError`` mit
dem Namen der Quelle am Anfang. Alle Zeitpunkte in UTC.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal, Protocol

import httpx

from bindaems.app.fetch import RawResponse, get_raw
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ensure_utc

SINGLE_INTERVAL = timedelta(minutes=15)  # Energy-Charts mit nur einem Eintrag


@dataclass(frozen=True)
class PriceInterval:
    start: datetime
    end: datetime
    ct_kwh: float


class PriceParseError(Exception):
    """Antwort einer Preisquelle hat nicht die erwartete Form."""


def _object(label: str, body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise PriceParseError(f"{label}: Antwort ist kein JSON") from exc
    if not isinstance(data, dict):
        raise PriceParseError(f"{label}: Antwort ist kein JSON-Objekt")
    return data


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _list(label: str, data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise PriceParseError(f"{label}: Feld „{key}“ fehlt oder ist keine Liste")
    return value


def _interval(label: str, start: datetime, end: datetime, ct_kwh: float) -> PriceInterval:
    if end <= start:
        raise PriceParseError(f"{label}: Ende {end.isoformat()} liegt nicht nach dem Beginn")
    return PriceInterval(start, end, ct_kwh)


def _local_iso(ts: datetime) -> str:
    return ensure_utc(ts).astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def _from_epoch(label: str, value: Any, divisor: int) -> datetime:
    number = _number(value)
    try:
        if number is None:
            raise ValueError
        return datetime.fromtimestamp(number / divisor, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise PriceParseError(f"{label}: ungültiger Zeitstempel: {value!r}") from exc


class SmartEnergySource:
    name = "smartenergy"
    URL = "https://apis.smartenergy.at/market/v1/price"
    LABEL = "smartENERGY"

    def __init__(self, http: httpx.AsyncClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    async def fetch(self) -> RawResponse:
        return await get_raw(self._http, self._clock, self.name, self.URL, {})

    def parse(self, body: str) -> list[PriceInterval]:
        label = self.LABEL
        data = _object(label, body)
        unit = data.get("unit")
        if unit != "ct/kWh":
            raise PriceParseError(f"{label}: unbekannte Einheit „{unit}“")
        minutes = data.get("interval")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            raise PriceParseError(f"{label}: ungültiges Intervall: {minutes!r}")
        step = timedelta(minutes=minutes)
        intervals: list[PriceInterval] = []
        for entry in _list(label, data, "data"):
            if not isinstance(entry, dict):
                raise PriceParseError(f"{label}: Eintrag ist kein Objekt: {entry!r}")
            text = entry.get("date")
            try:
                start = datetime.fromisoformat(text) if isinstance(text, str) else None
            except ValueError:
                start = None
            if start is None:
                raise PriceParseError(f"{label}: ungültiger Zeitpunkt: {text!r}")
            if start.tzinfo is None or start.utcoffset() is None:
                raise PriceParseError(f"{label}: Zeitpunkt ohne Zeitzone: {text}")
            value = _number(entry.get("value"))
            if value is None:
                raise PriceParseError(f"{label}: ungültiger Preis: {entry.get('value')!r}")
            start = start.astimezone(UTC)
            intervals.append(_interval(label, start, start + step, value))
        return sorted(intervals, key=lambda interval: interval.start)


class ReferenceSource(Protocol):
    name: str

    async def fetch(self, start: datetime, end: datetime) -> RawResponse: ...

    def parse(self, body: str) -> list[PriceInterval]: ...


class EnergyChartsSource:
    name = "energy_charts"
    URL = "https://api.energy-charts.info/price"
    LABEL = "Energy-Charts"

    def __init__(self, http: httpx.AsyncClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    async def fetch(self, start: datetime, end: datetime) -> RawResponse:
        params = {"bzn": "AT", "start": _local_iso(start), "end": _local_iso(end)}
        return await get_raw(self._http, self._clock, self.name, self.URL, params)

    def parse(self, body: str) -> list[PriceInterval]:
        label = self.LABEL
        data = _object(label, body)
        unit = data.get("unit")
        if unit != "EUR / MWh":
            raise PriceParseError(f"{label}: unbekannte Einheit „{unit}“")
        stamps, prices = _list(label, data, "unix_seconds"), _list(label, data, "price")
        if len(stamps) != len(prices):
            raise PriceParseError(f"{label}: unix_seconds und price sind ungleich lang")
        times = [_from_epoch(label, stamp, 1) for stamp in stamps]
        if any(later <= earlier for earlier, later in pairwise(times)):
            raise PriceParseError(f"{label}: Zeitstempel nicht streng aufsteigend")
        intervals: list[PriceInterval] = []
        for index, (start, price) in enumerate(zip(times, prices, strict=True)):
            if price is None:
                continue
            value = _number(price)
            if value is None:
                raise PriceParseError(f"{label}: ungültiger Preis: {price!r}")
            if index + 1 < len(times):
                end = times[index + 1]
            elif len(times) >= 2:
                end = start + (times[-1] - times[-2])
            else:
                end = start + SINGLE_INTERVAL
            intervals.append(_interval(label, start, end, value / 10))
        return intervals


class AwattarSource:
    name = "awattar"
    URL = "https://api.awattar.at/v1/marketdata"
    LABEL = "aWATTar"

    def __init__(self, http: httpx.AsyncClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    async def fetch(self, start: datetime, end: datetime) -> RawResponse:
        params = {"start": epoch_ms(start), "end": epoch_ms(end)}
        return await get_raw(self._http, self._clock, self.name, self.URL, params)

    def parse(self, body: str) -> list[PriceInterval]:
        label = self.LABEL
        intervals: list[PriceInterval] = []
        for entry in _list(label, _object(label, body), "data"):
            if not isinstance(entry, dict):
                raise PriceParseError(f"{label}: Eintrag ist kein Objekt: {entry!r}")
            unit = entry.get("unit")
            if not isinstance(unit, str) or unit.lower() != "eur/mwh":
                raise PriceParseError(f"{label}: unbekannte Einheit „{unit}“")
            start = _from_epoch(label, entry.get("start_timestamp"), 1000)
            end = _from_epoch(label, entry.get("end_timestamp"), 1000)
            value = _number(entry.get("marketprice"))
            if value is None:
                raise PriceParseError(f"{label}: ungültiger Preis: {entry.get('marketprice')!r}")
            intervals.append(_interval(label, start, end, value / 10))
        return sorted(intervals, key=lambda interval: interval.start)


def make_reference(
    name: Literal["energy_charts", "awattar"], http: httpx.AsyncClient, clock: Clock
) -> ReferenceSource:
    if name == "energy_charts":
        return EnergyChartsSource(http, clock)
    return AwattarSource(http, clock)
