"""Entscheidungslog in SQLite (Spec 7.10, Plan 2d Präzisierung 7).

Der ``DecisionStore`` hält die Entscheidungen des core in der Tabelle ``decision``. Der
``DecisionCollector`` füllt sie: jede Meldung des Streams in einen Puffer, den er jede Sekunde in
einem Zug schreibt, und nach jedem Verbindungsaufbau den Rückstand aus ``GET /v1/decisions``. Zu
jeder neu gespeicherten Entscheidung schreibt er einen Punkt für die Markierungen in Grafana.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import Engine, Row, and_, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bindaems.app.core_link import CoreClient, CoreUnavailableError
from bindaems.app.db.schema import decision_table
from bindaems.app.history.influx import RP_RAW
from bindaems.shared.control import CommandKind
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.shared.influx.lineprotocol import Point, PointSink
from bindaems.shared.timeutil import Clock

log = structlog.get_logger(__name__)

# Der Rückstand beginnt 10 min vor dem jüngsten ``ts`` der Tabelle: Die Wanduhr des core kann
# zurückspringen, dann tragen neue Entscheidungen ältere Zeiten (Übergabe, „Entscheidungen
# tragen die Wanduhr“). Doppelte fängt die eindeutige ``id``.
CATCH_UP_OVERLAP: Final = timedelta(minutes=10)
CATCH_UP_EMPTY: Final = timedelta(days=7)  # ohne Eintrag in der Tabelle
PAGE_LIMIT: Final = 1000
FLUSH_S: Final = 1.0
MEASUREMENT: Final = "decision"

_Row = Row[Any]


@dataclass(frozen=True)
class StoredDecision:
    """Eine gespeicherte Entscheidung; ``cursor`` (``seq``) ist ihre Stelle für „ältere laden“."""

    cursor: int
    decision: Decision


@dataclass(frozen=True)
class DecisionQuery:
    """Filter und Seite einer Abfrage; ``before`` ist der ``cursor`` der letzten Zeile der Seite.

    ``dry_run=False`` blendet Dry-Run-Entscheidungen aus; ``text`` sucht ohne Beachtung der
    Groß- und Kleinschreibung, auch bei Umlauten (SQL-Funktion ``ulower`` aus ``open_database``).
    """

    since: datetime | None = None
    until: datetime | None = None
    actuator: str | None = None
    result: DecisionResult | None = None
    dry_run: bool = True
    text: str | None = None
    before: int | None = None
    limit: int = 100


def _stored(row: _Row) -> StoredDecision:
    return StoredDecision(
        row.seq,
        Decision(
            id=row.id,
            ts=row.ts,
            actuator=row.actuator,
            kind=CommandKind(row.kind),
            requested=row.requested,
            applied=row.applied,
            dry_run=row.dry_run,
            outcome=row.outcome,
            result=DecisionResult(row.result),
            codes=tuple(row.codes),
            text=row.text,
            inputs=row.inputs,
        ),
    )


def _like_pattern(text: str) -> str:
    """Das Suchmuster für ``LIKE ... ESCAPE '\\'``: Platzhalter im Text gelten wörtlich."""
    escaped = text.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class DecisionStore:
    """Die Tabelle ``decision``; alle Methoden blockieren (Aufruf über ``asyncio.to_thread``)."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def add(self, decisions: Sequence[Decision]) -> list[Decision]:
        """Speichert die Entscheidungen; liefert die neuen (eine bekannte ``id`` entfällt)."""
        if not decisions:
            return []
        stored: list[Decision] = []
        with self._engine.begin() as conn:
            for decision in decisions:
                statement = (
                    sqlite_insert(decision_table)
                    .values(
                        id=decision.id,
                        ts=decision.ts,
                        actuator=decision.actuator,
                        kind=str(decision.kind),
                        requested=decision.requested,
                        applied=decision.applied,
                        dry_run=decision.dry_run,
                        outcome=decision.outcome,
                        result=str(decision.result),
                        codes=list(decision.codes),
                        text=decision.text,
                        inputs=dict(decision.inputs),
                    )
                    .on_conflict_do_nothing(index_elements=[decision_table.c.id])
                )
                if conn.execute(statement).rowcount:
                    stored.append(decision)
        return stored

    def newest_ts(self) -> datetime | None:
        """Der jüngste ``ts`` in der Tabelle; ``None``, wenn sie leer ist."""
        with self._engine.connect() as conn:
            newest: datetime | None = conn.execute(
                select(func.max(decision_table.c.ts))
            ).scalar_one()
        return newest

    def query(self, q: DecisionQuery) -> tuple[list[StoredDecision], bool]:
        """Die Seite zu ``q``, neueste zuerst (``ts``, dann ``seq``); ``True``: es gibt ältere."""
        table = decision_table
        conditions = []
        if q.since is not None:
            conditions.append(table.c.ts >= q.since)
        if q.until is not None:
            conditions.append(table.c.ts < q.until)
        if q.actuator is not None:
            conditions.append(table.c.actuator == q.actuator)
        if q.result is not None:
            conditions.append(table.c.result == str(q.result))
        if not q.dry_run:
            conditions.append(table.c.dry_run.is_(False))
        if q.text:
            conditions.append(func.ulower(table.c.text).like(_like_pattern(q.text), escape="\\"))
        if q.before is not None:
            # „älter“ heißt nach (ts, seq), nicht nach seq: der Rückstand liefert ältere Zeiten
            # mit höherem seq nach
            anchor = select(table.c.ts).where(table.c.seq == q.before).scalar_subquery()
            conditions.append(
                or_(
                    table.c.ts < anchor,
                    and_(table.c.ts == anchor, table.c.seq < q.before),
                )
            )
        statement = (
            select(table)
            .where(*conditions)
            .order_by(table.c.ts.desc(), table.c.seq.desc())
            .limit(q.limit + 1)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(statement).all()
        return [_stored(row) for row in rows[: q.limit]], len(rows) > q.limit

    def latest_per_actuator(self, since: datetime) -> list[StoredDecision]:
        """Je Aktor die jüngste Entscheidung seit ``since`` (neueste zuerst)."""
        table = decision_table
        rank = (
            func.row_number()
            .over(
                partition_by=table.c.actuator,
                order_by=(table.c.ts.desc(), table.c.seq.desc()),
            )
            .label("rank")
        )
        ranked = select(table, rank).where(table.c.ts >= since).subquery()
        statement = (
            select(ranked)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.ts.desc(), ranked.c.seq.desc())
        )
        with self._engine.connect() as conn:
            return [_stored(row) for row in conn.execute(statement)]

    def cleanup(self, keep_days: int) -> int:
        """Löscht Entscheidungen, die älter als ``keep_days`` Tage sind; liefert ihre Zahl."""
        cutoff = self._clock.now() - timedelta(days=keep_days)
        with self._engine.begin() as conn:
            result = conn.execute(delete(decision_table).where(decision_table.c.ts < cutoff))
        return int(result.rowcount)


