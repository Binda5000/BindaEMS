"""Pläne und ihre Eingangsdaten.

Revision ID: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("solver_status", sa.String(12), nullable=True),
        sa.Column("fallback_reason", sa.String(200), nullable=True),
        sa.Column("valid_until", sa.String(32), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("expected_cost_ct", sa.Float(), nullable=False),
        sa.Column("trimmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_plan_created_at", "plan", ["created_at"])
    op.create_table(
        "plan_inputs",
        sa.Column("plan_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plan_inputs")
    op.drop_index("ix_plan_created_at", table_name="plan")
    op.drop_table("plan")
