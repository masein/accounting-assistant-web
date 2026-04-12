"""Add currency column to transactions table.

Revision ID: 003
Revises: 002
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'transactions' AND column_name = 'currency'"
        )
    ).first()
    if not result:
        op.add_column(
            "transactions",
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
        )
        op.create_index("ix_transactions_currency", "transactions", ["currency"])


def downgrade() -> None:
    op.drop_index("ix_transactions_currency", table_name="transactions")
    op.drop_column("transactions", "currency")
