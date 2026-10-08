"""Gemeinsame Bausteine aller Adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
