"""Prognose-Archiv und Prognosegüte.

Revision ID: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_record",
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("slot_start", sa.String(32), primary_key=True),
        sa.Column("horizon", sa.String(8), primary_key=True),
        sa.Column("issued_at", sa.String(32), nullable=False),
        sa.Column("p50_w", sa.Float(), nullable=False),
        sa.Column("raw_w", sa.Float(), nullable=False),
        sa.Column("clearness", sa.String(8), nullable=True),
    )
    op.create_table(
        "forecast_metric",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("horizon", sa.String(8), primary_key=True),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("abs_err_sum_w", sa.Float(), nullable=False),
        sa.Column("err_sum_w", sa.Float(), nullable=False),
        sa.Column("actual_sum_w", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("forecast_metric")
    op.drop_table("forecast_record")
