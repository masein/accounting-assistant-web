"""E-mailed statements and payslips (roadmap 2026-09 §4.9). Idempotent.

Revision ID: 061
Revises: 060
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("document_emails"):
        return
    op.create_table(
        "document_emails",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=True),
        sa.Column("pay_run_id", UUID(as_uuid=True), sa.ForeignKey("pay_runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("to_address", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for col in ("company_id", "kind", "entity_id", "pay_run_id", "status"):
        op.create_index(f"ix_document_emails_{col}", "document_emails", [col])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("document_emails"):
        op.drop_table("document_emails")
