"""Ein Datensatz je lokalem Tag: Abfahrt, Rückkehr, Ankunft und Verbrauch (Plan 5b, Präzisierung 5).

Aus der Anwesenheit (oder den Ansteckvorgängen) eines Autos entstehen Aufenthalte zu Hause als
Intervalle. Je lokalem Tag ``d`` (Wiener Zeit) zählt der Aufenthalt, der den Anker des Tages
enthält: ``d 03:00``, sonst ``d 00:00``. War das Auto zu keinem der beiden Zeitpunkte zu Hause, ist
der Tag ``away`` (Ruling P2). Die Abfahrt ist das Ende dieses Aufenthalts, wenn es auf den Tag ``d``
fällt; so gehört eine Abfahrt um 00:30 zum neuen Tag. Rückkehr und Ankunft sind der Beginn des
Aufenthalts, der die folgende Nacht umfasst; Zwischenstopps teilen die Fahrt nicht. Alle Zeitpunkte
sind UTC, Tage und Anker lokal.

Laden unterwegs: Der Ladestand ändert sich zwischen Abfahrt und Rückkehr nicht nur durch Fahren.
Ein Anstieg (Folge von Schritten ohne Abfall) ab ``AWAY_CHARGE_MIN_PP`` zählt als Laden; berührt er
einen Aufenthalt zu Hause, ist er ein Zwischenstopp daheim, sonst Laden auswärts. ``net_pp`` ist
Abfahrts- minus Rückkehrstand, ``consumption_pp`` der Fahrverbrauch (netto plus alles Geladene),
``charged_away_pp`` das auswärts Geladene und ``drawdown_pp`` die tiefste Entladung gegenüber der
Abfahrt (unbekannt, wenn das Laden auswärts gleich nach der Abfahrt beginnt).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from bindaems.app.daytype import is_holiday
from bindaems.app.learning.driving_data import VehicleHistory
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

WINDOW_DAYS: Final = 56
UNPLUG_WINDOW: Final = timedelta(minutes=30)
DEPARTURE_SOC_LOOKBACK: Final = timedelta(hours=6)
RETURN_SOC_WINDOW: Final = timedelta(minutes=30)
SESSION_GRACE: Final = timedelta(minutes=5)
AWAY_CHARGE_MIN_PP: Final = 3.0  # kleinere Anstiege sind Messrauschen

_NEVER: Final = datetime.max.replace(tzinfo=UTC)  # offenes Ende: gilt über heute hinaus
_ALWAYS: Final = datetime.min.replace(tzinfo=UTC)  # Beginn vor allen Daten

Stay = tuple[datetime, datetime]


@dataclass(frozen=True)
class DayRecord:
    day: date
    weekday: int  # 0 = Montag … 6 = Sonntag; Feiertage zählen als 6
    away: bool  # um den Anker des Tages nicht zu Hause; die übrigen Felder sind dann leer
    departure: datetime | None
    departure_soc: float | None
    arrival: datetime | None
    arrival_soc: float | None
    return_at: datetime | None
    return_soc: float | None
    consumption_pp: float | None  # Fahrverbrauch: netto plus alles unterwegs Geladene
    net_pp: float | None = None  # Abfahrts- minus Rückkehrstand
    charged_away_pp: float | None = None  # auswärts geladene Prozentpunkte
    drawdown_pp: float | None = None  # tiefste Entladung gegenüber der Abfahrt


@dataclass(frozen=True)
class TripSoc:
    consumption_pp: float
    charged_away_pp: float
    drawdown_pp: float | None


def _local(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=LOCAL_TZ).astimezone(UTC)


def _merge(stays: list[Stay]) -> list[Stay]:
    merged: list[Stay] = []
    for start, end in sorted(stays):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _session_end(end: datetime | None) -> datetime:
    return _NEVER if end is None else ensure_utc(end)


def _stays(history: VehicleHistory) -> tuple[list[Stay], datetime | None]:
    """Aufenthalte zu Hause und der Zeitpunkt, ab dem der Zustand bekannt ist (``None``: nie)."""
    if history.source == "sessions":
        stays = _merge([(ensure_utc(s), _session_end(e)) for s, e in history.sessions])
        return stays, (stays[0][0] if stays else None)
    stays = []
    state = history.seed
    start: datetime | None = _ALWAYS if state else None
    for point in sorted(history.presence, key=lambda p: p.ts):
        ts = ensure_utc(point.ts)
        if point.home and state is not True:
            start = ts
        elif not point.home and state is True and start is not None:
            stays.append((start, ts))
            start = None
        state = point.home
    if state and start is not None:
        stays.append((start, _NEVER))
    if history.seed is not None:
        known: datetime | None = _ALWAYS
    else:
        known = ensure_utc(history.presence[0].ts) if history.presence else None
    return _merge(stays), known


def _stay_at(stays: list[Stay], moment: datetime, *, closed: bool = False) -> Stay | None:
    """Der Aufenthalt, der ``moment`` enthält; mit ``closed`` zählt auch sein Ende dazu."""
    index = bisect_right(stays, (moment, _NEVER)) - 1
    if (
        index >= 0
        and stays[index][0] <= moment
        and (moment <= stays[index][1] if closed else moment < stays[index][1])
    ):
        return stays[index]
    return None


def _night_stay(stays: list[Stay], day: date) -> Stay | None:
    """Der Aufenthalt, der den Anker von ``day`` enthält: 03:00, sonst 00:00. Am Rand zählt eine
    Abfahrt genau um 00:00:00 mit (das Ende ist eingeschlossen)."""
    return _stay_at(stays, _local(day, time(3))) or _stay_at(
        stays, _local(day, time(0)), closed=True
    )


def _soc_between(
    soc: list[tuple[datetime, float]], start: datetime, end: datetime, *, last: bool
) -> float | None:
    inside = [value for ts, value in soc if start <= ts <= end]
    if not inside:
        return None
    return inside[-1] if last else inside[0]


def _arrival_soc(
    soc: list[tuple[datetime, float]], session_starts: list[datetime], at: datetime
) -> float | None:
    """Erster Ladestand nach der Ankunft, spätestens 5 min nach dem nächsten Ansteckvorgang."""
    limit = at + RETURN_SOC_WINDOW
    later = [s for s in session_starts if s >= at]
    if later:
        limit = min(limit, min(later) + SESSION_GRACE)
    return _soc_between(soc, at, limit, last=False)


def _trip_soc(
    soc: list[tuple[datetime, float]],
    home: list[Stay],
    departure: datetime,
    departure_soc: float,
    return_at: datetime,
    return_soc: float,
) -> TripSoc:
    """Fahrverbrauch, auswärts Geladenes und Entladung einer Fahrt aus den Ladeständen.

    ``home``: Aufenthalte und Ansteckvorgänge; ein Anstieg, der eines davon berührt, ist zu Hause.
    """
    points = [(departure, departure_soc)]
    points += [(ts, value) for ts, value in soc if departure < ts < return_at]
    points.append((return_at, return_soc))
    deltas = [points[i + 1][1] - points[i][1] for i in range(len(points) - 1)]

    def at_home(i: int) -> bool:
        return any(start < points[i + 1][0] and end > points[i][0] for start, end in home)

    # Anstiege: maximale Folgen von Schritten ohne Abfall
    runs: list[tuple[int, int]] = []
    first = 0
    for i, delta in enumerate(deltas):
        if delta < 0:
            if i > first:
                runs.append((first, i))
            first = i + 1
    if len(deltas) > first:
        runs.append((first, len(deltas)))

    counted_total = 0.0
    charged_away = 0.0
    first_away_start: int | None = None
    for begin, end in runs:
        height = sum(deltas[begin:end])
        if height < AWAY_CHARGE_MIN_PP:
            continue
        counted_total += height
        if not any(at_home(i) for i in range(begin, end) if deltas[i] > 0):
            charged_away += height
            if first_away_start is None:
                first_away_start = begin
    drawdown: float | None = None
    if first_away_start != 0:
        drawdown = max(0.0, departure_soc - min(value for _, value in points))
    return TripSoc(max(0.0, departure_soc - return_soc + counted_total), charged_away, drawdown)


def day_records(history: VehicleHistory, *, today: date) -> list[DayRecord]:
    """Ein Datensatz je bewertetem Tag vor ``today``, nach Tag sortiert.

    Bewertet wird ein Tag nur, wenn der Zustand ab seiner lokalen Mitternacht bekannt ist (``seed``
    oder ein früherer Punkt); ``today`` selbst ist unvollständig und zählt nicht.
    """
    stays, known_from = _stays(history)
    if known_from is None:
        return []
    soc = sorted((ensure_utc(ts), value) for ts, value in history.soc)
    session_starts = [ensure_utc(s) for s, _ in history.sessions]
    home = _merge([*stays, *((ensure_utc(s), _session_end(e)) for s, e in history.sessions)])
    session_ends = sorted(ensure_utc(e) for _, e in history.sessions if e is not None)

    if known_from == _ALWAYS:
        starts = [stay[0].astimezone(LOCAL_TZ).date() for stay in stays if stay[0] != _ALWAYS]
        first_day = min([today - timedelta(days=WINDOW_DAYS), *starts])
    else:
        first_day = known_from.astimezone(LOCAL_TZ).date()

    records: list[DayRecord] = []
    day = first_day
    while day < today:
        record = _record(day, stays, home, known_from, soc, session_starts, session_ends)
        if record is not None:
            records.append(record)
        day += timedelta(days=1)
    return records


def _record(
    day: date,
    stays: list[Stay],
    home: list[Stay],
    known_from: datetime,
    soc: list[tuple[datetime, float]],
    session_starts: list[datetime],
    session_ends: list[datetime],
) -> DayRecord | None:
    day_start = _local(day, time(0))
    day_end = _local(day + timedelta(days=1), time(0))
    if known_from > day_start:
        return None
    weekday = 6 if is_holiday(day) else day.weekday()
    anchor = _night_stay(stays, day)
    if anchor is None:
        return DayRecord(day, weekday, True, None, None, None, None, None, None, None)
    departure = anchor[1] if anchor[1] < day_end else None
    if departure is None:
        return DayRecord(day, weekday, False, None, None, None, None, None, None, None)
    unplugged = [
        end for end in session_ends if max(day_start, departure - UNPLUG_WINDOW) <= end <= departure
    ]
    if unplugged:
        departure = unplugged[-1]
    departure_soc = _soc_between(soc, departure - DEPARTURE_SOC_LOOKBACK, departure, last=True)

    # Rückkehr: Beginn des Aufenthalts, der die folgende Nacht umfasst (Ruling T4-2), auch nach
    # einer Abfahrt vor 03:00; die Ankunft ist dieselbe Rückkehr, wenn sie auf den Tag fällt
    night_stay = _night_stay(stays, day + timedelta(days=1))
    return_at = night_stay[0] if night_stay is not None else None
    return_soc = _arrival_soc(soc, session_starts, return_at) if return_at else None
    arrival = return_at if return_at is not None and day_start <= return_at < day_end else None
    arrival_soc = _arrival_soc(soc, session_starts, arrival) if arrival else None
    net: float | None = None
    trip: TripSoc | None = None
    if departure_soc is not None and return_soc is not None and return_at is not None:
        net = departure_soc - return_soc
        trip = _trip_soc(soc, home, departure, departure_soc, return_at, return_soc)
    return DayRecord(
        day,
        weekday,
        False,
        departure,
        departure_soc,
        arrival,
        arrival_soc,
        return_at,
        return_soc,
        trip.consumption_pp if trip else None,
        net,
        trip.charged_away_pp if trip else None,
        trip.drawdown_pp if trip else None,
    )
