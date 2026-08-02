"""transaction_entities.amount — the entity's own share of a journal.

The migrated opening balances post as ONE aggregate journal linked to every
imported party, so the per-entity "Transactions with X" view showed the whole
34-billion journal total for every entity. The link now carries the entity's
own signed share (minor units, + = debit) when it is known; NULL keeps the
normal-voucher semantics (the whole transaction concerns the entity).

Revision ID: 028
Revises: 027
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "transaction_entities", "amount"):
        op.add_column("transaction_entities", sa.Column("amount", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "transaction_entities", "amount"):
        op.drop_column("transaction_entities", "amount")
