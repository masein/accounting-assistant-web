"""Correction memory (roadmap 2026-09 §5.4): the account and party the user
chose for a narration, learned from their corrections. Idempotent.

Revision ID: 054
Revises: 053
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("learned_preferences"):
        return
    op.create_table(
        "learned_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pattern", sa.String(256), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("account_code", sa.String(64), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("times_chosen", sa.Integer(), nullable=False),
        sa.Column("times_used", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_learned_preferences_company_id", "learned_preferences", ["company_id"])
    op.create_index("uq_learned_preferences_pattern", "learned_preferences", ["company_id", "pattern"], unique=True)


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("learned_preferences"):
        op.drop_table("learned_preferences")
