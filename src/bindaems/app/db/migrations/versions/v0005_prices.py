"""Preis-Archiv und Preise je Slot.

Revision ID: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "price_raw",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("fetched_at", sa.String(32), nullable=False),
        sa.Column("last_fetched_at", sa.String(32), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_price_raw_source", "price_raw", ["source"])
    op.create_table(
        "price_slot",
        sa.Column("slot_start", sa.String(32), primary_key=True),
        sa.Column("spot_raw_ct", sa.Float(), nullable=True),
        sa.Column("spot_net_ct", sa.Float(), nullable=False),
        sa.Column("reference_ct", sa.Float(), nullable=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("raw_id", sa.Integer(), sa.ForeignKey("price_raw.id"), nullable=True),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("price_slot")
    op.drop_index("ix_price_raw_source", table_name="price_raw")
    op.drop_table("price_raw")
