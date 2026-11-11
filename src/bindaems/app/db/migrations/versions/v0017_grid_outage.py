"""Netzausfälle: ``grid_outage`` hält jeden Ausfall (Victron im Inselbetrieb) mit Beginn, Ende,
SOC-Verlauf und der Energie an AC-Out. Vorhandene Zeilen bleiben unverändert.

Revision ID: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "grid_outage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("start", sa.String(32), nullable=False),
        sa.Column("end", sa.String(32), nullable=True),
        sa.Column("end_estimated", sa.Boolean(), nullable=False),
        sa.Column("soc_start", sa.Float(), nullable=True),
        sa.Column("soc_end", sa.Float(), nullable=True),
        sa.Column("soc_min", sa.Float(), nullable=True),
        sa.Column("load_wh", sa.Float(), nullable=False),
        sa.Column("covered_s", sa.Float(), nullable=False),
    )
    op.create_index("ix_grid_outage_start", "grid_outage", ["start"])


def downgrade() -> None:
    op.drop_index("ix_grid_outage_start", table_name="grid_outage")
    op.drop_table("grid_outage")