def _point(decision: Decision) -> Point:
    """Markierung für Grafana. Die Entscheidungen eines Zyklus teilen ``ts``; der Tag ``kind``
    (vier Werte) hält etwa Strom und „Laden an“ einer Startfolge in getrennten Serien, sonst
    behielte InfluxDB nur den letzten Punkt."""
    return Point(
        MEASUREMENT,
        {
            "actuator": decision.actuator,
            "kind": str(decision.kind),
            "result": str(decision.result),
            "dry_run": "true" if decision.dry_run else "false",
        },
        {"text": decision.text, "codes": ",".join(decision.codes)},
        decision.ts,
        rp=RP_RAW,
    )


class DecisionCollector:
    """Füllt den ``DecisionStore`` aus Stream und Rückstand und schreibt die Markierungen."""

    def __init__(
        self, store: DecisionStore, client: CoreClient, sink: PointSink, clock: Clock
    ) -> None:
        self._store = store
        self._client = client
        self._sink = sink
        self._clock = clock
        self._buffer: list[Decision] = []

    async def on_decision(self, decision: Decision) -> None:
        """Handler des Streams: puffert die Entscheidung bis zum nächsten Schreiben."""
        self._buffer.append(decision)

    async def flush(self) -> int:
        """Schreibt den Puffer in einem Zug; liefert die Zahl der neu gespeicherten.

        Schlägt das Schreiben fehl oder wird es abgebrochen, bleibt der Stapel für den nächsten
        Versuch im Puffer.
        """
        pending, self._buffer = self._buffer, []
        if not pending:
            return 0
        try:
            return await self._store_and_mark(pending)
        except BaseException:  # auch ein Abbruch (Herunterfahren) gibt den Stapel zurück
            self._buffer = pending + self._buffer
            raise

    async def run(self) -> None:
        """Schreibt den Puffer jede Sekunde; ein Fehler kommt ins Log, die Schleife läuft weiter."""
        while True:
            await asyncio.sleep(FLUSH_S)
            try:
                await self.flush()
            except Exception:
                log.exception("Entscheidungen nicht gespeichert")

    async def catch_up(self) -> None:
        """Connect-Handler: holt den Rückstand seitenweise vom core (ohne Lücke, ohne Doppelte).

        Ist der core nicht erreichbar, endet der Rückstand mit einer Logzeile; der nächste
        Verbindungsaufbau holt ihn nach.
        """
        newest = await asyncio.to_thread(self._store.newest_ts)
        since = (
            newest - CATCH_UP_OVERLAP if newest is not None else self._clock.now() - CATCH_UP_EMPTY
        )
        total = 0
        try:
            while True:
                decisions, more = await self._client.decisions(since, PAGE_LIMIT)
                total += await self._store_and_mark(decisions)
                if not more:
                    break
                if not decisions or decisions[-1].ts <= since:  # kein Fortschritt: nicht endlos
                    log.warning("Rückstand der Entscheidungen kommt nicht voran", since=str(since))
                    break
                since = decisions[-1].ts
        except CoreUnavailableError as exc:
            log.warning("Rückstand der Entscheidungen nicht lesbar", error=str(exc))
            return
        if total:
            log.info("Rückstand der Entscheidungen gespeichert", decisions=total)

    async def cleanup(self, keep_days: int) -> int:
        """Löscht Entscheidungen, die älter als ``keep_days`` Tage sind."""
        return await asyncio.to_thread(self._store.cleanup, keep_days)

    async def _store_and_mark(self, decisions: Sequence[Decision]) -> int:
        stored = await asyncio.to_thread(self._store.add, decisions)
        for decision in stored:
            self._sink.write(_point(decision))
        return len(stored)
