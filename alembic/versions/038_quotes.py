"""Sales quotes (پیش‌فاکتور) and their lines (roadmap §4.2).

Revision ID: 038
Revises: 037
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return table in sa.inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "quotes"):
        op.create_table(
            "quotes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            # Same shape as TenantMixin (nullable, indexed, stamped by the session
            # events) — matches migrations 031/032 and the model.
            sa.Column("company_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("number", sa.String(128), nullable=False, index=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft", index=True),
            sa.Column("issue_date", sa.Date(), nullable=False, index=True),
            sa.Column("valid_until", sa.Date(), nullable=False, index=True),
            sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=True, index=True),
            sa.Column("converted_invoice_id", UUID(as_uuid=True),
                      sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("company_id", "number", name="uq_quotes_company_number"),
        )
    if not _table_exists(conn, "quote_items"):
        op.create_table(
            "quote_items",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            # Same shape as TenantMixin (nullable, indexed, stamped by the session
            # events) — matches migrations 031/032 and the model.
            sa.Column("company_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("quote_id", UUID(as_uuid=True), sa.ForeignKey("quotes.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("product_name", sa.String(256), nullable=False),
            sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="1"),
            sa.Column("unit_price", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("unit_cost", sa.BigInteger(), nullable=True),
            sa.Column("line_total", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("tax_rate", sa.Numeric(7, 4), nullable=False, server_default="0"),
            sa.Column("taxable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("tax_code", sa.String(64), nullable=True),
            sa.Column("tax_treatment", sa.String(24), nullable=False, server_default="standard"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("inventory_item_id", UUID(as_uuid=True), sa.ForeignKey("inventory_items.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "quote_items"):
        op.drop_table("quote_items")
    if _table_exists(conn, "quotes"):
        op.drop_table("quotes")
