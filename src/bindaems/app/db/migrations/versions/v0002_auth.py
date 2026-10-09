"""Benutzer, Sitzungen und Fehlversuche.

Revision ID: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False),
        sa.Column("totp_last_step", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("username", name="uq_user_username"),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.String(32), nullable=False),
        sa.Column("remember", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_session_user_id", "session", ["user_id"])
    op.create_table(
        "login_failure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("ts", sa.String(32), nullable=False),
    )
    op.create_index("ix_login_failure_username_ts", "login_failure", ["username", "ts"])


def downgrade() -> None:
    op.drop_index("ix_login_failure_username_ts", table_name="login_failure")
    op.drop_table("login_failure")
    op.drop_index("ix_session_user_id", table_name="session")
    op.drop_table("session")
    op.drop_table("user")
