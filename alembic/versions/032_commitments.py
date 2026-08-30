"""commitments — loan installments and post-dated cheques.

One dated obligation per row; installments of a loan share plan_id.

Revision ID: 032
Revises: 031
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "commitments"):
        return
    op.create_table(
        "commitments",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="installment"),
        sa.Column("direction", sa.String(8), nullable=False, server_default="pay"),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("plan_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("plan_total", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(64), nullable=True),
        sa.Column("bank_name", sa.String(128), nullable=True),
        sa.Column("counterparty", sa.String(256), nullable=True),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("counter_account_code", sa.String(64), nullable=True),
        sa.Column("settled_on", sa.Date(), nullable=True),
        sa.Column("settled_transaction_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for col in ("company_id", "kind", "direction", "due_date", "status", "plan_id",
                "reference", "entity_id"):
        op.create_index(f"ix_commitments_{col}", "commitments", [col])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "commitments"):
        op.drop_table("commitments")
