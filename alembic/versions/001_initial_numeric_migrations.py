"""Promote INT columns to BIGINT and cleanup entity names.

Revision ID: 001
Revises: None
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_type(conn, table: str, column: str) -> str | None:
    row = conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def _col_is_nullable(conn, table: str, column: str) -> bool | None:
    row = conn.execute(
        sa.text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] == "YES" if row else None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Promote INT → BIGINT for large IRR values
    targets = [
        ("invoices", "amount"),
        ("recurring_rules", "amount"),
        ("budget_limits", "limit_amount"),
        ("transaction_lines", "debit"),
        ("transaction_lines", "credit"),
    ]
    for table, col in targets:
        current = _col_type(conn, table, col)
        if current and current != "bigint":
            op.execute(sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {col} TYPE BIGINT USING {col}::BIGINT"
            ))

    # Entity name cleanup
    stmts = [
        "UPDATE entities SET name = btrim(regexp_replace(name, '\\s+', ' ', 'g')) WHERE name ~ '\\s{2,}'",
        (
            "UPDATE entities "
            "SET name = initcap(btrim(regexp_replace(name, E'\\s+with\\s+of\\s+\\d+\\s*$', '', 'i'))) "
            "WHERE type = 'bank' AND name ~* E'\\s+with\\s+of\\s+\\d+\\s*$'"
        ),
    ]
    for s in stmts:
        conn.execute(sa.text(s))

    # Drop NOT NULL on transaction_fee_applications.transaction_id if needed
    nullable = _col_is_nullable(conn, "transaction_fee_applications", "transaction_id")
    if nullable is False:
        op.execute(sa.text(
            "ALTER TABLE transaction_fee_applications ALTER COLUMN transaction_id DROP NOT NULL"
        ))

    # Add preferred_language to users if missing
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(8) DEFAULT 'en'"
    ))
    op.execute(sa.text(
        "UPDATE users SET preferred_language = 'en' WHERE preferred_language IS NULL OR btrim(preferred_language) = ''"
    ))

    # Add deleted_at to transactions if missing
    if _col_type(conn, "transactions", "deleted_at") is None:
        op.add_column("transactions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_transactions_deleted_at", "transactions", ["deleted_at"])


def downgrade() -> None:
    # These are data cleanup and type promotions — not reversible in a meaningful way.
    pass
