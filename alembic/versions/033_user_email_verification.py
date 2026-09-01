"""users.email + verification fields.

All nullable: super-admin-provisioned users have no address and must keep
working. A non-null verification_token means "awaiting confirmation".

Revision ID: 033
Revises: 032
Create Date: 2026-08-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    "email": sa.Column("email", sa.String(254), nullable=True),
    "email_verified_at": sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    "verification_token": sa.Column("verification_token", sa.String(64), nullable=True),
    "verification_sent_at": sa.Column("verification_sent_at", sa.DateTime(timezone=True), nullable=True),
}


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    for name, column in _COLUMNS.items():
        if not _column_exists(conn, "users", name):
            op.add_column("users", column)
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_verification_token", "users", ["verification_token"])


def downgrade() -> None:
    conn = op.get_bind()
    for name in _COLUMNS:
        if _column_exists(conn, "users", name):
            op.drop_column("users", name)
