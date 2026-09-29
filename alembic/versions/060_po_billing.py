"""Purchase orders to bills (roadmap 2026-09 §4.8): what each PO line has
been billed, and which order and line a bill came from. Idempotent.

Revision ID: 060
Revises: 059
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "billed_qty" not in _cols("purchase_order_lines"):
        op.add_column("purchase_order_lines",
                      sa.Column("billed_qty", sa.Numeric(18, 4), nullable=False, server_default="0"))
    if "purchase_order_id" not in _cols("invoices"):
        op.add_column("invoices", sa.Column("purchase_order_id", UUID(as_uuid=True), nullable=True))
        op.create_foreign_key("fk_invoices_purchase_order_id", "invoices", "purchase_orders",
                              ["purchase_order_id"], ["id"], ondelete="SET NULL")
        op.create_index("ix_invoices_purchase_order_id", "invoices", ["purchase_order_id"])
    if "po_line_id" not in _cols("invoice_items"):
        op.add_column("invoice_items", sa.Column("po_line_id", UUID(as_uuid=True),
                                                 sa.ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
                                                 nullable=True))
        op.create_index("ix_invoice_items_po_line_id", "invoice_items", ["po_line_id"])


def downgrade() -> None:
    if "po_line_id" in _cols("invoice_items"):
        op.drop_index("ix_invoice_items_po_line_id", table_name="invoice_items")
        op.drop_column("invoice_items", "po_line_id")
    if "purchase_order_id" in _cols("invoices"):
        op.drop_index("ix_invoices_purchase_order_id", table_name="invoices")
        op.drop_constraint("fk_invoices_purchase_order_id", "invoices", type_="foreignkey")
        op.drop_column("invoices", "purchase_order_id")
    if "billed_qty" in _cols("purchase_order_lines"):
        op.drop_column("purchase_order_lines", "billed_qty")
