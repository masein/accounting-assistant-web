"""Household invitations into personal books (roadmap 2026-09 §4.12). Idempotent.

Revision ID: 064
Revises: 063
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("household_invites"):
        return
    op.create_table(
        "household_invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_household_invites_company_id", "household_invites", ["company_id"])
    op.create_index("ix_household_invites_token_hash", "household_invites", ["token_hash"], unique=True)


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("household_invites"):
        op.drop_table("household_invites")
