"""Migration-from-another-system import: staged batches + completion queue.

- migration_batches: one row per uploaded chart/تفصیلی export set. Parsed rows
  live in JSONB payload so Confirm never re-reads the upload; status gives
  idempotency (confirming an applied batch returns the original result).
- migration_pending_records: the "Complete imported records" queue — imported
  entities with missing required fields (address, bank account, …) and review
  flags (ambiguous counterparty type).

Revision ID: 025
Revises: 024
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _table_exists(conn, "migration_batches"):
        op.create_table(
            "migration_batches",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("token", sa.String(80), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("source_files", JSONB(), nullable=False, server_default="[]"),
            sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
            sa.Column("summary", JSONB(), nullable=False, server_default="{}"),
            sa.Column("result", JSONB(), nullable=True),
            sa.Column("opening_date", sa.Date(), nullable=True),
            sa.Column("opening_transaction_id", UUID(as_uuid=True),
                      sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("company_id", "token", name="uq_migration_batch_token"),
        )
        op.create_index("ix_migration_batches_company_id", "migration_batches", ["company_id"])
        op.create_index("ix_migration_batches_token", "migration_batches", ["token"])

    if not _table_exists(conn, "migration_pending_records"):
        op.create_table(
            "migration_pending_records",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("batch_id", UUID(as_uuid=True),
                      sa.ForeignKey("migration_batches.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True),
                      sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entity_type", sa.String(32), nullable=False),
            sa.Column("source_code", sa.String(64), nullable=True),
            sa.Column("missing_fields", JSONB(), nullable=False, server_default="[]"),
            sa.Column("review_flags", JSONB(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_migration_pending_company_id", "migration_pending_records", ["company_id"])
        op.create_index("ix_migration_pending_batch_id", "migration_pending_records", ["batch_id"])
        op.create_index("ix_migration_pending_entity_id", "migration_pending_records", ["entity_id"])
        op.create_index("ix_migration_pending_status", "migration_pending_records", ["status"])


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "migration_pending_records"):
        op.drop_table("migration_pending_records")
    if _table_exists(conn, "migration_batches"):
        op.drop_table("migration_batches")
