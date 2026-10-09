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
