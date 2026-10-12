"""Stock in the books (docs/PLAN_INVENTORY_GL.md).

``inventory_postings``: what the stock sync has posted per month and kind.
``inventory_movements.ledger_value``: what an invoice posted to stock for the
movement it made. Idempotent on fresh and migrated databases alike (a fresh
install already has both from create_all).

Revision ID: 076
Revises: 075
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# money columns this migration adds, as NUMERIC (tests/test_money.py reads it)
MONEY = {"inventory_postings": ["amount"], "inventory_movements": ["ledger_value"]}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("inventory_postings"):
        op.create_table(
            "inventory_postings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("month", sa.String(7), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("amount", sa.Numeric(), nullable=False),
            sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        for col in ("company_id", "month", "transaction_id"):
            op.create_index(f"ix_inventory_postings_{col}", "inventory_postings", [col])
    cols = {c["name"] for c in insp.get_columns("inventory_movements")}
    if "ledger_value" not in cols:
        op.add_column("inventory_movements", sa.Column("ledger_value", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_movements", "ledger_value")
    op.drop_table("inventory_postings")
