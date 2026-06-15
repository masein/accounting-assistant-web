"""Purchase orders, order lines, goods receipts and receipt lines (3-way match).

Revision ID: 011
Revises: 010
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "purchase_orders"):
        op.create_table(
            "purchase_orders",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("number", sa.String(128), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=True),
            sa.Column("order_date", sa.Date, nullable=False),
            sa.Column("expected_date", sa.Date, nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("matched_invoice_id", UUID(as_uuid=True),
                      sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_purchase_orders_number", "purchase_orders", ["number"])
        op.create_index("ix_purchase_orders_entity_id", "purchase_orders", ["entity_id"])
        op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])

    if not _table_exists(conn, "purchase_order_lines"):
        op.create_table(
            "purchase_order_lines",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("order_id", UUID(as_uuid=True),
                      sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inventory_item_id", UUID(as_uuid=True),
                      sa.ForeignKey("inventory_items.id"), nullable=True),
            sa.Column("description", sa.String(256), nullable=False),
            sa.Column("ordered_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("received_qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("unit_price", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("line_total", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_purchase_order_lines_order_id", "purchase_order_lines", ["order_id"])

    if not _table_exists(conn, "goods_receipts"):
        op.create_table(
            "goods_receipts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("order_id", UUID(as_uuid=True),
                      sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("receipt_date", sa.Date, nullable=False),
            sa.Column("note", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_goods_receipts_order_id", "goods_receipts", ["order_id"])

    if not _table_exists(conn, "goods_receipt_lines"):
        op.create_table(
            "goods_receipt_lines",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("receipt_id", UUID(as_uuid=True),
                      sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("po_line_id", UUID(as_uuid=True),
                      sa.ForeignKey("purchase_order_lines.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        )
        op.create_index("ix_goods_receipt_lines_receipt_id", "goods_receipt_lines", ["receipt_id"])
        op.create_index("ix_goods_receipt_lines_po_line_id", "goods_receipt_lines", ["po_line_id"])


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in ("goods_receipt_lines", "goods_receipts", "purchase_order_lines", "purchase_orders"):
        if _table_exists(conn, tbl):
            op.drop_table(tbl)
