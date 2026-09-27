"""Inventory costing (roadmap 2026-09 §4.4): barcode and reorder point on
items, and bill-of-materials lines. Idempotent on fresh and migrated
databases alike.

Revision ID: 049
Revises: 048
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("inventory_items")}
    if "barcode" not in cols:
        op.add_column("inventory_items", sa.Column("barcode", sa.String(64), nullable=True))
        op.create_index("ix_inventory_items_barcode", "inventory_items", ["barcode"])
    for name in ("reorder_level", "reorder_qty"):
        if name not in cols:
            op.add_column("inventory_items", sa.Column(name, sa.Numeric(18, 4), nullable=True))
    if not insp.has_table("inventory_bom_lines"):
        op.create_table(
            "inventory_bom_lines",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("product_id", UUID(as_uuid=True),
                      sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("component_id", UUID(as_uuid=True),
                      sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("product_id", "component_id", name="uq_inventory_bom_component"),
        )
        for col in ("company_id", "product_id", "component_id"):
            op.create_index(f"ix_inventory_bom_lines_{col}", "inventory_bom_lines", [col])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("inventory_bom_lines"):
        op.drop_table("inventory_bom_lines")
    cols = {c["name"] for c in insp.get_columns("inventory_items")}
    if "barcode" in cols:
        op.drop_index("ix_inventory_items_barcode", table_name="inventory_items")
    for name in ("reorder_qty", "reorder_level", "barcode"):
        if name in cols:
            op.drop_column("inventory_items", name)
