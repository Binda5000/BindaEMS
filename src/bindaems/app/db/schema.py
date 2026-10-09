"""Tabellen der app. Jede Änderung braucht eine Migration unter ``migrations/versions``."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
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

user_table = Table(
    "user",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(32), nullable=False),
    Column("password_hash", String(200), nullable=False),
    Column("role", String(16), nullable=False),
    Column("totp_secret", String(64), nullable=True),
    Column("totp_enabled", Boolean, nullable=False),
    Column("totp_last_step", Integer, nullable=True),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    UniqueConstraint("username", name="uq_user_username"),
)

# id = SHA-256 des Sitzungstokens; das Token selbst steht nur im Cookie
session_table = Table(
    "session",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("last_seen_at", UtcDateTime(), nullable=False),
    Column("expires_at", UtcDateTime(), nullable=False),
    Column("remember", Boolean, nullable=False),
    Index("ix_session_user_id", "user_id"),
)

login_failure_table = Table(
    "login_failure",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False),
    Column("ts", UtcDateTime(), nullable=False),
    Index("ix_login_failure_username_ts", "username", "ts"),
)

# Jede gespeicherte Fassung der Laufzeit-Einstellungen; die höchste Version gilt
settings_version = Table(
    "settings_version",
    metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("source", String(16), nullable=False),
    Column("comment", String(200), nullable=True),
    Column("data", JSON, nullable=False),
)

# Verbraucher mit Hierarchie; Leistung aus einem core-Signal oder einer HA-Entität
consumer_table = Table(
    "consumer",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
    Column("group_name", String(60), nullable=True),
    Column("parent_id", Integer, ForeignKey("consumer.id", ondelete="RESTRICT"), nullable=True),
    Column("color", String(7), nullable=False),
    Column("source_kind", String(8), nullable=False),
    Column("power_ref", String(200), nullable=False),
    Column("power_unit", String(4), nullable=True),
    Column("sort", Integer, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    Index("ix_consumer_parent_id", "parent_id"),
)
