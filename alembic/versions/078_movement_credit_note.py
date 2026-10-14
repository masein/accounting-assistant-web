"""Goods back on a credit note (docs/PLAN_INVENTORY_GL.md, phase G3; scenarios T15–T19).

``inventory_movements.credit_note_id``: the credit note a movement came back
on, when a customer returns goods or we return them to a supplier. It tells a
return from the sale or bill it belongs to (both carry ``invoice_id``), so what
is left to return is the invoice's quantity less what came back. Nullable;
idempotent on fresh and migrated databases alike.

Revision ID: 078
Revises: 077
Create Date: 2026-10-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# money columns this migration adds (tests/test_money.py reads it): none
MONEY: dict[str, list[str]] = {}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("inventory_movements")}
    if "credit_note_id" not in columns:
        op.add_column("inventory_movements", sa.Column(
            "credit_note_id", UUID(as_uuid=True), sa.ForeignKey("credit_notes.id", ondelete="SET NULL"), nullable=True))
    indexes = {i["name"] for i in insp.get_indexes("inventory_movements")}
    if "ix_inventory_movements_credit_note_id" not in indexes:
        op.create_index("ix_inventory_movements_credit_note_id", "inventory_movements", ["credit_note_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    indexes = {i["name"] for i in insp.get_indexes("inventory_movements")}
    if "ix_inventory_movements_credit_note_id" in indexes:
        op.drop_index("ix_inventory_movements_credit_note_id", table_name="inventory_movements")
    columns = {c["name"] for c in insp.get_columns("inventory_movements")}
    if "credit_note_id" in columns:
        op.drop_column("inventory_movements", "credit_note_id")
