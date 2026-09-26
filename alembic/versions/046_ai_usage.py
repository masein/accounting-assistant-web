"""AI usage ledger and token budgets (roadmap 2026-09 §2.5).

``ai_usage_events``: one row per provider call. ``companies`` gains the two
budget columns (NULL = platform default). Idempotent on fresh and migrated
databases alike.

Revision ID: 046
Revises: 045
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("companies")}
    for name in ("ai_daily_token_budget", "ai_user_daily_token_budget"):
        if name not in cols:
            op.add_column("companies", sa.Column(name, sa.BigInteger(), nullable=True))
    if not insp.has_table("ai_usage_events"):
        op.create_table(
            "ai_usage_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE", name="fk_ai_usage_events_company"),
                      nullable=True),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("username", sa.String(128), nullable=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("purpose", sa.String(24), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), nullable=False),
            sa.Column("output_tokens", sa.BigInteger(), nullable=False),
            sa.Column("cached_tokens", sa.BigInteger(), nullable=False),
            sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False),
            sa.Column("estimated", sa.Boolean(), nullable=False),
            sa.Column("cost_micros", sa.BigInteger(), nullable=True),
            sa.Column("outcome", sa.String(8), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(64), nullable=True),
        )
        op.create_index("ix_ai_usage_events_created_at", "ai_usage_events", ["created_at"])
        op.create_index("ix_ai_usage_company_created", "ai_usage_events", ["company_id", "created_at"])
        op.create_index("ix_ai_usage_user_created", "ai_usage_events", ["user_id", "created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if insp.has_table("ai_usage_events"):
        op.drop_table("ai_usage_events")
    cols = {c["name"] for c in insp.get_columns("companies")}
    for name in ("ai_user_daily_token_budget", "ai_daily_token_budget"):
        if name in cols:
            op.drop_column("companies", name)
