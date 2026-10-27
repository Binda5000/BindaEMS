"""Vorheizen und Vorkühlen (Plan 5c, Präzisierung 3): ``thermal_model`` hält je Gerät und
Betriebsart das gelernte Hausmodell samt Profil. Vorhandene Zeilen bleiben unverändert.
``downgrade`` entfernt außerdem den Abschnitt ``learning.thermal`` aus den gespeicherten
Einstellungen, damit eine app aus Plan 5b sie lesen kann.

Revision ID: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "thermal_model",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("computed_at", sa.String(32), nullable=False),
        sa.Column("device", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("usable", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_thermal_model_device_mode_computed_at",
        "thermal_model",
        ["device", "mode", "computed_at"],
    )


def downgrade() -> None:
    # Eine app aus Plan 5b lehnt ``learning.thermal`` ab und nähme sonst Standardwerte. Alle
    # anderen Werte bleiben.
    op.execute(
        "UPDATE settings_version SET data = json_remove(data, '$.learning.thermal') "
        "WHERE json_type(data, '$.learning.thermal') IS NOT NULL"
    )
    op.drop_index("ix_thermal_model_device_mode_computed_at", table_name="thermal_model")
    op.drop_table("thermal_model")
