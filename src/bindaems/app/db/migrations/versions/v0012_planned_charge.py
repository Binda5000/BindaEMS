"""Geplante Ladungen für die Abnahme „Ziel-SOC“ (Plan 4b, Präzisierung 16): Tabelle
``planned_charge`` mit Index auf ``departure_at``. Ändert keine vorhandene Tabelle.

Revision ID: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "planned_charge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("charging_session.id"), nullable=False),
        sa.Column("vehicle", sa.String(32), nullable=False),
        sa.Column("target_soc_pct", sa.Float(), nullable=False),
        sa.Column("departure_at", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("start_soc_pct", sa.Float(), nullable=False),
        sa.Column("start_soc_source", sa.String(10), nullable=False),
        sa.Column("feasible", sa.Boolean(), nullable=False),
        sa.Column("reached_at", sa.String(32), nullable=True),
        sa.Column("end_soc_pct", sa.Float(), nullable=True),
        sa.Column("end_soc_source", sa.String(10), nullable=True),
        sa.Column("end_soc_at", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(10), nullable=False),
    )
    op.create_index("ix_planned_charge_departure_at", "planned_charge", ["departure_at"])


def downgrade() -> None:
    op.drop_index("ix_planned_charge_departure_at", table_name="planned_charge")
    op.drop_table("planned_charge")
