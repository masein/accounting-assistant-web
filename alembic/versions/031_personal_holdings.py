"""personal_holdings — quantities of revaluable assets (gold, FX).

The ledger holds the rial cost of gold/foreign currency; this stores the
quantity in its native unit so net worth can be shown at current value.
Reporting-only: nothing here posts journal entries.

Revision ID: 031
Revises: 030
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "personal_holdings"):
        return
    op.create_table(
        "personal_holdings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("account_code", sa.String(64), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "account_code", "unit", name="uq_personal_holding_acct_unit"),
    )
    op.create_index("ix_personal_holdings_account_code", "personal_holdings", ["account_code"])
    op.create_index("ix_personal_holdings_unit", "personal_holdings", ["unit"])
    op.create_index("ix_personal_holdings_company_id", "personal_holdings", ["company_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "personal_holdings"):
        op.drop_table("personal_holdings")
