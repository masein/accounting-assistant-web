"""AR/AP depth: payments and credit_notes tables.

Revision ID: 006
Revises: 005
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
            {"t": table},
        ).first()
    )


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _column_exists(conn, "invoices", "scheduled_payment_date"):
        op.add_column("invoices", sa.Column("scheduled_payment_date", sa.Date, nullable=True))
        op.create_index("ix_invoices_scheduled_payment_date", "invoices", ["scheduled_payment_date"])

    if not _table_exists(conn, "payments"):
        op.create_table(
            "payments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("invoice_id", UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("date", sa.Date, nullable=False),
            sa.Column("amount", sa.BigInteger, nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("method", sa.String(16), nullable=False, server_default="bank"),
            sa.Column("direction", sa.String(8), nullable=False),
            sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])
        op.create_index("ix_payments_direction", "payments", ["direction"])
        op.create_index("ix_payments_transaction_id", "payments", ["transaction_id"])
        op.create_index("ix_payments_date", "payments", ["date"])

    if not _table_exists(conn, "credit_notes"):
        op.create_table(
            "credit_notes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("invoice_id", UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=True),
            sa.Column("kind", sa.String(16), nullable=False, server_default="sales"),
            sa.Column("date", sa.Date, nullable=False),
            sa.Column("amount", sa.BigInteger, nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("reason", sa.Text, nullable=True),
            sa.Column("note_type", sa.String(16), nullable=False, server_default="reduction"),
            sa.Column("transaction_id", UUID(as_uuid=True), sa.ForeignKey("transactions.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_credit_notes_invoice_id", "credit_notes", ["invoice_id"])
        op.create_index("ix_credit_notes_entity_id", "credit_notes", ["entity_id"])
        op.create_index("ix_credit_notes_kind", "credit_notes", ["kind"])
        op.create_index("ix_credit_notes_note_type", "credit_notes", ["note_type"])
        op.create_index("ix_credit_notes_transaction_id", "credit_notes", ["transaction_id"])
        op.create_index("ix_credit_notes_date", "credit_notes", ["date"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "credit_notes"):
        op.drop_table("credit_notes")
    if _table_exists(conn, "payments"):
        op.drop_table("payments")
