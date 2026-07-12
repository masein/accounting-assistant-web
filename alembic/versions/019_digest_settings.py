"""Per-company low-cash / daily-digest settings.

Revision ID: 019
Revises: 018
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID
    if not _table_exists(conn, "digest_settings"):
        op.create_table(
            "digest_settings",
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("cash_threshold", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("runway_months", sa.Numeric(6, 2), nullable=False, server_default="3"),
            sa.Column("channel", sa.String(16), nullable=False, server_default="all"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "digest_settings"):
        op.drop_table("digest_settings")
