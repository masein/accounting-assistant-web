"""Add list_price column to inventory_items table.

Revision ID: 002
Revises: 001
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Check if column already exists
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'inventory_items' AND column_name = 'list_price'"
        )
    ).first()
    if not result:
        op.add_column(
            "inventory_items",
            sa.Column("list_price", sa.BigInteger(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("inventory_items", "list_price")
