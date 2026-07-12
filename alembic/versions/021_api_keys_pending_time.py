"""Inbound time-tracking API (Part B): company API keys + parked pushes.

- api_keys: per-company service credentials (SHA-256 hash, label, revocable).
- pending_time_entries: pushes whose worker couldn't be matched — parked for
  the employer to resolve in-app, never silently dropped.

(time_entries.source/external_id landed in migration 020.)

Revision ID: 021
Revises: 020
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True),
                      sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("label", sa.String(128), nullable=False, server_default="integration"),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("prefix", sa.String(16), nullable=False),
            sa.Column("revoked", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_api_keys_company_id", "api_keys", ["company_id"])
        op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
        op.create_index("ix_api_keys_revoked", "api_keys", ["revoked"])

    if not _table_exists(conn, "pending_time_entries"):
        op.create_table(
            "pending_time_entries",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("external_id", sa.String(128), nullable=False),
            sa.Column("worker_ref", sa.String(256), nullable=False),
            sa.Column("client_ref", sa.String(256), nullable=True),
            sa.Column("project_ref", sa.String(256), nullable=True),
            sa.Column("work_date", sa.Date, nullable=False),
            sa.Column("hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("entry_type", sa.String(16), nullable=False, server_default="work"),
            sa.Column("billable", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("reason", sa.String(256), nullable=True),
            sa.Column("resolved_entry_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_pending_time_company", "pending_time_entries", ["company_id"])
        op.create_index("ix_pending_time_status", "pending_time_entries", ["status"])
        op.create_index(
            "ix_pending_time_source_external", "pending_time_entries",
            ["company_id", "source", "external_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "pending_time_entries"):
        op.drop_table("pending_time_entries")
    if _table_exists(conn, "api_keys"):
        op.drop_table("api_keys")
