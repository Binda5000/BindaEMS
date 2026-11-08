"""Energie je Quelle und lokalem Tag (Plan 4a, Entscheidung 8).

Die app speichert die Energie je Leistungsquelle (Verbraucher, PV) in einer Tagestabelle; für die
Verbraucher ``consumer_day``: nachts die fehlenden Tage der
letzten 3 (gestern, Fehler nachholen), beim Start die der letzten 31. Es gilt dieselbe Rechnung
wie für „heute“ (``EnergyCache.window_energy``); der Tag ist
``[lokale Mitternacht, nächste Mitternacht)``, an Tagen der Zeitumstellung also 23 oder 25
Stunden. Ältere Tage bleiben leer, denn die Rohdaten in InfluxDB halten 90 Tage. Heute und die
Zukunft werden nie gespeichert.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Final

from sqlalchemy import Engine, Table, delete, insert, select

from bindaems.app.consumers.energy import NO_HISTORY, UNREADABLE, EnergyCache
from bindaems.app.consumers.service import ConsumerService
from bindaems.app.consumers.source import PowerSource
from bindaems.app.db.schema import consumer_day
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots

FILL_DAYS: Final = 31  # so viele Tage zurück füllt der Start auf
NO_DAY: Final = "Tag nicht gespeichert"


class SourceDays:
    """Tagesenergie je Quelle in ``table`` (Spalten ``day``, ``id_column``, ``kwh``, ``note``).

    Mit ``total_id`` speichert ``store`` zusätzlich die Energie von ``derived/pv_total`` unter
    dieser ID."""

    def __init__(
        self,
        engine: Engine,
        cache: EnergyCache,
        list_sources: Callable[[], Sequence[PowerSource]],
        table: Table,
        id_column: str,
        clock: Clock,
        *,
        total_id: int | None = None,
    ) -> None:
        self._engine = engine
        self._cache = cache
        self._list = list_sources
        self._table = table
        self._id = table.c[id_column]
        self._clock = clock
        self._total_id = total_id

    def _today(self) -> date:
        return self._clock.now().astimezone(LOCAL_TZ).date()

    async def store(self, day: date) -> int:
        """Rechnet ``day`` und ersetzt seine Zeilen; liefert die Zahl der geschriebenen Zeilen.

        Heute und die Zukunft werden nicht gespeichert (0). Eine Quelle, deren Verlauf nicht
        lesbar war, bekommt keine neue Zeile und behält die alte, damit ein vorübergehender Fehler
        keinen Tag mit einem falschen Grund belegt; ``fill_missing`` holt ihn nach (nachts für die
        letzten Tage, beim Start für mehr).
        """
        if day >= self._today():
            return 0
        sources = await asyncio.to_thread(self._list)
        if not sources and self._total_id is None:
            return 0
        start, end = local_day_slots(day)[0], local_day_slots(day + timedelta(days=1))[0]
        result = await self._cache.window_energy(sources, start, end)
        energy = dict(result.per_source)
        if self._total_id is not None:
            total = result.pv_total_kwh
            energy[self._total_id] = (total, None if total is not None else NO_HISTORY)
        return await asyncio.to_thread(self._write, day, energy)

    def _write(self, day: date, energy: dict[int, tuple[float | None, str | None]]) -> int:
        readable = {sid: value for sid, value in energy.items() if value[1] != UNREADABLE}
        key = day.isoformat()
        table = self._table
        with self._engine.begin() as conn:
            # auch die Zeilen gelöschter Quellen gehen; nur unlesbare behalten ihre alte Zeile
            conn.execute(
                delete(table)
                .where(table.c.day == key)
                .where(self._id.not_in(set(energy) - set(readable)))
            )
            if readable:
                conn.execute(
                    insert(table),
                    [
                        {"day": key, self._id.name: sid, "kwh": kwh, "note": note}
                        for sid, (kwh, note) in readable.items()
                    ],
                )
        return len(readable)

    async def fill_missing(self, today: date, days: int = FILL_DAYS) -> int:
        """Speichert die Tage ``today - days … today - 1``, an denen eine Quelle keine Zeile
        hat (vom ältesten zum jüngsten); liefert die Zahl der Tage mit neuen Zeilen."""
        sources = await asyncio.to_thread(self._list)
        if not sources:
            return 0
        wanted = [today - timedelta(days=n) for n in range(days, 0, -1)]
        missing = await asyncio.to_thread(self._missing, wanted, sources)
        filled = 0
        for day in missing:
            if await self.store(day):
                filled += 1
        return filled

    def _missing(self, wanted: Sequence[date], sources: Sequence[PowerSource]) -> list[date]:
        if not wanted:
            return []
        table = self._table
        query = (
            select(table.c.day, self._id)
            .where(table.c.day >= wanted[0].isoformat())
            .where(table.c.day <= wanted[-1].isoformat())
        )
        with self._engine.connect() as conn:
            stored = {(row[0], row[1]) for row in conn.execute(query)}
        ids = [source.id for source in sources]
        return [day for day in wanted if any((day.isoformat(), sid) not in stored for sid in ids)]

    def _rows(
        self, first: date, last: date
    ) -> dict[int, dict[str, tuple[float | None, str | None]]]:
        table = self._table
        query = (
            select(table.c.day, self._id, table.c.kwh, table.c.note)
            .where(table.c.day >= first.isoformat())
            .where(table.c.day <= last.isoformat())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        by_source: dict[int, dict[str, tuple[float | None, str | None]]] = {}
        for day, sid, kwh, note in rows:
            by_source.setdefault(sid, {})[day] = (kwh, note)
        return by_source

    def daily(
        self, first: date, last: date
    ) -> dict[int, dict[date, tuple[float | None, str | None]]]:
        """Gespeicherte Werte je Quelle und Tag in ``first … last`` (ohne Summe; Tage ohne Zeile
        fehlen)."""
        if first > last:
            return {}
        return {
            sid: {date.fromisoformat(day): value for day, value in per_day.items()}
            for sid, per_day in self._rows(first, last).items()
        }

    def energy(self, first: date, last: date) -> dict[int, tuple[float | None, str | None]]:
        """Summe je Quelle über ``first … last`` (nur gespeicherte Tage; Schlüssel: alle
        Quellen mit einer Zeile im Zeitraum).

        Ein Tag ohne Wert (oder ohne Zeile) macht die Summe ``None`` mit dem Grund des ersten
        solchen Tages.
        """
        if first > last:
            return {}
        by_source = self._rows(first, last)
        wanted = [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]
        result: dict[int, tuple[float | None, str | None]] = {}
        for sid, per_day in by_source.items():
            values: list[float] = []
            reason: str | None = None
            for key in wanted:
                kwh, note = per_day.get(key, (None, NO_DAY))
                if kwh is None:
                    reason = note or NO_DAY
                    break
                values.append(kwh)
            result[sid] = (
                (None, reason) if reason is not None else (round(math.fsum(values), 3), None)
            )
        return result


class ConsumerDays(SourceDays):
    def __init__(
        self, engine: Engine, cache: EnergyCache, consumers: ConsumerService, clock: Clock
    ) -> None:
        super().__init__(engine, cache, consumers.list, consumer_day, "consumer_id", clock)
