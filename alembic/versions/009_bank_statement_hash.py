"""Bank statement file-level duplicate detection: content_hash column.

Revision ID: 009
Revises: 008
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        ).first()
    )


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "bank_statements") and not _column_exists(
        conn, "bank_statements", "content_hash"
    ):
        op.add_column(
            "bank_statements",
            sa.Column("content_hash", sa.String(64), nullable=True),
        )
        op.create_index(
            "ix_bank_statements_content_hash", "bank_statements", ["content_hash"]
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "bank_statements", "content_hash"):
        op.drop_index("ix_bank_statements_content_hash", "bank_statements")
        op.drop_column("bank_statements", "content_hash")
