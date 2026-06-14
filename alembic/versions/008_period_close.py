"""Period-close adjustments: accruals, prepayments, depreciation schedules.

Revision ID: 008
Revises: 007
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
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
    if not _table_exists(conn, "adjustments"):
        op.create_table(
            "adjustments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("amount", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("residual", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("periods", sa.Integer, nullable=False, server_default="1"),
            sa.Column("period_months", sa.Integer, nullable=False, server_default="1"),
            sa.Column("start_date", sa.Date, nullable=False),
            sa.Column("direction", sa.String(8), nullable=False, server_default="expense"),
            sa.Column("auto_reverse", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("periods_posted", sa.Integer, nullable=False, server_default="0"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("transaction_id", UUID(as_uuid=True), nullable=True),
            sa.Column("reversal_transaction_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_adjustments_kind", "adjustments", ["kind"])
        op.create_index("ix_adjustments_status", "adjustments", ["status"])
        op.create_index("ix_adjustments_start_date", "adjustments", ["start_date"])
        op.create_index("ix_adjustments_transaction_id", "adjustments", ["transaction_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "adjustments"):
        op.drop_table("adjustments")
