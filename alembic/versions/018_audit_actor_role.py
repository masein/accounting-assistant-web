"""Audit attribution: record the acting user's RBAC role.

Adds a nullable ``audit_logs.actor_role`` so the Audit view can show who did
what AND in what role. Existing rows stay NULL (unknown historical role).

Revision ID: 018
Revises: 017
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "audit_logs", "actor_role"):
        op.add_column("audit_logs", sa.Column("actor_role", sa.String(16), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "audit_logs", "actor_role"):
        op.drop_column("audit_logs", "actor_role")
