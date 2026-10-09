"""Abrufplan der Preise (Spec 6.7): stündlich, zwischen 12:30 und 16:00 alle 15 min, bis der
Folgetag von smartENERGY vollständig vorliegt."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

import structlog

from bindaems.app.prices.pipeline import PricePipeline, PriceStatus
from bindaems.app.prices.store import PriceStore
from bindaems.app.schedule import next_local
from bindaems.shared.timeutil import LOCAL_TZ, Clock

log = structlog.get_logger(__name__)

HOURLY_MINUTE = 2
DENSE_FROM = time(12, 30)
DENSE_UNTIL = time(16, 0)
RETRY_S = 300.0


def next_price_fetch(now: datetime, tomorrow_complete: bool) -> datetime:
    def accept(local: datetime) -> bool:
        if local.minute == HOURLY_MINUTE:
            return True
        dense = not tomorrow_complete and DENSE_FROM <= local.time() < DENSE_UNTIL
        return dense and local.minute % 15 == 0

    return next_local(now, accept)


def _succeeded(status: PriceStatus) -> bool:
    if status.last_success is None:
        return False
    return status.last_attempt is None or status.last_success >= status.last_attempt


class PriceScheduler:
    def __init__(
        self,
        pipeline: PricePipeline,
        store: PriceStore,
        clock: Clock,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pipeline = pipeline
        self._store = store
        self._clock = clock
        self._sleep = sleep

    async def run(self) -> None:
        """Sofort abrufen, dann nach Plan; nach einem Fehlschlag spätestens nach 5 min."""
        while True:
            succeeded = await self._refresh()
            now = self._clock.now()
            wait = (next_price_fetch(now, await self._tomorrow_complete(now)) - now).total_seconds()
            if not succeeded:
                wait = min(wait, RETRY_S)
            await self._sleep(max(wait, 0.0))

    async def _refresh(self) -> bool:
        try:
            return _succeeded(await self._pipeline.refresh())
        except Exception:  # der Abrufplan darf nie stehen bleiben
            log.exception("Preisabruf fehlgeschlagen")
            return False

    async def _tomorrow_complete(self, now: datetime) -> bool:
        tomorrow = now.astimezone(LOCAL_TZ).date() + timedelta(days=1)
        try:
            return await asyncio.to_thread(self._store.complete_day, tomorrow, "primary")
        except Exception:
            log.exception("Prüfung der gespeicherten Preise fehlgeschlagen")
            return False
