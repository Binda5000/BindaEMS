"""Lernen der Hauslast (Plan 5a, Präzisierung 1): Wetterarchiv ``weather_slot`` und Registry der
Lastmodelle ``model_registry``. Die Archivarten wachsen um ``load_profile`` und ``load_model``;
deshalb wird ``kind`` in ``forecast_record`` und ``forecast_metric`` auf 16 Zeichen verlängert.
Vorhandene Zeilen bleiben unverändert.

Revision ID: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "weather_slot",
        sa.Column("slot_start", sa.String(32), primary_key=True),
        sa.Column("temp_c", sa.Float(), nullable=False),
        sa.Column("issued_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="load"),
        sa.Column("trained_at", sa.String(32), nullable=False),
        sa.Column("data_from", sa.String(32), nullable=False),
        sa.Column("data_to", sa.String(32), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("max_w", sa.Float(), nullable=False),
        sa.Column("file", sa.String(200), nullable=False),
        sa.Column("backtest", sa.JSON(), nullable=True),
        sa.Column("importance", sa.JSON(), nullable=False),
        sa.Column("usable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("decided_at", sa.String(32), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=True),
    )
    op.create_index("ix_model_registry_trained_at", "model_registry", ["trained_at"])
    with op.batch_alter_table("forecast_record") as batch:
        batch.alter_column("kind", existing_type=sa.String(8), type_=sa.String(16))
    with op.batch_alter_table("forecast_metric") as batch:
        batch.alter_column("kind", existing_type=sa.String(8), type_=sa.String(16))


def downgrade() -> None:
    # Zeilen der neuen Arten passen nicht mehr in die alte Spalte und sind ohnehin abgeleitet
    op.execute("DELETE FROM forecast_record WHERE kind NOT IN ('pv', 'load')")
    op.execute("DELETE FROM forecast_metric WHERE kind NOT IN ('pv', 'load')")
    with op.batch_alter_table("forecast_metric") as batch:
        batch.alter_column("kind", existing_type=sa.String(16), type_=sa.String(8))
    with op.batch_alter_table("forecast_record") as batch:
        batch.alter_column("kind", existing_type=sa.String(16), type_=sa.String(8))
    op.drop_index("ix_model_registry_trained_at", table_name="model_registry")
    op.drop_table("model_registry")
    op.drop_table("weather_slot")
