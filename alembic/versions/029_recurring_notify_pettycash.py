"""Recurring auto-posting + notifications feed + petty cash + reminders.

- recurring_rules: end_date, bank_account_code, counter_account_code,
  auto_post, last_run_date — rules can now materialize real journal entries
  through a chosen bank account instead of being inert reminders.
- notifications: the first persisted alert feed (invoice due/overdue, payroll
  payday, pending approvals, recurring schedules, reminders), deduped per
  (company, dedupe_key).
- reminders: user-created reminders with repeat interval + lead time.
- petty_cash_accounts / petty_cash_transactions: per-user تنخواه imprest
  subledger with a pending→approved/rejected flow; GL postings reference the
  petty_cash resolver category.

Revision ID: 029
Revises: 028
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


_RECURRING_COLS = [
    ("end_date", sa.Date(), None),
    ("bank_account_code", sa.String(16), None),
    ("counter_account_code", sa.String(16), None),
    ("auto_post", sa.Boolean(), "true"),
    ("last_run_date", sa.Date(), None),
]


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    for name, coltype, default in _RECURRING_COLS:
        if not _column_exists(conn, "recurring_rules", name):
            kwargs = {"server_default": default} if default is not None else {}
            op.add_column("recurring_rules", sa.Column(name, coltype, nullable=(default is None), **kwargs))

    if not _table_exists(conn, "notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("level", sa.String(16), nullable=False, server_default="info"),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column("link_page", sa.String(64), nullable=True),
            sa.Column("dedupe_key", sa.String(160), nullable=False),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("company_id", "dedupe_key", name="uq_notifications_dedupe"),
        )
        op.create_index("ix_notifications_company_id", "notifications", ["company_id"])
        op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
        op.create_index("ix_notifications_kind", "notifications", ["kind"])

    if not _table_exists(conn, "reminders"):
        op.create_table(
            "reminders",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=False),
            sa.Column("repeat", sa.String(16), nullable=False, server_default="none"),
            sa.Column("days_before", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_reminders_company_id", "reminders", ["company_id"])
        op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
        op.create_index("ix_reminders_due_date", "reminders", ["due_date"])

    if not _table_exists(conn, "petty_cash_accounts"):
        op.create_table(
            "petty_cash_accounts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("holder_name", sa.String(256), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_petty_cash_accounts_company_id", "petty_cash_accounts", ["company_id"])
        op.create_index("ix_petty_cash_accounts_user_id", "petty_cash_accounts", ["user_id"])

    if not _table_exists(conn, "petty_cash_transactions"):
        op.create_table(
            "petty_cash_transactions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("account_id", UUID(as_uuid=True),
                      sa.ForeignKey("petty_cash_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("amount", sa.BigInteger(), nullable=False),
            sa.Column("signed_amount", sa.BigInteger(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("counter_account_code", sa.String(16), nullable=True),
            sa.Column("attachment_id", UUID(as_uuid=True),
                      sa.ForeignKey("transaction_attachments.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("decided_by", sa.String(64), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_petty_cash_txns_company_id", "petty_cash_transactions", ["company_id"])
        op.create_index("ix_petty_cash_txns_account_id", "petty_cash_transactions", ["account_id"])
        op.create_index("ix_petty_cash_txns_status", "petty_cash_transactions", ["status"])


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("petty_cash_transactions", "petty_cash_accounts", "reminders", "notifications"):
        if _table_exists(conn, table):
            op.drop_table(table)
    for name, _t, _d in _RECURRING_COLS:
        if _column_exists(conn, "recurring_rules", name):
            op.drop_column("recurring_rules", name)
