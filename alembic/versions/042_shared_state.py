"""Shared state for multi-worker deployments (roadmap §2.2): rate-limit
events, per-company books version for caches, upload tokens.

Revision ID: 042
Revises: 041
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    have = set(sa.inspect(conn).get_table_names())
    if "rate_limit_events" not in have:
        op.create_table(
            "rate_limit_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("bucket", sa.String(32), nullable=False),
            sa.Column("identity", sa.String(256), nullable=False),
            sa.Column("at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_rate_limit_events_bucket_identity_at", "rate_limit_events", ["bucket", "identity", "at"])
    if "books_versions" not in have:
        op.create_table(
            "books_versions",
            sa.Column("scope", sa.String(64), primary_key=True),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        )
    if "upload_tokens" not in have:
        op.create_table(
            "upload_tokens",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("token", sa.String(300), nullable=False),
            sa.Column("file_path", sa.String(1024), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False, index=True),
            sa.UniqueConstraint("company_id", "kind", "token", name="uq_upload_tokens_company_kind_token"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    have = set(sa.inspect(conn).get_table_names())
    for t in ("upload_tokens", "books_versions", "rate_limit_events"):
        if t in have:
            op.drop_table(t)
