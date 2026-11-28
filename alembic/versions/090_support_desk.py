"""Support conversations: a chat with the support desk from the app, the phones and the bot (2026-11-28, DA1–DA8).

The owner wants a support button: a person who meets a problem writes to the support desk — Claude, running on
the owner's Mac in Nightshift's support desk — and what needs fixing is fixed by the night shift. The server keeps
only the conversations; the desk fetches new messages with its own key and posts its replies (app/api/support.py).

* ``support_threads``: one per person, and where they last wrote from.
* ``support_messages``: the person's and the desk's messages, with the screenshots attached, what the app knew
  when it was sent (page, version, language, recent errors' request ids), when the desk took a message and when
  the person saw a reply.

Both are tenant tables, guarded like every table added after 015 (``install_tenant_guards``). Safe to run twice;
a fresh database gets them from ``create_all``.

Revision ID: 090
Revises: 089
Create Date: 2026-11-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "090"
down_revision: Union[str, None] = "089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("support_threads"):
        op.create_table(
            "support_threads",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("user_id", UUID(as_uuid=True), nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_user_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_desk_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_support_threads_company_id", "support_threads", ["company_id"])
        op.create_index("ix_support_threads_user_id", "support_threads", ["user_id"], unique=True)
    if not insp.has_table("support_messages"):
        op.create_table(
            "support_messages",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("company_id", UUID(as_uuid=True), nullable=True),
            sa.Column("thread_id", UUID(as_uuid=True),
                      sa.ForeignKey("support_threads.id", ondelete="CASCADE"), nullable=False),
            sa.Column("author", sa.String(8), nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("attachment_ids", _JSON, nullable=True),
            sa.Column("context", _JSON, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("desk_taken_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_support_messages_company_id", "support_messages", ["company_id"])
        op.create_index("ix_support_messages_thread_id", "support_messages", ["thread_id"])
        op.create_index("ix_support_messages_thread_created", "support_messages", ["thread_id", "created_at"])
        op.create_index("ix_support_messages_waiting", "support_messages", ["author", "desk_taken_at"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in ("support_messages", "support_threads"):
        if insp.has_table(table):
            op.drop_table(table)
