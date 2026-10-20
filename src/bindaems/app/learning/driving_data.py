"""Daten des Fahrverhaltens lesen (Plan 5b, Präzisierung 4).

Je Fahrzeug werden Anwesenheit, Ansteckvorgänge und Ladestand für ein Fenster gelesen. Die
Anwesenheit kommt aus der ersten verfügbaren Quelle: die Entität aus den Einstellungen
(``presence_entities``, HA-Datenbank), ``home_entity`` (HA-Datenbank, ohne sie der Status
``home_state`` in der BindaEMS-Datenbank), bei einem Tessie-Abschnitt der Status ``at_home``, sonst
nur die Ansteckvorgänge. Eine Quelle ohne Punkte bleibt die Quelle: Unbekannt bleibt unbekannt.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import structlog
from sqlalchemy import Engine, or_, select
from sqlalchemy.exc import SQLAlchemyError

from bindaems.app.db.schema import charging_session
from bindaems.app.history.influx import InfluxQueryError, InfluxReader, Series, quote_str
from bindaems.shared.config import Config, VehicleConfig, home_signal
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.settings import DrivingSettings

log = structlog.get_logger(__name__)

# So weit vor dem Fenster wird der letzte Zustand gesucht; ein Auto kann wochenlang stehen
SEED_LOOKBACK: Final = timedelta(days=90)
SOC_STEP: Final = "5m"

Source = Literal["ha", "home_entity", "tessie", "sessions"]


class DrivingDataError(Exception):
    """Die Daten ließen sich nicht lesen; der Dienst behält dann den letzten Stand."""


@dataclass(frozen=True)
class PresencePoint:
    ts: datetime
    home: bool


@dataclass(frozen=True)
class VehicleHistory:
    key: str
    source: Source
    presence: list[PresencePoint]
    seed: bool | None  # Zustand vor dem Fenster (letzter Punkt davor), ``None`` unbekannt
    sessions: list[tuple[datetime, datetime | None]]  # offenes Ende: ``None`` (gilt bis jetzt)
    soc: list[tuple[datetime, float]]


@dataclass(frozen=True)
class _Plan:
    """Woher die Anwesenheit kommt: Quelle und Abfragen dafür (``None``: nur Ansteckvorgänge)."""

    source: Source
    database: str | None  # ``None``: BindaEMS-Datenbank
    # Rumpf der Abfrage ohne Zeitbedingung: ``SELECT … FROM … WHERE …``
    query: str | None
    field: str  # Feld, aus dem der Zustand gelesen wird (nur BindaEMS)


def _split_entity(entity: str) -> tuple[str, str] | None:
    domain, dot, object_id = entity.partition(".")
    return (domain, object_id) if dot and domain and object_id else None


def _ha_condition(entity: str) -> str | None:
    parts = _split_entity(entity)
    if parts is None:
        return None
    return f'"domain" = {quote_str(parts[0])} AND "entity_id" = {quote_str(parts[1])}'


def _ha_select(condition: str) -> str:
    return f'SELECT "value", "state" FROM /.*/ WHERE {condition}'  # noqa: S608  # Werte maskiert


def _status_select(signal: str, field: str) -> str:
    # Werte sind maskiert, ``field`` ist eine Konstante
    return f'SELECT "{field}" FROM "status" WHERE "id" = {quote_str(signal)}'  # noqa: S608


def _presence_plan(
    vehicle: VehicleConfig, cfg: Config, settings: DrivingSettings, key: str
) -> _Plan:
    """Quelle der Anwesenheit nach der Reihenfolge der Präzisierung 4."""
    ha_database = cfg.influxdb.ha_database
    configured = settings.presence_entities.get(key)
    if configured is not None:
        condition = _ha_condition(configured)
        if ha_database is None:
            log.warning(
                "Die Anwesenheit aus Home Assistant braucht eine HA-Datenbank. "
                "Die nächste Quelle gilt.",
                vehicle=key,
            )
        elif condition is not None:
            head = _ha_select(condition)
            return _Plan("ha", ha_database, head, "")
    if vehicle.home_entity is not None:
        condition = _ha_condition(vehicle.home_entity)
        if ha_database is not None and condition is not None:
            return _Plan(
                "home_entity",
                ha_database,
                _ha_select(condition),
                "",
            )
        if ha_database is None:
            return _Plan(
                "home_entity", None, _status_select(home_signal(key), "value_str"), "value_str"
            )
    if vehicle.tessie is not None:
        return _Plan(
            "tessie", None, _status_select(f"vehicle.{key}.at_home", "value_bool"), "value_bool"
        )
    return _Plan("sessions", None, None, "")


def _records(series: Sequence[Series]) -> list[tuple[datetime, dict[str, Any]]]:
    """Alle Zeilen aller Reihen als (Zeitpunkt, Spalte → Wert), nach Zeit sortiert."""
    rows: list[tuple[datetime, dict[str, Any]]] = []
    for item in series:
        for row in item.values:
            if not row or isinstance(row[0], bool) or not isinstance(row[0], int):
                continue
            ts = datetime.fromtimestamp(row[0] / 1000, UTC)
            rows.append((ts, dict(zip(item.columns, row, strict=False))))
    rows.sort(key=lambda entry: entry[0])
    return rows


def _ha_home(cells: dict[str, Any]) -> bool | None:
    """HA schreibt ``home`` als 1, ``not_home`` als 0 und eine Zone nur als Text in ``state``."""
    value, state = cells.get("value"), cells.get("state")
    if value is None and not isinstance(state, str):
        return None
    return (isinstance(value, int | float) and not isinstance(value, bool) and value == 1) or (
        state == "home"
    )


def _status_home(cells: dict[str, Any], field: str) -> bool | None:
    value = cells.get(field)
    if field == "value_bool":
        return value if isinstance(value, bool) else None
    return value == "home" if isinstance(value, str) else None


def _points(rows: list[tuple[datetime, dict[str, Any]]], plan: _Plan) -> list[PresencePoint]:
    points: list[PresencePoint] = []
    for ts, cells in rows:
        home = _ha_home(cells) if plan.database is not None else _status_home(cells, plan.field)
        if home is not None:
            points.append(PresencePoint(ts, home))
    return points


def _window(start: datetime, end: datetime) -> str:
    return f"time >= {epoch_ms(start)}ms AND time <= {epoch_ms(end)}ms"


async def _query(reader: InfluxReader, text: str, database: str | None) -> list[Series]:
    try:
        return await reader.query(text, database=database)
    except InfluxQueryError as exc:
        raise DrivingDataError(f"Die Fahrdaten sind nicht lesbar: {exc}") from exc


async def _presence(
    reader: InfluxReader, plan: _Plan, start: datetime, end: datetime
) -> tuple[list[PresencePoint], bool | None]:
    if plan.query is None:
        return [], None
    window = await _query(reader, f"{plan.query} AND {_window(start, end)}", plan.database)
    earlier = await _query(
        reader,
        f"{plan.query} AND time >= {epoch_ms(start - SEED_LOOKBACK)}ms AND time < "
        f"{epoch_ms(start)}ms ORDER BY time DESC LIMIT 1",
        plan.database,
    )
    before = _points(_records(earlier), plan)
    return _points(_records(window), plan), (before[-1].home if before else None)


def _soc_points(
    rows: list[tuple[datetime, dict[str, Any]]], column: str
) -> list[tuple[datetime, float]]:
    result: list[tuple[datetime, float]] = []
    for ts, cells in rows:
        value = cells.get(column)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            continue
        result.append((ts, float(value)))
    return result


async def _soc(
    reader: InfluxReader,
    cfg: Config,
    vehicle: VehicleConfig,
    key: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float]]:
    own = await _query(
        reader,
        f'SELECT last("pct") FROM "soc" WHERE "id" = {quote_str(key)} '  # noqa: S608
        f"AND {_window(start, end)} GROUP BY time({SOC_STEP}) fill(none)",  # Werte maskiert
        None,
    )
    points = _soc_points(_records(own), "last")
    if points or vehicle.soc_entity is None or cfg.influxdb.ha_database is None:
        return points
    condition = _ha_condition(vehicle.soc_entity)
    if condition is None:
        return points
    ha = await _query(
        reader,
        f'SELECT last("value") FROM /.*/ WHERE {condition} '  # noqa: S608
        f"AND {_window(start, end)} GROUP BY time({SOC_STEP}) fill(none)",  # Werte maskiert
        cfg.influxdb.ha_database,
    )
    return _soc_points(_records(ha), "last")


def _sessions(
    engine: Engine, key: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime | None]]:
    """Ansteckvorgänge des Autos, die das Fenster berühren (Gäste und Unbekannte zählen nicht)."""
    statement = (
        select(charging_session.c.start, charging_session.c.end)
        .where(
            charging_session.c.vehicle == key,
            charging_session.c.start <= end,
            or_(charging_session.c.end.is_(None), charging_session.c.end >= start),
        )
        .order_by(charging_session.c.start)
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(statement).all()
    except SQLAlchemyError as exc:
        raise DrivingDataError(f"Die Ansteckvorgänge sind nicht lesbar: {exc}") from exc
    return [(row.start, row.end) for row in rows]


async def load_history(
    reader: InfluxReader,
    engine: Engine,
    cfg: Config,
    settings: DrivingSettings,
    key: str,
    *,
    start: datetime,
    end: datetime,
) -> VehicleHistory:
    """Anwesenheit, Ansteckvorgänge und Ladestand von ``key`` in ``[start, end]``; ein Fehler beim
    Lesen wirft ``DrivingDataError``."""
    vehicle = cfg.vehicles.get(key)
    if vehicle is None:
        raise DrivingDataError(f"Das Fahrzeug „{key}“ ist nicht konfiguriert.")
    plan = _presence_plan(vehicle, cfg, settings, key)
    presence, seed = await _presence(reader, plan, start, end)
    soc = await _soc(reader, cfg, vehicle, key, start, end)
    sessions = _sessions(engine, key, start, end)
    return VehicleHistory(key, plan.source, presence, seed, sessions, soc)
