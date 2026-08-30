"""companies.kind — business | personal.

Personal-finance mode: a company can be a single-user 'personal' tenant
(simplified chart of accounts, the 'personal' role, slim UI). Existing
rows default to 'business'.

Revision ID: 030
Revises: 029
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "companies", "kind"):
        op.add_column(
            "companies",
            sa.Column("kind", sa.String(16), nullable=False, server_default="business"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "companies", "kind"):
        op.drop_column("companies", "kind")
