"""Abrechnung je Viertelstunde.

Revision ID: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_slot",
        sa.Column("slot_start", sa.String(32), primary_key=True),
        sa.Column("covered_s", sa.Float(), nullable=False),
        sa.Column("flows_wh", sa.JSON(), nullable=False),
        sa.Column("counters", sa.JSON(), nullable=True),
        sa.Column("import_wh", sa.Float(), nullable=False),
        sa.Column("export_wh", sa.Float(), nullable=False),
        sa.Column("import_price_ct", sa.Float(), nullable=True),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ledger_slot")
