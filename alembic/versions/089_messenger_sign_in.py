"""A Telegram or Bale chat signs in on its own (2026-11-28, scenarios CU1–CU9).

The owner wants people who only use the bot to need nothing else: sign in in the
chat — username, password, the two-factor code — or make a new account there.

* ``messenger_sign_ins``: where a chat is in that conversation (no company yet,
  so not a tenant table). The password is never stored.
* ``messenger_links.token_version``: the user's when the chat got in. A password
  change, "sign out everywhere" or a suspended company ends the chat's sign-in,
  as it ends a browser's and a phone's. Existing links take their user's
  current value, so none of them ends now.
* ``messenger_links.via``: how the chat got in (``code``, ``password``,
  ``signup``); empty for the links made before.
* ``messenger_updates.done_at``: an update is marked handled at the end, so one
  the server stopped half way through is handled again on the next try instead
  of lost. Those already recorded were handled: they take their received time.

Safe to run twice; a fresh database gets all of it from ``create_all``.

Revision ID: 089
Revises: 088
Create Date: 2026-11-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "089"
down_revision: Union[str, None] = "088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("messenger_sign_ins"):
        op.create_table(
            "messenger_sign_ins",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("chat_id", sa.String(64), nullable=False),
            sa.Column("purpose", sa.String(16), nullable=False),
            sa.Column("step", sa.String(16), nullable=False),
            sa.Column("username", sa.String(64), nullable=True),
            sa.Column("email", sa.String(254), nullable=True),
            sa.Column("challenge", sa.Text(), nullable=True),
            sa.Column("lang", sa.String(8), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("uq_messenger_sign_ins_chat", "messenger_sign_ins", ["platform", "chat_id"], unique=True)
    if insp.has_table("messenger_links"):
        cols = _columns(insp, "messenger_links")
        if "token_version" not in cols:
            op.add_column("messenger_links", sa.Column("token_version", sa.Integer(), server_default="0",
                                                       nullable=False))
            bind.execute(sa.text(
                "UPDATE messenger_links SET token_version = COALESCE(("
                "  SELECT u.token_version FROM users u WHERE u.id = messenger_links.user_id), 0)"))
        if "via" not in cols:
            op.add_column("messenger_links", sa.Column("via", sa.String(16), nullable=True))
    if insp.has_table("messenger_updates") and "done_at" not in _columns(insp, "messenger_updates"):
        op.add_column("messenger_updates", sa.Column("done_at", sa.DateTime(timezone=True), nullable=True))
        bind.execute(sa.text("UPDATE messenger_updates SET done_at = received_at"))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("messenger_updates") and "done_at" in _columns(insp, "messenger_updates"):
        op.drop_column("messenger_updates", "done_at")
    if insp.has_table("messenger_links"):
        cols = _columns(insp, "messenger_links")
        for col in ("via", "token_version"):
            if col in cols:
                op.drop_column("messenger_links", col)
    if insp.has_table("messenger_sign_ins"):
        op.drop_table("messenger_sign_ins")
