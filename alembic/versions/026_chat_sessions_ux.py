"""ChatGPT-style chat sessions: soft-delete + title backfill.

- ai_chat_sessions.archived: soft delete for the sessions sidebar (history
  kept; archived sessions just disappear from the list).
- Backfill titles for existing sessions from their first user message (first
  ~6 words), so pre-existing history shows up named in the sidebar instead of
  "Untitled". New sessions are auto-titled the same way at chat time.

Revision ID: 026
Revises: 025
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def _auto_title(text: str) -> str:
    words = (text or "").strip().split()
    title = " ".join(words[:6])
    return title[:60] if title else ""


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "ai_chat_sessions", "archived"):
        op.add_column(
            "ai_chat_sessions",
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    # Backfill titles from each untitled session's first user message.
    rows = conn.execute(sa.text(
        """
        SELECT s.id, (
            SELECT COALESCE(m.content->>'text', m.content->>'content')
            FROM ai_chat_messages m
            WHERE m.session_id = s.id AND m.role = 'user'
            ORDER BY m.created_at, m.id
            LIMIT 1
        ) AS first_text
        FROM ai_chat_sessions s
        WHERE s.title IS NULL OR s.title = ''
        """
    )).fetchall()
    for sid, first_text in rows:
        title = _auto_title(first_text or "")
        if title:
            conn.execute(
                sa.text("UPDATE ai_chat_sessions SET title = :t WHERE id = :i"),
                {"t": title, "i": sid},
            )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "ai_chat_sessions", "archived"):
        op.drop_column("ai_chat_sessions", "archived")
