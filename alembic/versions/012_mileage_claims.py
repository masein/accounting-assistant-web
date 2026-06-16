"""Mileage claims (expense reimbursements with approval routing).

Revision ID: 012
Revises: 011
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
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
    if not _table_exists(conn, "mileage_claims"):
        op.create_table(
            "mileage_claims",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id"), nullable=True),
            sa.Column("employee_name", sa.String(256), nullable=False),
            sa.Column("claim_date", sa.Date, nullable=False),
            sa.Column("distance", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("unit", sa.String(8), nullable=False, server_default="mile"),
            sa.Column("rate", sa.Numeric(12, 4), nullable=False, server_default="0"),
            sa.Column("amount", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("purpose", sa.Text, nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="approved"),
            sa.Column("transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reimbursement_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decided_by", sa.String(128), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_mileage_claims_entity_id", "mileage_claims", ["entity_id"])
        op.create_index("ix_mileage_claims_status", "mileage_claims", ["status"])
        op.create_index("ix_mileage_claims_claim_date", "mileage_claims", ["claim_date"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "mileage_claims"):
        op.drop_table("mileage_claims")
