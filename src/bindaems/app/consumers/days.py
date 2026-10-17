"""Energie je Verbraucher und lokalem Tag (Plan 4a, Entscheidung 8).

Die app speichert die Energie je Verbraucher in ``consumer_day``: nachts für gestern, beim Start
für die fehlenden Tage der letzten 31. Es gilt dieselbe Rechnung wie für „heute“
(``EnergyCache.window_energy``); der Tag ist ``[lokale Mitternacht, nächste Mitternacht)``, an
Tagen der Zeitumstellung also 23 oder 25 Stunden. Ältere Tage bleiben leer, denn die Rohdaten in
InfluxDB halten 90 Tage. Heute und die Zukunft werden nie gespeichert.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

from sqlalchemy import Engine, delete, insert, select

from bindaems.app.consumers.energy import UNREADABLE, EnergyCache
from bindaems.app.consumers.service import Consumer, ConsumerService
from bindaems.app.db.schema import consumer_day
from bindaems.shared.timeutil import LOCAL_TZ, Clock, local_day_slots

FILL_DAYS: Final = 31  # so viele Tage zurück füllt der Start auf
NO_DAY: Final = "Tag nicht gespeichert"


class ConsumerDays:
    def __init__(
        self, engine: Engine, cache: EnergyCache, consumers: ConsumerService, clock: Clock
    ) -> None:
        self._engine = engine
        self._cache = cache
        self._consumers = consumers
        self._clock = clock

    def _today(self) -> date:
        return self._clock.now().astimezone(LOCAL_TZ).date()

    async def store(self, day: date) -> int:
        """Rechnet ``day`` und ersetzt seine Zeilen; liefert die Zahl der geschriebenen Zeilen.

        Heute und die Zukunft werden nicht gespeichert (0). Ein Verbraucher, dessen Verlauf nicht
        lesbar war, bekommt keine neue Zeile und behält die alte, damit ein vorübergehender Fehler
        keinen Tag mit einem falschen Grund belegt; ``fill_missing`` holt ihn nach (nachts für die
        letzten Tage, beim Start für die letzten 31).
        """
        if day >= self._today():
            return 0
        consumers = await asyncio.to_thread(self._consumers.list)
        if not consumers:
            return 0
        start, end = local_day_slots(day)[0], local_day_slots(day + timedelta(days=1))[0]
        energy, _house = await self._cache.window_energy(consumers, start, end)
        return await asyncio.to_thread(self._write, day, energy)

    def _write(self, day: date, energy: dict[int, tuple[float | None, str | None]]) -> int:
        readable = {cid: value for cid, value in energy.items() if value[1] != UNREADABLE}
        key = day.isoformat()
        with self._engine.begin() as conn:
            # auch die Zeilen gelöschter Verbraucher gehen; nur unlesbare behalten ihre alte Zeile
            conn.execute(
                delete(consumer_day)
                .where(consumer_day.c.day == key)
                .where(consumer_day.c.consumer_id.not_in(set(energy) - set(readable)))
            )
            if readable:
                conn.execute(
                    insert(consumer_day),
                    [
                        {"day": key, "consumer_id": cid, "kwh": kwh, "note": note}
                        for cid, (kwh, note) in readable.items()
                    ],
                )
        return len(readable)

    async def fill_missing(self, today: date, days: int = FILL_DAYS) -> int:
        """Speichert die Tage ``today - days … today - 1``, an denen ein Verbraucher keine Zeile
        hat (vom ältesten zum jüngsten); liefert die Zahl der Tage mit neuen Zeilen."""
        consumers = await asyncio.to_thread(self._consumers.list)
        if not consumers:
            return 0
        wanted = [today - timedelta(days=n) for n in range(days, 0, -1)]
        missing = await asyncio.to_thread(self._missing, wanted, consumers)
        filled = 0
        for day in missing:
            if await self.store(day):
                filled += 1
        return filled

    def _missing(self, wanted: Sequence[date], consumers: Sequence[Consumer]) -> list[date]:
        if not wanted:
            return []
        query = (
            select(consumer_day.c.day, consumer_day.c.consumer_id)
            .where(consumer_day.c.day >= wanted[0].isoformat())
            .where(consumer_day.c.day <= wanted[-1].isoformat())
        )
        with self._engine.connect() as conn:
            stored = {(row.day, row.consumer_id) for row in conn.execute(query)}
        ids = [consumer.id for consumer in consumers]
        return [day for day in wanted if any((day.isoformat(), cid) not in stored for cid in ids)]

    def energy(self, first: date, last: date) -> dict[int, tuple[float | None, str | None]]:
        """Summe je Verbraucher über ``first … last`` (nur gespeicherte Tage; Schlüssel: alle
        Verbraucher mit einer Zeile im Zeitraum).

        Ein Tag ohne Wert (oder ohne Zeile) macht die Summe ``None`` mit dem Grund des ersten
        solchen Tages.
        """
        if first > last:
            return {}
        query = (
            select(consumer_day)
            .where(consumer_day.c.day >= first.isoformat())
            .where(consumer_day.c.day <= last.isoformat())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        by_consumer: dict[int, dict[str, tuple[float | None, str | None]]] = {}
        for row in rows:
            by_consumer.setdefault(row.consumer_id, {})[row.day] = (row.kwh, row.note)
        wanted = [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]
        result: dict[int, tuple[float | None, str | None]] = {}
        for cid, per_day in by_consumer.items():
            values: list[float] = []
            reason: str | None = None
            for key in wanted:
                kwh, note = per_day.get(key, (None, NO_DAY))
                if kwh is None:
                    reason = note or NO_DAY
                    break
                values.append(kwh)
            result[cid] = (
                (None, reason) if reason is not None else (round(math.fsum(values), 3), None)
            )
        return result
