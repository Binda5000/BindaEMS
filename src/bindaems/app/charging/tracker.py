"""Beobachtung der Wallboxen: Belegung und Ladesessions (Plan 4a, Präzisierung 6).

Nach jeder Antwort von Health (alle 10 s) sieht der Tracker je Wallbox, ob ein Auto steckt und
wer dort steht (``wallboxes[].occupant`` und ``assignment``).

- Ändert sich ``(occupant, kind)`` einer Wallbox, kommt eine Zeile in ``assignment_log``; die erste
  Beobachtung nach dem Start schreibt immer.
- Steckt ein Auto und es gibt keine offene Session, beginnt eine; ist es abgezogen, endet die
  offene. Ist der Steckzustand unbekannt, bleibt alles, wie es ist, auch ``last_seen``.
- Liegt ``last_seen`` mehr als ``SESSION_GAP`` vor der Beobachtung (core oder app war aus), endet
  die offene Session bei ``last_seen``; steckt das Auto noch, beginnt eine neue. Beim Start
  liest der Tracker die offenen Sessions, die erste Beobachtung entscheidet so über Fortsetzung
  oder Ende.
- ``last_seen`` wird höchstens jede Minute gespeichert, Anfang und Ende sofort.
- Das Fahrzeug der Session ist das nicht leere ``occupant`` mit der längsten Dauer in ihrem
  Zeitraum (``session_vehicle``); es wird bei jeder Änderung der Belegung, beim Speichern von
  ``last_seen`` und beim Ende neu bestimmt. Hat kein Fahrzeug Zeit (etwa eine Session unter einer
  Minute, deren Insasse erst in der schließenden Beobachtung steht), zählt die letzte nicht leere
  Belegung der Wallbox zwischen Beginn und Ende.

Das Schreiben läuft in einem Thread, ``wake`` setzt nur ein Ereignis.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import Connection, Engine, desc, insert, or_, select, update

from bindaems.app.charging.plug import wallbox_connected
from bindaems.app.core_link import LiveState
from bindaems.app.db.schema import assignment_log, charging_session
from bindaems.shared.config import Config
from bindaems.shared.timeutil import Clock, ensure_utc

log = structlog.get_logger(__name__)

SESSION_GAP: Final = timedelta(minutes=30)
LAST_SEEN_EVERY: Final = timedelta(minutes=1)


@dataclass(frozen=True)
class WallboxSeen:
    """Eine Beobachtung an einer Wallbox; ``None`` heißt unbekannt (bzw. niemand)."""

    wallbox: str
    connected: bool | None
    occupant: str | None  # Schlüssel des Fahrzeugs oder ``guest``
    kind: str | None  # Wert von ``AssignmentKind``


@dataclass(frozen=True)
class StoredSession:
    """Eine gespeicherte Ladesession; ``end`` ist ``None``, solange sie läuft."""

    id: int
    wallbox: str
    vehicle: str | None  # Schlüssel des Fahrzeugs, ``guest`` oder ``None`` (unbekannt)
    start: datetime
    end: datetime | None
    last_seen: datetime


@dataclass
class _Open:
    """Eine offene Session im Speicher."""

    id: int
    start: datetime
    last_seen: datetime
    saved: datetime  # ``last_seen`` in der Datenbank


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


def session_vehicle(
    log: Sequence[tuple[datetime, str | None]], start: datetime, end: datetime
) -> str | None:
    """Das Fahrzeug mit der längsten Dauer in ``[start, end)``; bei Gleichstand das spätere.

    ``log`` sind die Änderungen der Belegung ``(Zeitpunkt, occupant)``; ein Eintrag gilt bis zum
    nächsten, der letzte bis ``end``, und ein Eintrag vor ``start`` gilt ab ``start``. Ohne
    Fahrzeug (``None``) zählt die Zeit nicht; ohne Zeit mit Fahrzeug ist das Ergebnis ``None``.
    """
    entries = sorted(log, key=lambda entry: entry[0])
    held: dict[str, tuple[timedelta, datetime]] = {}  # Dauer, Ende des letzten Abschnitts
    for index, (ts, occupant) in enumerate(entries):
        until = entries[index + 1][0] if index + 1 < len(entries) else end
        first, last = max(ts, start), min(until, end)
        if occupant and last > first:
            duration = held[occupant][0] if occupant in held else timedelta(0)
            held[occupant] = (duration + (last - first), last)
    return max(held, key=lambda occupant: held[occupant], default=None)


class ChargingTracker:
    """Zeichnet Belegung und Ladesessions der Wallboxen auf."""

    def __init__(self, engine: Engine, clock: Clock, cfg: Config, live: LiveState) -> None:
        self._engine = engine
        self._clock = clock
        self._cfg = cfg
        self._live = live
        self._wake = asyncio.Event()
        # zuletzt geschriebene Belegung je Wallbox; nach dem Start leer, die erste schreibt immer
        self._assigned: dict[str, tuple[str, str]] = {}
        self._open: dict[str, _Open] = {}
        self._loaded = False

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
            if not self._loaded:
                self._load(conn)
            for wallbox, current in list(self._open.items()):
                if now - current.last_seen > SESSION_GAP:  # Pause: Ende bei der letzten Sicht
                    self._close(conn, wallbox, current.last_seen)
            for item in seen:
                changed = self._log_assignment(conn, now, item)
                self._track(conn, now, item, changed)

    def sessions(
        self, start: datetime, end: datetime, vehicle: str | None = None
    ) -> list[StoredSession]:
        """Die Sessions, die ``[start, end)`` überdecken, neueste zuerst; ``vehicle`` ist ein
        Schlüssel (``guest`` eingeschlossen)."""
        query = (
            select(charging_session)
            .where(
                charging_session.c.start < end,
                or_(charging_session.c.end.is_(None), charging_session.c.end > start),
            )
            .order_by(desc(charging_session.c.start), desc(charging_session.c.id))
        )
        if vehicle is not None:
            query = query.where(charging_session.c.vehicle == vehicle)
        with self._engine.connect() as conn:
            return [_stored(row) for row in conn.execute(query)]

    def open_sessions(self) -> list[StoredSession]:
        """Die offenen Sessions (``end`` leer), neueste zuerst; unabhängig davon, wie lange sie
        schon laufen."""
        query = (
            select(charging_session)
            .where(charging_session.c.end.is_(None))
            .order_by(desc(charging_session.c.start), desc(charging_session.c.id))
        )
        with self._engine.connect() as conn:
            return [_stored(row) for row in conn.execute(query)]

    # --- Belegung ---------------------------------------------------------------------------

    def _log_assignment(self, conn: Connection, now: datetime, item: WallboxSeen) -> bool:
        """Schreibt die Belegung, wenn sie sich geändert hat; ``True`` bei einer Zeile."""
        assigned = (item.occupant or "", item.kind or "")
        if self._assigned.get(item.wallbox) == assigned:
            return False
        conn.execute(
            insert(assignment_log).values(
                ts=now, wallbox=item.wallbox, occupant=assigned[0], kind=assigned[1]
            )
        )
        self._assigned[item.wallbox] = assigned
        return True

    # --- Sessions ---------------------------------------------------------------------------

    def _load(self, conn: Connection) -> None:
        """Liest die offenen Sessions; mehrere je Wallbox (Rest eines Fehlers) endet bis auf die
        neueste bei ihrer letzten Sicht."""
        rows = conn.execute(
            select(charging_session)
            .where(charging_session.c.end.is_(None))
            .order_by(charging_session.c.start, charging_session.c.id)
        ).all()
        for row in rows:
            earlier = self._open.pop(row.wallbox, None)
            if earlier is not None:
                self._finish(conn, row.wallbox, earlier, earlier.last_seen)
            start, last_seen = ensure_utc(row.start), ensure_utc(row.last_seen)
            self._open[row.wallbox] = _Open(row.id, start, last_seen, last_seen)
        self._loaded = True

    def _track(self, conn: Connection, now: datetime, item: WallboxSeen, changed: bool) -> None:
        current = self._open.get(item.wallbox)
        if item.connected is False:
            if current is not None:
                self._close(conn, item.wallbox, now)
            return
        if current is None:
            if item.connected:
                self._begin(conn, now, item)
            return
        if item.connected:
            current.last_seen = now
        if changed or (item.connected and now - current.saved >= LAST_SEEN_EVERY):
            self._save(conn, item.wallbox, current, now)

    def _begin(self, conn: Connection, now: datetime, item: WallboxSeen) -> None:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(
                wallbox=item.wallbox,
                vehicle=item.occupant or "",
                start=now,
                end=None,
                last_seen=now,
            )
        ).scalar_one()
        self._open[item.wallbox] = _Open(session_id, now, now, now)

    def _save(self, conn: Connection, wallbox: str, current: _Open, now: datetime) -> None:
        """Speichert ``last_seen`` und das Fahrzeug; solange kein Fahrzeug Zeit hat, gilt
        vorläufig das, das jetzt dort steht."""
        vehicle = self._vehicle(conn, wallbox, current.start, now)
        if vehicle is None:
            vehicle = self._assigned.get(wallbox, ("", ""))[0] or None
        conn.execute(
            update(charging_session)
            .where(charging_session.c.id == current.id)
            .values(vehicle=vehicle or "", last_seen=current.last_seen)
        )
        current.saved = current.last_seen

    def _close(self, conn: Connection, wallbox: str, end: datetime) -> None:
        current = self._open.pop(wallbox)
        self._finish(conn, wallbox, current, end)

    def _finish(self, conn: Connection, wallbox: str, current: _Open, end: datetime) -> None:
        vehicle = self._vehicle(conn, wallbox, current.start, end)
        conn.execute(
            update(charging_session)
            .where(charging_session.c.id == current.id)
            .values(vehicle=vehicle or "", end=end, last_seen=max(current.last_seen, end))
        )

    def _vehicle(
        self, conn: Connection, wallbox: str, start: datetime, end: datetime
    ) -> str | None:
        """Das Fahrzeug der Session nach ``assignment_log`` in ``[start, end)``."""
        table = assignment_log
        prior = conn.execute(
            select(table.c.ts, table.c.occupant)
            .where(table.c.wallbox == wallbox, table.c.ts <= start)
            .order_by(desc(table.c.ts), desc(table.c.id))
            .limit(1)
        ).all()
        inside = conn.execute(
            select(table.c.ts, table.c.occupant)
            .where(table.c.wallbox == wallbox, table.c.ts > start, table.c.ts < end)
            .order_by(table.c.ts, table.c.id)
        ).all()
        entries = [(ensure_utc(row.ts), row.occupant or None) for row in [*prior, *inside]]
        vehicle = session_vehicle(entries, start, end)
        if vehicle is not None:
            return vehicle
        # Keine Zeit mit Fahrzeug (Session unter einer Minute, der Insasse steht erst in der
        # schließenden Beobachtung bei ``end``): die letzte bekannte Belegung in ``[start, end]``
        last = conn.execute(
            select(table.c.occupant)
            .where(
                table.c.wallbox == wallbox,
                table.c.ts >= start,
                table.c.ts <= end,
                table.c.occupant != "",
            )
            .order_by(desc(table.c.ts), desc(table.c.id))
            .limit(1)
        ).scalar()
        return last or None


def _stored(row: Any) -> StoredSession:
    return StoredSession(
        row.id,
        row.wallbox,
        row.vehicle or None,
        ensure_utc(row.start),
        None if row.end is None else ensure_utc(row.end),
        ensure_utc(row.last_seen),
    )
