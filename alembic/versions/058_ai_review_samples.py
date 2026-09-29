"""AI review queue (roadmap 2026-09 §5.5, part 2): chat turns kept for the
owner to review. Idempotent.

Revision ID: 058
Revises: 057
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("ai_review_samples"):
        return
    op.create_table(
        "ai_review_samples",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True),
                  sa.ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("lang", sa.String(8), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("tools", JSONB(), nullable=False),
        sa.Column("cards", JSONB(), nullable=False),
        sa.Column("tool_errors", sa.BigInteger(), nullable=False),
        sa.Column("turns", sa.BigInteger(), nullable=False),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("verdict", sa.String(8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_review_samples_company_id", "ai_review_samples", ["company_id"])
    op.create_index("ix_ai_review_samples_session_id", "ai_review_samples", ["session_id"])
    op.create_index("ix_ai_review_samples_verdict", "ai_review_samples", ["verdict"])
    op.create_index("ix_ai_review_samples_created", "ai_review_samples", ["company_id", "created_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ai_review_samples"):
        op.drop_table("ai_review_samples")
