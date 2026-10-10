"""Phone messages by the phone's own id (roadmap ROADMAP_ANDROID_CHAT P0.5, P1.5).

The phone's outbox resends a message until it is answered; one row per
(user, client_message_id) is claimed before the turn runs, so a message that
arrives twice is answered once. The reply is kept for the repeat. Safe to run
twice.

Revision ID: 072
Revises: 071
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "mobile_turns" in _tables():
        return
    op.create_table(
        "mobile_turns",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_message_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="running"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("reply", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "client_message_id", name="pk_mobile_turns"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_mobile_turns_user"),
    )
    op.create_index("ix_mobile_turns_started_at", "mobile_turns", ["started_at"])


def downgrade() -> None:
    if "mobile_turns" in _tables():
        op.drop_table("mobile_turns")
