"""users.last_seen_release — the release whose what's-new tour the user has
already been shown. NULL = existing user, show the current release once.

Revision ID: 034
Revises: 033
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "users", "last_seen_release"):
        op.add_column("users", sa.Column("last_seen_release", sa.String(32), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "users", "last_seen_release"):
        op.drop_column("users", "last_seen_release")
