"""Wetterwarnungen von GeoSphere Austria (Warn-API, ohne Schlüssel) für den Standort.

Die Antwort ist ein GeoJSON-Feature; die Warnungen stehen in ``properties.warnings``. Beginn und
Ende kommen aus ``rawinfo.start``/``rawinfo.end`` (Unix-Sekunden als Text), die lokalen Textfelder
``begin``/``end`` werden ignoriert.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final, get_args

import httpx
import structlog
from pydantic import ValidationError

from bindaems.app.fetch import FetchError, get_raw
from bindaems.shared.config import LatLon
from bindaems.shared.plan import StormKind, StormLevel, StormWarning
from bindaems.shared.timeutil import Clock

URL: Final = "https://warnungen.zamg.at/wsapp/api/getWarningsForCoords"
SOURCE: Final = "geosphere_warn"

log = structlog.get_logger(__name__)

_KINDS: Final = get_args(StormKind)  # Reihenfolge = warntypid 1–7
_LEVELS: Final = get_args(StormLevel)


class WarningsUnavailable(Exception):
    """Warnungen sind nicht lesbar: kein Abruf, Fehlerantwort oder unerwartete Form."""


def _epoch(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError(f"kein Zeitstempel: {value!r}")
    return datetime.fromtimestamp(int(value), UTC)


def _warning(props: dict[str, Any]) -> StormWarning:
    kind_id, level = props.get("warntypid"), props.get("warnstufeid")
    if isinstance(kind_id, bool) or not isinstance(kind_id, int) or not 1 <= kind_id <= len(_KINDS):
        raise ValueError(f"unbekannte warntypid: {kind_id!r}")
    if level not in _LEVELS or isinstance(level, bool):
        raise ValueError(f"unbekannte warnstufeid: {level!r}")
    rawinfo = props.get("rawinfo")
    if not isinstance(rawinfo, dict):
        raise ValueError("rawinfo fehlt")
    return StormWarning(
        warn_id=props.get("warnid"),
        version=props.get("chgid"),
        kind=_KINDS[kind_id - 1],
        level=level,
        begin=_epoch(rawinfo.get("start")),
        end=_epoch(rawinfo.get("end")),
        text=str(props.get("text", "")),
    )


def parse_warnings(body: str) -> list[StormWarning]:
    """Warnungen aus der Antwort; unbrauchbare Einträge entfallen mit einer Log-Zeile.

    Wirft ``WarningsUnavailable`` bei kaputtem JSON, einer Fehlerantwort oder fehlendem
    ``properties.warnings``.
    """
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise WarningsUnavailable("Antwort ist kein JSON") from exc
    if not isinstance(data, dict):
        raise WarningsUnavailable("Antwort ist kein JSON-Objekt")
    if str(data.get("type", "")).lower() == "error":
        raise WarningsUnavailable(f"GeoSphere meldet einen Fehler: {data.get('msg')!s}"[:200])
    properties = data.get("properties")
    entries = properties.get("warnings") if isinstance(properties, dict) else None
    if not isinstance(entries, list):
        raise WarningsUnavailable("properties.warnings fehlt")
    found: list[StormWarning] = []
    for entry in entries:
        props = entry.get("properties") if isinstance(entry, dict) else None
        if not isinstance(props, dict):
            log.warning("Unwetterwarnung übersprungen", code="storm_warning_skipped", reason="Form")
            continue
        try:
            found.append(_warning(props))
        except (ValueError, TypeError, OverflowError, OSError, ValidationError) as exc:
            log.warning(
                "Unwetterwarnung übersprungen",
                code="storm_warning_skipped",
                warn_id=props.get("warnid"),
                reason=str(exc)[:200],
            )
    return found


async def fetch_warnings(
    http: httpx.AsyncClient, clock: Clock, location: LatLon
) -> list[StormWarning]:
    """Aktuelle Warnungen für den Standort; jedes Scheitern wird zu ``WarningsUnavailable``."""
    params: dict[str, str | int | float] = {"lon": location.lon, "lat": location.lat, "lang": "de"}
    try:
        raw = await get_raw(http, clock, SOURCE, URL, params)
    except FetchError as exc:
        raise WarningsUnavailable(str(exc)) from exc
    if not raw.ok:
        raise WarningsUnavailable(f"GeoSphere HTTP {raw.status}")
    return parse_warnings(raw.body)
