"""Tabellen der app. Jede Änderung braucht eine Migration unter ``migrations/versions``."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, Dialect, Index, Integer, MetaData, String, Table
from sqlalchemy.types import TypeDecorator

from bindaems.shared.timeutil import ensure_utc


class UtcDateTime(TypeDecorator[datetime]):
    """Zeitpunkt als ISO-Text in UTC; naive Zeitpunkte sind ein Fehler."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return ensure_utc(value).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value).astimezone(UTC)


metadata = MetaData()

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", UtcDateTime(), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("source", String(16), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", String(200), nullable=True),
    Column("details", JSON, nullable=True),
    Index("ix_audit_log_ts", "ts"),
)
