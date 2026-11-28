"""Support from the Telegram/Bale bot (2026-11-28, scenario DA9).

``/support`` in a signed-in chat writes to the support desk instead of the assistant, and the desk's reply comes
back to that chat:

* ``messenger_links.mode``: "support" while the chat writes to the desk (``/done`` goes back to the assistant).
* ``support_threads.chat_platform`` / ``chat_id``: the chat a person last wrote to support from.
* ``messenger_outbox``: a message for a chat outside any update being answered (the desk's reply). A bot on a
  webhook sends it at once; the Telegram relay fetches it, since the server can't reach Telegram.

Safe to run twice; a fresh database gets all of it from ``create_all``.

Revision ID: 091
Revises: 090
Create Date: 2026-11-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "091"
down_revision: Union[str, None] = "090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("messenger_links") and "mode" not in _columns(insp, "messenger_links"):
        op.add_column("messenger_links", sa.Column("mode", sa.String(16), nullable=True))
    if insp.has_table("support_threads"):
        cols = _columns(insp, "support_threads")
        if "chat_platform" not in cols:
            op.add_column("support_threads", sa.Column("chat_platform", sa.String(16), nullable=True))
        if "chat_id" not in cols:
            op.add_column("support_threads", sa.Column("chat_id", sa.String(64), nullable=True))
    if not insp.has_table("messenger_outbox"):
        op.create_table(
            "messenger_outbox",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("chat_id", sa.String(64), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_messenger_outbox_waiting", "messenger_outbox", ["platform", "sent_at"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("messenger_outbox"):
        op.drop_table("messenger_outbox")
    if insp.has_table("support_threads"):
        cols = _columns(insp, "support_threads")
        for col in ("chat_id", "chat_platform"):
            if col in cols:
                op.drop_column("support_threads", col)
    if insp.has_table("messenger_links") and "mode" in _columns(insp, "messenger_links"):
        op.drop_column("messenger_links", "mode")
