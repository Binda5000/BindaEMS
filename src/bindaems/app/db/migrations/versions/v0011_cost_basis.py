"""Einstandspreis: unbekannte Entladekosten (``discharge_ct`` darf leer sein) und die Eingaben je
Zeile (``slot_updated_at``, ``wear_ct``, ``usable_kwh``), damit die app veraltete Zeilen nach einem
Neustart findet.

Die Zeilen von ``ledger_cost`` sind abgeleitet; die Migration leert die Tabelle, und die app rechnet
sie beim Start mit der neuen Regel neu.

Revision ID: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DELETE FROM ledger_cost")
    with op.batch_alter_table("ledger_cost") as batch:
        batch.alter_column("discharge_ct", existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column("slot_updated_at", sa.String(32), nullable=False))
        batch.add_column(sa.Column("wear_ct", sa.Float(), nullable=False))
        batch.add_column(sa.Column("usable_kwh", sa.Float(), nullable=False))


def downgrade() -> None:
    op.execute("DELETE FROM ledger_cost")
    with op.batch_alter_table("ledger_cost") as batch:
        batch.drop_column("usable_kwh")
        batch.drop_column("wear_ct")
        batch.drop_column("slot_updated_at")
        batch.alter_column("discharge_ct", existing_type=sa.Float(), nullable=False)
