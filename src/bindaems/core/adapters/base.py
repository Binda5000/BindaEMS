"""Gemeinsame Bausteine aller Adapter."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import structlog

log = structlog.get_logger("bindaems.core.adapters")


@dataclass
class AdapterHealth:
    name: str
    connected: bool = False
    last_ok: datetime | None = None
    last_error: str | None = None
    error_count: int = 0


class Adapter(Protocol):
    name: str

    async def run(self) -> None: ...

    def health(self) -> AdapterHealth: ...


class Backoff:
    """Exponentielle Wartezeit für Wiederverbindungen (1 s, 2 s, 4 s … bis ``maximum``)."""

    def __init__(self, initial: float = 1.0, maximum: float = 60.0, factor: float = 2.0) -> None:
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._current = initial

    def next(self) -> float:
        delay = self._current
        self._current = min(self._current * self._factor, self._maximum)
        return delay

    def reset(self) -> None:
        self._current = self._initial


class StatusLog:
    """Protokolliert Zustandswechsel eines Adapters für den Betrieb.

    „Adapter verbunden“ nur beim Wechsel; ein Fehler bei neuem Text, gleiche Fehler höchstens
    alle ``REPEAT_S`` Sekunden – so bleibt das Log bei langen Ausfällen lesbar.
    """

    REPEAT_S = 300.0

    def __init__(self, adapter: str, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._adapter = adapter
        self._monotonic = monotonic
        self._connected = False
        self._last_error: str | None = None
        self._last_logged = 0.0

    def connected(self) -> None:
        if not self._connected:
            log.info("Adapter verbunden", adapter=self._adapter)
        self._connected = True
        self._last_error = None

    def failed(self, error: str, retry_in_s: float) -> None:
        now = self._monotonic()
        if error != self._last_error or now - self._last_logged >= self.REPEAT_S:
            log.warning("Adapter-Fehler", adapter=self._adapter, error=error, retry_in_s=retry_in_s)
            self._last_error = error
            self._last_logged = now
        self._connected = False
