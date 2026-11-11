"""Heizungstage (Plan Ölheizung): ``heating_day`` hält je lokalem Tag Brennerstunden, bekannte
Stunden, Tageslänge, Endzustand des Brenners, Heizgradtage, Tagesmittel und den Strom der
Klimaanlage beim Heizen. Vorhandene Tabellen bleiben unverändert.

Revision ID: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "heating_day",
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("burner_h", sa.Float(), nullable=True),
        sa.Column("known_h", sa.Float(), nullable=False),
        sa.Column("day_h", sa.Float(), nullable=False),
        sa.Column("burner_end", sa.Boolean(), nullable=True),
        sa.Column("hdd_kd", sa.Float(), nullable=True),
        sa.Column("outdoor_mean_c", sa.Float(), nullable=True),
        sa.Column("ac_heat_el_kwh", sa.Float(), nullable=True),
        sa.Column("note", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("heating_day")
