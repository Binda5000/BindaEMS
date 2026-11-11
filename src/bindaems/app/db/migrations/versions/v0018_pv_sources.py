"""PV-Anlagen und Strings: ``pv_source`` hält die Einträge (Anlage oder String), ``pv_source_day``
den Ertrag je Quelle und Tag (``source_id`` 0 ist „PV gesamt“). Vorhandene Tabellen bleiben
unverändert.

Revision ID: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "pv_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("pv_source.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("source_kind", sa.String(8), nullable=False),
        sa.Column("power_ref", sa.String(200), nullable=False),
        sa.Column("power_unit", sa.String(4), nullable=True),
        sa.Column("kwp", sa.Float(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_pv_source_parent_id", "pv_source", ["parent_id"])
    op.create_table(
        "pv_source_day",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("source_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("kwh", sa.Float(), nullable=True),
        sa.Column("note", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("pv_source_day")
    op.drop_index("ix_pv_source_parent_id", table_name="pv_source")
    op.drop_table("pv_source")
