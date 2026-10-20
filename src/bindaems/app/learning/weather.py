"""Wetterarchiv: je Slot die beste bekannte Lufttemperatur (Plan 5a, Entscheidung 6).

Jede PV-Prognose schreibt die Temperatur ihrer Slots bis 6 h nach der Ausgabe, auch die des Vortags
aus ``past_days=1``; eine spätere Ausgabe ersetzt eine frühere. So steht für die Vergangenheit der
beste bekannte Wert, nahe am Ist. Das Lastmodell lernt mit ihm (Task 9). Beim ersten Start holt die
Nachholung die Temperatur der letzten 92 Tage einmal von Open-Meteo nach.

Synchron wie die anderen Archive: Aufrufer in der Ereignisschleife nehmen ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta

import structlog
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.db.schema import weather_slot
from bindaems.app.forecast.openmeteo import WeatherSlot
from bindaems.shared.timeutil import ensure_utc

log = structlog.get_logger(__name__)

LEAD = timedelta(hours=6)  # so weit nach der Ausgabe gilt die Prognose als „nahe am Ist“
BACKFILL_MIN_AGE = timedelta(days=56)  # so alt muss das Archiv sein, damit nichts nachzuholen ist


class WeatherArchive:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, slots: Sequence[WeatherSlot], issued_at: datetime) -> int:
        """Nimmt die Temperatur der Slots bis 6 h nach ``issued_at`` auf und ersetzt damit
        Werte aus älteren Ausgaben. Gibt die Zahl der geschriebenen Zeilen zurück."""
        issued = ensure_utc(issued_at)
        values = {ensure_utc(slot.slot_start): slot.temp_c for slot in slots}
        return self._write(values, issued, replace=True)

    def temperatures(self, start: datetime, end: datetime) -> dict[datetime, float]:
        """Temperatur der Slots in ``[start, end)``."""
        query = (
            select(weather_slot.c.slot_start, weather_slot.c.temp_c)
            .where(weather_slot.c.slot_start >= ensure_utc(start))
            .where(weather_slot.c.slot_start < ensure_utc(end))
            .order_by(weather_slot.c.slot_start)
        )
        with self._engine.connect() as conn:
            return {row.slot_start: row.temp_c for row in conn.execute(query)}

    def oldest(self) -> datetime | None:
        """Beginn des ältesten archivierten Slots; ohne Archiv ``None``."""
        with self._engine.connect() as conn:
            oldest: datetime | None = conn.execute(
                select(func.min(weather_slot.c.slot_start))
            ).scalar_one()
        return oldest

    async def backfill(
        self, fetch: Callable[[], Awaitable[Mapping[datetime, float]]], now: datetime
    ) -> int:
        """Holt die Temperatur der letzten Monate nach, wenn das Archiv jünger als 56 Tage ist.

        Vorhandene Slots bleiben, denn sie stammen aus einer Prognose mit besserem Wissen als die
        Rückschau. Fehler von ``fetch`` gehen an den Aufrufer; das Archiv bleibt dann unverändert,
        und der nächste Start versucht es erneut. Gibt die Zahl der geschriebenen Zeilen zurück.
        """
        moment = ensure_utc(now)
        oldest = await asyncio.to_thread(self.oldest)
        if oldest is not None and oldest <= moment - BACKFILL_MIN_AGE:
            return 0
        fetched = await fetch()
        values = {ensure_utc(slot): temp_c for slot, temp_c in fetched.items()}
        return await asyncio.to_thread(self._write, values, moment, replace=False)

    def _write(self, values: Mapping[datetime, float], issued: datetime, *, replace: bool) -> int:
        cutoff = issued + LEAD
        params = [
            {"slot_start": slot, "temp_c": float(temp_c), "issued_at": issued}
            for slot, temp_c in values.items()
            if slot < cutoff
        ]
        if not params:
            return 0
        statement = sqlite_insert(weather_slot)
        if replace:
            new_row = statement.excluded
            # nur eine gleich neue oder neuere Ausgabe ersetzt; eine ältere (Nachzügler) nicht
            statement = statement.on_conflict_do_update(
                index_elements=[weather_slot.c.slot_start],
                set_={"temp_c": new_row.temp_c, "issued_at": new_row.issued_at},
                where=weather_slot.c.issued_at <= new_row.issued_at,
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=[weather_slot.c.slot_start])
        with self._engine.begin() as conn:
            result = conn.execute(statement, params)
        return result.rowcount
