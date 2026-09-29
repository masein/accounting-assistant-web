"""Savings goals for personal books (roadmap 2026-09 §4.12). Idempotent.

Revision ID: 063
Revises: 062
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("savings_goals"):
        return
    op.create_table(
        "savings_goals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("account_code", sa.String(64), nullable=False),
        sa.Column("target_amount", sa.BigInteger(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for col in ("company_id", "account_code", "archived"):
        op.create_index(f"ix_savings_goals_{col}", "savings_goals", [col])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("savings_goals"):
        op.drop_table("savings_goals")
