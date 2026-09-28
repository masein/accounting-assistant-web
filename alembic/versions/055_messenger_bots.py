"""Messenger bots (roadmap 2026-09 §5.7, part 2): chats linked to logins, and
the updates already handled. Idempotent.

Revision ID: 055
Revises: 054
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("messenger_links"):
        op.create_table(
            "messenger_links",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("user_id", UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("code", sa.String(32), nullable=True),
            sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("chat_id", sa.String(64), nullable=True),
            sa.Column("chat_name", sa.String(128), nullable=True),
            sa.Column("session_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        )
        for col in ("company_id", "user_id", "code"):
            op.create_index(f"ix_messenger_links_{col}", "messenger_links", [col])
        op.create_index("uq_messenger_links_chat", "messenger_links", ["platform", "chat_id"], unique=True,
                        postgresql_where=sa.text("chat_id IS NOT NULL"))
    if not insp.has_table("messenger_updates"):
        op.create_table(
            "messenger_updates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("update_id", sa.BigInteger(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("uq_messenger_updates", "messenger_updates", ["platform", "update_id"], unique=True)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in ("messenger_updates", "messenger_links"):
        if insp.has_table(table):
            op.drop_table(table)
