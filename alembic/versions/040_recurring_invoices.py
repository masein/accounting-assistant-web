"""Recurring sales invoices + the link from each invoice to its template
(roadmap §4.2).

Revision ID: 040
Revises: 039
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "recurring_invoices" not in insp.get_table_names():
        op.create_table(
            "recurring_invoices",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=False, index=True),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("items", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("frequency", sa.String(16), nullable=False, server_default="monthly"),
            sa.Column("calendar", sa.String(16), nullable=False, server_default="gregorian"),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("max_occurrences", sa.Integer(), nullable=True),
            sa.Column("next_run_date", sa.Date(), nullable=False, index=True),
            sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("terms_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("issue_status", sa.String(16), nullable=False, server_default="issued"),
            sa.Column("auto_send", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True),
            sa.Column("last_invoice_id", UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    cols = {c["name"] for c in sa.inspect(conn).get_columns("invoices")}
    if "recurring_invoice_id" not in cols:
        op.add_column("invoices", sa.Column("recurring_invoice_id", UUID(as_uuid=True), nullable=True))
        op.create_index("ix_invoices_recurring_invoice_id", "invoices", ["recurring_invoice_id"])
        op.create_foreign_key("fk_invoices_recurring_invoice", "invoices", "recurring_invoices",
                              ["recurring_invoice_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("invoices")}
    if "recurring_invoice_id" in cols:
        op.drop_constraint("fk_invoices_recurring_invoice", "invoices", type_="foreignkey")
        op.drop_index("ix_invoices_recurring_invoice_id", table_name="invoices")
        op.drop_column("invoices", "recurring_invoice_id")
    if "recurring_invoices" in sa.inspect(conn).get_table_names():
        op.drop_table("recurring_invoices")
