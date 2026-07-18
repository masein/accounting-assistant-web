"""Shareholder equity: cap table + tagged equity-movement events + registered capital.

- shareholdings: one row per shareholder (shares / ownership percent / par value /
  since / class) — the cap table.
- equity_events: tags a GL posting as a contribution / capital increase / dividend
  declaration or payment / current-account movement, so the changes-in-equity
  statement can show real movement rows and the cap table can attribute paid-in
  capital per shareholder.
- companies.registered_capital: authorised/registered share capital (minor units).

New equity/liability accounts (share capital, retained earnings, dividends payable,
shareholders' current account) are added to the seed charts for fresh tenants and
self-healed by account_resolver for existing ones on first posting.

Revision ID: 022
Revises: 021
Create Date: 2026-07-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
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


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _column_exists(conn, "companies", "registered_capital"):
        op.add_column(
            "companies",
            sa.Column("registered_capital", sa.BigInteger(), nullable=False, server_default="0"),
        )

    if not _table_exists(conn, "shareholdings"):
        op.create_table(
            "shareholdings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shares", sa.BigInteger(), nullable=True),
            sa.Column("percent", sa.Numeric(7, 4), nullable=True),
            sa.Column("par_value", sa.BigInteger(), nullable=True),
            sa.Column("since", sa.Date(), nullable=True),
            sa.Column("share_class", sa.String(16), nullable=False, server_default="ordinary"),
            sa.Column("notes", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_shareholdings_company_id", "shareholdings", ["company_id"])
        op.create_index("ix_shareholdings_entity_id", "shareholdings", ["entity_id"])

    if not _table_exists(conn, "equity_events"):
        op.create_table(
            "equity_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
            sa.Column("transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("funded_from", sa.String(24), nullable=True),
            sa.Column("group_ref", sa.String(64), nullable=True),
            sa.Column("description", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_equity_events_company_id", "equity_events", ["company_id"])
        op.create_index("ix_equity_events_event_type", "equity_events", ["event_type"])
        op.create_index("ix_equity_events_date", "equity_events", ["date"])
        op.create_index("ix_equity_events_entity_id", "equity_events", ["entity_id"])
        op.create_index("ix_equity_events_transaction_id", "equity_events", ["transaction_id"])
        op.create_index("ix_equity_events_group_ref", "equity_events", ["group_ref"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "equity_events"):
        op.drop_table("equity_events")
    if _table_exists(conn, "shareholdings"):
        op.drop_table("shareholdings")
    if _column_exists(conn, "companies", "registered_capital"):
        op.drop_column("companies", "registered_capital")
