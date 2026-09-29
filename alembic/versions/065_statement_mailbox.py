"""Statements by e-mail (roadmap 2026-09 §4.1): the messages read from a
company's statements mailbox, and where a statement came from. Idempotent.

Revision ID: 065
Revises: 064
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("statement_mail_messages"):
        op.create_table(
            "statement_mail_messages",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("message_key", sa.String(64), nullable=False),
            sa.Column("sender", sa.String(254), nullable=False),
            sa.Column("subject", sa.String(300), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("statement_ids", sa.Text(), nullable=True),
            sa.Column("detail", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_statement_mail_messages_company_id", "statement_mail_messages", ["company_id"])
        op.create_index("ix_statement_mail_messages_status", "statement_mail_messages", ["status"])
        op.create_index("uq_statement_mail_company_key", "statement_mail_messages", ["company_id", "message_key"],
                        unique=True)
    if "origin" not in {c["name"] for c in insp.get_columns("bank_statements")}:
        op.add_column("bank_statements", sa.Column("origin", sa.String(16), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "origin" in {c["name"] for c in insp.get_columns("bank_statements")}:
        op.drop_column("bank_statements", "origin")
    if insp.has_table("statement_mail_messages"):
        op.drop_table("statement_mail_messages")
