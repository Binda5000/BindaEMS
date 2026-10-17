"""Statistik: Zuordnung, Ladesitzungen, Einstandspreis, Abgleich und Verbraucher je Tag.

Revision ID: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "assignment_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("wallbox", sa.String(32), nullable=False),
        sa.Column("occupant", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
    )
    op.create_index("ix_assignment_log_wallbox_ts", "assignment_log", ["wallbox", "ts"])
    op.create_table(
        "charging_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("wallbox", sa.String(32), nullable=False),
        sa.Column("vehicle", sa.String(32), nullable=False),
        sa.Column("start", sa.String(32), nullable=False),
        sa.Column("end", sa.String(32), nullable=True),
        sa.Column("last_seen", sa.String(32), nullable=False),
    )
    op.create_index("ix_charging_session_start", "charging_session", ["start"])
    op.create_table(
        "ledger_cost",
        sa.Column("slot_start", sa.String(32), primary_key=True),
        sa.Column("stock_kwh", sa.Float(), nullable=False),
        sa.Column("avg_ct", sa.Float(), nullable=True),
        sa.Column("discharge_ct", sa.Float(), nullable=False),
    )
    op.create_table(
        "ledger_check",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("signal", sa.String(40), primary_key=True),
        sa.Column("ledger_kwh", sa.Float(), nullable=False),
        sa.Column("counter_kwh", sa.Float(), nullable=True),
        sa.Column("deviation_pct", sa.Float(), nullable=True),
        sa.Column("flagged", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(80), nullable=True),
        sa.Column("checked_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "consumer_day",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("consumer_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("kwh", sa.Float(), nullable=True),
        sa.Column("note", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("consumer_day")
    op.drop_table("ledger_check")
    op.drop_table("ledger_cost")
    op.drop_index("ix_charging_session_start", table_name="charging_session")
    op.drop_table("charging_session")
    op.drop_index("ix_assignment_log_wallbox_ts", table_name="assignment_log")
    op.drop_table("assignment_log")
