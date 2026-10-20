"""Fahrverhalten (Plan 5b, Präzisierung 1): ``driving_profile`` hält die berechneten Profile je
Auto, ``driving_automation`` den Stand der Automatik für Ziel und Abfahrt. Vorhandene Zeilen
bleiben unverändert. ``downgrade`` entfernt außerdem den Abschnitt ``learning.driving`` aus den
gespeicherten Einstellungen, damit eine app aus Plan 5a sie lesen kann.

Revision ID: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "driving_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vehicle", sa.String(32), nullable=False),
        sa.Column("computed_at", sa.String(32), nullable=False),
        sa.Column("data_from", sa.String(32), nullable=False),
        sa.Column("data_to", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_driving_profile_vehicle_computed_at", "driving_profile", ["vehicle", "computed_at"]
    )
    op.create_table(
        "driving_automation",
        sa.Column("vehicle", sa.String(32), primary_key=True),
        sa.Column("paused_until", sa.String(32), nullable=True),
        sa.Column("last_set_at", sa.String(32), nullable=True),
        sa.Column("last_values", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # Eine app aus Plan 5a lehnt ``learning.driving`` ab und nähme sonst Standardwerte. Alle
    # anderen Werte bleiben.
    op.execute(
        "UPDATE settings_version SET data = json_remove(data, '$.learning.driving') "
        "WHERE json_type(data, '$.learning.driving') IS NOT NULL"
    )
    op.drop_table("driving_automation")
    op.drop_index("ix_driving_profile_vehicle_computed_at", table_name="driving_profile")
    op.drop_table("driving_profile")
