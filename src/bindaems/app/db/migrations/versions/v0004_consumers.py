"""Verbraucher mit Hierarchie.

Revision ID: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "consumer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("group_name", sa.String(60), nullable=True),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("consumer.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("source_kind", sa.String(8), nullable=False),
        sa.Column("power_ref", sa.String(200), nullable=False),
        sa.Column("power_unit", sa.String(4), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_consumer_parent_id", "consumer", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_consumer_parent_id", table_name="consumer")
    op.drop_table("consumer")
