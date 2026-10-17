"""Beobachtung der Wallboxen: Belegung als ``assignment_log`` (Plan 4a, Präzisierung 6).

Nach jeder Antwort von Health (alle 10 s) sieht der Tracker je Wallbox, ob ein Auto steckt und
wer dort steht (``wallboxes[].occupant`` und ``assignment``). Ändert sich ``(occupant, kind)``
einer Wallbox, kommt eine Zeile in ``assignment_log``; die erste Beobachtung nach dem Start
schreibt immer. Das Schreiben läuft in einem Thread, ``wake`` setzt nur ein Ereignis.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import Engine, insert

from bindaems.app.charging.plug import wallbox_connected
from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import assignment_log
from bindaems.shared.config import Config
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WallboxSeen:
    """Eine Beobachtung an einer Wallbox; ``None`` heißt unbekannt (bzw. niemand)."""

    wallbox: str
    connected: bool | None
    occupant: str | None  # Schlüssel des Fahrzeugs oder ``guest``
    kind: str | None  # Wert von ``AssignmentKind``


def observe(cfg: Config, live: LiveState) -> list[WallboxSeen]:
    """Steckzustand und Belegung aller Wallboxen, in der Reihenfolge von ``config.yaml``.

    Die Belegung kommt aus ``health["wallboxes"]``; fehlt Health oder der Eintrag, ist sie
    unbekannt.
    """
    health = live.health if live.connected else None
    listed = health.get("wallboxes") if health is not None else None
    entries: dict[str, dict[str, Any]] = {}
    if isinstance(listed, list):
        for entry in listed:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                entries[entry["id"]] = entry
    result: list[WallboxSeen] = []
    for key, wallbox in cfg.wallboxes.items():
        entry = entries.get(key, {})
        occupant, kind = entry.get("occupant"), entry.get("assignment")
        result.append(
            WallboxSeen(
                key,
                wallbox_connected(wallbox, key, live.value),
                occupant if isinstance(occupant, str) and occupant else None,
                kind if isinstance(kind, str) and kind else None,
            )
        )
    return result


class ChargingTracker:
    """Zeichnet die Belegung der Wallboxen auf."""

    def __init__(self, engine: Engine, clock: Clock, cfg: Config, live: LiveState) -> None:
        self._engine = engine
        self._clock = clock
        self._cfg = cfg
        self._live = live
        self._wake = asyncio.Event()
        # zuletzt geschriebene Belegung je Wallbox; nach dem Start leer, die erste schreibt immer
        self._assigned: dict[str, tuple[str, str]] = {}

    def wake(self) -> None:
        """Health-Handler: eine neue Beobachtung ist fällig (blockiert nicht)."""
        self._wake.set()

    async def run(self) -> None:
        """Beobachtet nach jeder Antwort von Health; ein Fehler kommt ins Log."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                now, seen = self._clock.now(), observe(self._cfg, self._live)
                await asyncio.to_thread(self.record, now, seen)
            except Exception:
                log.exception("Beobachtung der Wallboxen fehlgeschlagen")

    def record(self, now: datetime, seen: Sequence[WallboxSeen]) -> None:
        """Verarbeitet eine Beobachtung (synchron; ``run`` ruft es in einem Thread)."""
        with self._engine.begin() as conn:
            for item in seen:
                assigned = (item.occupant or "", item.kind or "")
                if self._assigned.get(item.wallbox) != assigned:
                    conn.execute(
                        insert(assignment_log).values(
                            ts=now, wallbox=item.wallbox, occupant=assigned[0], kind=assigned[1]
                        )
                    )
                    self._assigned[item.wallbox] = assigned
