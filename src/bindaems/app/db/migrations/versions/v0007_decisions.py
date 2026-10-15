"""Entscheidungslog des core.

Revision ID: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decision",
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("actuator", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("requested", sa.JSON(), nullable=True),
        sa.Column("applied", sa.JSON(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("codes", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.UniqueConstraint("id", name="uq_decision_id"),
    )
    op.create_index("ix_decision_ts", "decision", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_decision_ts", table_name="decision")
    op.drop_table("decision")
