"""Invoice e-mail log: manual sends and automatic overdue reminders
(roadmap §4.2).

Revision ID: 039
Revises: 038
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if "invoice_emails" in sa.inspect(conn).get_table_names():
        return
    op.create_table(
        "invoice_emails",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("invoice_id", UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False, index=True),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("to_address", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if "invoice_emails" in sa.inspect(conn).get_table_names():
        op.drop_table("invoice_emails")
