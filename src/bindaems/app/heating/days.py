"""Heizungstage: Brennerstunden, Heizgradtage und Klima-Strom je lokalem Tag (Plan Ölheizung).

Der Brennerstatus steht in InfluxDB ``status`` nur bei Änderung (``thermal.<gerät>.oil_burner``)
und wird als Stufenfunktion gelesen (Entscheidung 2). Gezählt wird nur in Viertelstunden, in denen
die Quelle lebt, also mindestens ein Messwert ``actual_c`` des Geräts in ``thermal`` steht: Fällt
das C.M.I. bei laufendem Brenner aus, ist diese Zeit unbekannt, nicht Brennerzeit. Der Anfangswert
ist der letzte Wert vor dem Tag, sonst der gespeicherte Endzustand des Vortags, sonst unbekannt.

Heizgradtage (Heizgrenze 15 °C) und Tagesmittel kommen aus dem Wetterarchiv (Entscheidung 5), der
Strom der Klimaanlage beim Heizen aus ``read_thermal_slots`` (Entscheidung 7). InfluxDB ``raw``
hält 90 Tage und das Wetterarchiv 120, darum speichert die app jeden vergangenen Tag dauerhaft in
``heating_day`` (nachts und beim Start die fehlenden, aufsteigend). Heute rechnet
``refresh_today`` bis jetzt und hält das Ergebnis im Speicher; heute wird nie gespeichert.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from bindaems.app.db.schema import heating_day
from bindaems.app.heating.fit import HEATING_LIMIT_C, HeatDay
from bindaems.app.history.influx import (
    RP_RAW,
    InfluxQueryError,
    InfluxReader,
    quote_ident,
    quote_str,
)
from bindaems.app.history.steps import StatusSteps, read_status_steps, series_rows
from bindaems.app.learning.thermal_data import ThermalDataError, read_thermal_slots
from bindaems.app.learning.weather import WeatherArchive
from bindaems.shared.config import OIL_BURNER_SIGNAL
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, local_day_slots

log = structlog.get_logger(__name__)

SLOT_H: Final = SLOT.total_seconds() / 3600
WEATHER_MIN_SHARE: Final = 0.9  # so viele Viertelstunden braucht ein Tag für Heizgradtage
UNCLEAR_NOTE_KWH: Final = 0.1  # ab so viel Strom ohne klare Betriebsart steht ein Hinweis am Tag
NOTE_MAX: Final = 80  # Länge der Spalte ``note``
NO_BURNER: Final = "Brennerstatus fehlt"
NO_SOURCE: Final = "Keine Messwerte der Quelle"


class HeatingDataError(Exception):
    """InfluxDB oder das Wetterarchiv ließen sich nicht lesen; es wird nichts gespeichert."""


def _unclear_note(kwh: float) -> str:
    return f"Klimaanlage ohne klare Betriebsart: {kwh:.1f} kWh".replace(".", ",")


class HeatingDays:
    """Rechnet und speichert die Heizungstage des Geräts ``device`` (Empfangstabelle mit
    ``oil_burner``). ``ac_power_known``: Die Tabelle hat ``power_kw``; sonst bleibt der Strom der
    Klimaanlage ``None``."""

    def __init__(
        self,
        engine: Engine,
        reader: InfluxReader,
        weather: WeatherArchive,
        device: str,
        clock: Clock,
        *,
        ac_power_known: bool,
    ) -> None:
        self._engine = engine
        self._reader = reader
        self._weather = weather
        self._device = device
        self._clock = clock
        self._ac_power_known = ac_power_known
        self._burner_id = f"thermal.{device}.{OIL_BURNER_SIGNAL}"
        self._today: HeatDay | None = None

    def _local_today(self) -> date:
        return self._clock.now().astimezone(LOCAL_TZ).date()

    async def compute(self, day: date, seed: bool | None) -> HeatDay:
        """Rechnet ``day`` (heute bis jetzt) und schreibt nichts. ``seed`` gilt als Anfangswert,
        wenn ``status`` vor dem Tag keinen Wert hat. Ein Lesefehler wirft ``HeatingDataError``."""
        day_start = local_day_slots(day)[0]
        day_end = local_day_slots(day + timedelta(days=1))[0]
        day_h = (day_end - day_start).total_seconds() / 3600
        now = self._clock.now()
        grid = [slot for slot in local_day_slots(day) if slot + SLOT <= now]
        if not grid:
            return HeatDay(day, None, 0.0, day_h, None, None, None, None, None)
        stop = grid[-1] + SLOT
        try:
            alive = await self._alive(day_start, stop)
            steps = (await read_status_steps(self._reader, [self._burner_id], day_start, stop))[
                self._burner_id
            ]
            temps = await asyncio.to_thread(self._weather.temperatures, day_start, stop)
            ac = (
                await read_thermal_slots(self._reader, self._weather, self._device, day_start, stop)
                if self._ac_power_known
                else None
            )
        except (InfluxQueryError, ThermalDataError, SQLAlchemyError) as exc:
            raise HeatingDataError(f"Heizungstag {day.isoformat()} nicht lesbar: {exc}") from exc

        notes: list[str] = []
        if steps.seed is None:
            steps = StatusSteps(seed, steps.times, steps.values)
        burner_end = steps.values[-1] if steps.values else steps.seed
        burner_h: float | None
        known_h = 0.0
        if steps.seed is None and not steps.values:
            burner_h, burner_end = None, None
            notes.append(NO_BURNER)
        else:
            burner_s = 0.0
            for slot in grid:
                if slot not in alive:
                    continue
                share = steps.share(slot, slot + SLOT)
                if share is None:
                    continue
                burner_s += share * SLOT_H
                known_h += SLOT_H
            burner_h = burner_s if known_h > 0 else None
            if burner_h is None:
                notes.append(NO_SOURCE)

        hdd_kd: float | None = None
        outdoor_mean_c: float | None = None
        values = [t for slot in grid if (t := temps.get(slot)) is not None and math.isfinite(t)]
        if values and len(values) >= WEATHER_MIN_SHARE * len(grid):
            hdd_kd = math.fsum(max(0.0, HEATING_LIMIT_C - t) * SLOT_H for t in values) / 24
            outdoor_mean_c = math.fsum(values) / len(values)

        ac_heat_el_kwh: float | None = None
        if ac is not None:
            ac_heat_el_kwh = (
                math.fsum(s.power_w for s in ac if s.mode == "heizen" and s.power_w is not None)
                * SLOT_H
                / 1000
            )
            unclear = (
                math.fsum(s.power_w for s in ac if s.mode is None and s.power_w is not None)
                * SLOT_H
                / 1000
            )
            if unclear > UNCLEAR_NOTE_KWH:
                notes.append(_unclear_note(unclear))

        note = "; ".join(notes)[:NOTE_MAX] if notes else None
        return HeatDay(
            day, burner_h, known_h, day_h, burner_end, hdd_kd, outdoor_mean_c, ac_heat_el_kwh, note
        )

    async def _alive(self, start: datetime, end: datetime) -> set[datetime]:
        """Viertelstunden (Beginn UTC) mit mindestens einem Messwert ``actual_c`` des Geräts."""
        # Bezeichner und Werte sind maskiert
        text = (
            f'SELECT count("actual_c") AS "n" FROM {quote_ident(RP_RAW)}."thermal" '  # noqa: S608
            f'WHERE "id" = {quote_str(self._device)} '
            f"AND time >= {epoch_ms(start)}ms AND time < {epoch_ms(end)}ms "
            "GROUP BY time(15m) fill(none)"
        )
        alive: set[datetime] = set()
        for ts, cells in series_rows(await self._reader.query(text)):
            count = cells.get("n")
            if isinstance(count, int | float) and not isinstance(count, bool) and count > 0:
                alive.add(ts)
        return alive

    async def fill_missing(self, today: date, days: int) -> int:
        """Rechnet und speichert die fehlenden Tage ``today - days … today - 1``, vom ältesten
        zum jüngsten (der Endzustand eines Tages ist der Anfangswert des nächsten). Ein Lesefehler
        bricht den Lauf ab; Gespeichertes bleibt, und der Tag kommt beim nächsten Lauf wieder
        dran. Gibt die Zahl der gespeicherten Tage zurück."""
        wanted = [today - timedelta(days=n) for n in range(days, 0, -1)]
        if not wanted:
            return 0
        stored = await asyncio.to_thread(self._stored_days, wanted[0], wanted[-1])
        ends: dict[date, bool | None] = {}
        filled = 0
        for day in wanted:
            if day in stored:
                continue
            previous = day - timedelta(days=1)
            seed = (
                ends[previous]
                if previous in ends
                else await asyncio.to_thread(self._stored_end, previous)
            )
            try:
                heat_day = await self.compute(day, seed)
            except HeatingDataError as exc:
                log.warning("Heizungstage nicht lesbar", error=str(exc))
                break
            await asyncio.to_thread(self._write, heat_day)
            ends[day] = heat_day.burner_end
            filled += 1
        return filled

    async def refresh_today(self) -> None:
        """Rechnet heute bis jetzt und hält das Ergebnis im Speicher; ein Lesefehler behält den
        bisherigen Stand und kommt ins Log."""
        today = self._local_today()
        seed = await asyncio.to_thread(self._stored_end, today - timedelta(days=1))
        try:
            self._today = await self.compute(today, seed)
        except HeatingDataError as exc:
            log.warning("Heizungstag heute nicht lesbar", error=str(exc))

    def today(self) -> HeatDay | None:
        """Heute bis zum letzten ``refresh_today``; vorher ``None``."""
        return self._today

    def period(self, first: date, last: date) -> list[HeatDay]:
        """Gespeicherte Tage in ``first … last`` vor heute plus heute bis jetzt, wenn heute im
        Zeitraum liegt und schon gerechnet ist; aufsteigend, Tage ohne Zeile fehlen."""
        today = self._local_today()
        days = self.rows(first, min(last, today - timedelta(days=1)))
        cached = self._today
        if cached is not None and cached.day == today and first <= today <= last:
            days.append(cached)
        return days

    def rows(self, first: date, last: date) -> list[HeatDay]:
        """Gespeicherte Tage in ``first … last``, aufsteigend (synchron)."""
        if first > last:
            return []
        query = (
            select(heating_day)
            .where(heating_day.c.day >= first.isoformat())
            .where(heating_day.c.day <= last.isoformat())
            .order_by(heating_day.c.day)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [
            HeatDay(
                day=date.fromisoformat(row["day"]),
                burner_h=row["burner_h"],
                known_h=row["known_h"],
                day_h=row["day_h"],
                burner_end=row["burner_end"],
                hdd_kd=row["hdd_kd"],
                outdoor_mean_c=row["outdoor_mean_c"],
                ac_heat_el_kwh=row["ac_heat_el_kwh"],
                note=row["note"],
            )
            for row in rows
        ]

    def _stored_days(self, first: date, last: date) -> set[date]:
        query = (
            select(heating_day.c.day)
            .where(heating_day.c.day >= first.isoformat())
            .where(heating_day.c.day <= last.isoformat())
        )
        with self._engine.connect() as conn:
            return {date.fromisoformat(row[0]) for row in conn.execute(query)}

    def _stored_end(self, day: date) -> bool | None:
        query = select(heating_day.c.burner_end).where(heating_day.c.day == day.isoformat())
        with self._engine.connect() as conn:
            value: bool | None = conn.execute(query).scalar_one_or_none()
        return value

    def _write(self, heat_day: HeatDay) -> None:
        values = {
            "day": heat_day.day.isoformat(),
            "burner_h": heat_day.burner_h,
            "known_h": heat_day.known_h,
            "day_h": heat_day.day_h,
            "burner_end": heat_day.burner_end,
            "hdd_kd": heat_day.hdd_kd,
            "outdoor_mean_c": heat_day.outdoor_mean_c,
            "ac_heat_el_kwh": heat_day.ac_heat_el_kwh,
            "note": heat_day.note,
        }
        statement = sqlite_insert(heating_day).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[heating_day.c.day],
            set_={key: statement.excluded[key] for key in values if key != "day"},
        )
        with self._engine.begin() as conn:
            conn.execute(statement)
