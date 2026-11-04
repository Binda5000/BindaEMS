"""Betrieb im Blick: ``ops_finding`` hält die Auffälligkeiten je lokalem Tag, ``ops_day`` die
gerechneten Tage samt Versand des Tagesberichts. Vorhandene Zeilen bleiben unverändert.

Revision ID: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ops_finding",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("rule", sa.String(32), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("start", sa.String(32), nullable=True),
        sa.Column("end", sa.String(32), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("link", sa.String(200), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_ops_finding_day", "ops_finding", ["day"])
    op.create_table(
        "ops_day",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("computed_at", sa.String(32), nullable=False),
        sa.Column("report_sent_at", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ops_day")
    op.drop_index("ix_ops_finding_day", table_name="ops_finding")
    op.drop_table("ops_finding")
