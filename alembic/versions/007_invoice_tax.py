"""Per-line VAT / sales tax on invoice items.

Revision ID: 007
Revises: 006
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    if not _column_exists(conn, "invoice_items", "tax_rate"):
        op.add_column(
            "invoice_items",
            sa.Column("tax_rate", sa.Numeric(7, 4), nullable=False, server_default="0"),
        )
    if not _column_exists(conn, "invoice_items", "taxable"):
        op.add_column(
            "invoice_items",
            sa.Column("taxable", sa.Boolean, nullable=False, server_default="true"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "invoice_items", "taxable"):
        op.drop_column("invoice_items", "taxable")
    if _column_exists(conn, "invoice_items", "tax_rate"):
        op.drop_column("invoice_items", "tax_rate")
