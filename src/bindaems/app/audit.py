"""Änderungsprotokoll: wer hat wann was geändert (Spec 14)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, Engine, insert, select

from bindaems.app.db.schema import audit_log
from bindaems.shared.timeutil import Clock

Source = Literal["ui", "ha", "cli", "system"]


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: datetime
    actor: str
    source: str
    action: str
    target: str | None
    details: Mapping[str, Any] | None


class AuditLog:
    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def record(
        self,
        actor: str,
        source: Source,
        action: str,
        target: str | None = None,
        details: Mapping[str, Any] | None = None,
        *,
        conn: Connection | None = None,
    ) -> None:
        """Schreibt einen Eintrag; mit ``conn`` in der Transaktion des Aufrufers."""
        statement = insert(audit_log).values(
            ts=self._clock.now(),
            actor=actor,
            source=source,
            action=action,
            target=target,
            details=dict(details) if details is not None else None,
        )
        if conn is not None:
            conn.execute(statement)
            return
        with self._engine.begin() as own:
            own.execute(statement)

    def recent(self, limit: int = 100) -> list[AuditEntry]:
        """Die jüngsten Einträge, neueste zuerst."""
        query = select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [
            AuditEntry(
                id=row.id,
                ts=row.ts,
                actor=row.actor,
                source=row.source,
                action=row.action,
                target=row.target,
                details=row.details,
            )
            for row in rows
        ]
