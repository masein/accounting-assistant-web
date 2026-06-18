"""Effective-dated tax rates + per-line tax code/treatment.

Revision ID: 013
Revises: 012
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
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
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "tax_rates"):
        op.create_table(
            "tax_rates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("code", sa.String(64), nullable=False),
            sa.Column("jurisdiction", sa.String(32), nullable=False),
            sa.Column("description", sa.String(128), nullable=True),
            sa.Column("rate", sa.Numeric(7, 4), nullable=False, server_default="0"),
            sa.Column("effective_from", sa.Date, nullable=False),
            sa.Column("effective_to", sa.Date, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_tax_rates_code", "tax_rates", ["code"])
        op.create_index("ix_tax_rates_jurisdiction", "tax_rates", ["jurisdiction"])
        op.create_index("ix_tax_rates_effective_from", "tax_rates", ["effective_from"])

    # Per-line tax code + treatment for effective-dating and cross-border.
    if _table_exists(conn, "invoice_items"):
        if not _column_exists(conn, "invoice_items", "tax_code"):
            op.add_column("invoice_items", sa.Column("tax_code", sa.String(64), nullable=True))
        if not _column_exists(conn, "invoice_items", "tax_treatment"):
            op.add_column(
                "invoice_items",
                sa.Column("tax_treatment", sa.String(24), nullable=False, server_default="standard"),
            )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "invoice_items", "tax_treatment"):
        op.drop_column("invoice_items", "tax_treatment")
    if _column_exists(conn, "invoice_items", "tax_code"):
        op.drop_column("invoice_items", "tax_code")
    if _table_exists(conn, "tax_rates"):
        op.drop_table("tax_rates")
