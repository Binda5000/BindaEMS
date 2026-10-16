"""Tabellen der app. Jede Änderung braucht eine Migration unter ``migrations/versions``."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
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

# Rohantworten der Preisquellen; gleiche Antworten nacheinander werden nur einmal gespeichert
price_raw = Table(
    "price_raw",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(32), nullable=False),
    Column("url", String(500), nullable=False),
    Column("fetched_at", UtcDateTime(), nullable=False),
    Column("last_fetched_at", UtcDateTime(), nullable=False),
    Column("status", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("body", Text, nullable=False),
    Index("ix_price_raw_source", "source"),
)

# Gültiger Börsenpreis je Slot (netto) mit Herkunft: primary = smartENERGY, fallback = Referenz
price_slot = Table(
    "price_slot",
    metadata,
    Column("slot_start", UtcDateTime(), primary_key=True),
    Column("spot_raw_ct", Float, nullable=True),
    Column("spot_net_ct", Float, nullable=False),
    Column("reference_ct", Float, nullable=True),
    Column("origin", String(8), nullable=False),
    Column("raw_id", Integer, ForeignKey("price_raw.id"), nullable=True),
    Column("updated_at", UtcDateTime(), nullable=False),
)

# Prognose-Archiv: je Slot und Horizontklasse die Ausgabe, deren Vorlauf der Klassenmitte am
# nächsten liegt (Plan 3a, Präzisierung 7)
forecast_record = Table(
    "forecast_record",
    metadata,
    Column("kind", String(8), primary_key=True),  # pv, load
    Column("slot_start", UtcDateTime(), primary_key=True),
    Column("horizon", String(8), primary_key=True),  # h0_6, h6_24, h24
    Column("issued_at", UtcDateTime(), nullable=False),
    Column("p50_w", Float, nullable=False),
    Column("raw_w", Float, nullable=False),  # PV: Modell ohne Kalibrierung; Last: wie p50_w
    Column("clearness", String(8), nullable=True),  # clear, mixed, overcast
)

# Prognosegüte je lokalem Tag, Art und Horizont (Plan 3a, Präzisierung 10)
forecast_metric = Table(
    "forecast_metric",
    metadata,
    Column("day", String(10), primary_key=True),  # lokales Datum, ISO
    Column("kind", String(8), primary_key=True),
    Column("horizon", String(8), primary_key=True),
    Column("n", Integer, nullable=False),
    Column("abs_err_sum_w", Float, nullable=False),
    Column("err_sum_w", Float, nullable=False),
    Column("actual_sum_w", Float, nullable=False),
)

# Abrechnung je Viertelstunde aus den slot_flows des core (Spec 11.4)
ledger_slot = Table(
    "ledger_slot",
    metadata,
    Column("slot_start", UtcDateTime(), primary_key=True),
    Column("covered_s", Float, nullable=False),
    Column("flows_wh", JSON, nullable=False),
    Column("counters", JSON, nullable=True),
    Column("import_wh", Float, nullable=False),
    Column("export_wh", Float, nullable=False),
    Column("import_price_ct", Float, nullable=True),  # Bezugspreis brutto zum Slotzeitpunkt
    Column("origin", String(8), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
)

# Entscheidungslog des core (Spec 7.10): ``id`` ist die des core, ``seq`` die Reihenfolge des
# Eintreffens (der Rückstand kann ältere Zeiten nachliefern)
decision_table = Table(
    "decision",
    metadata,
    Column("seq", Integer, primary_key=True),
    Column("id", String(64), nullable=False),
    Column("ts", UtcDateTime(), nullable=False),
    Column("actuator", String(64), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("requested", JSON, nullable=True),
    Column("applied", JSON, nullable=True),
    Column("dry_run", Boolean, nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("result", String(16), nullable=False),
    Column("codes", JSON, nullable=False),
    Column("text", Text, nullable=False),
    Column("inputs", JSON, nullable=False),
    UniqueConstraint("id", name="uq_decision_id"),
    Index("ix_decision_ts", "ts"),
)
