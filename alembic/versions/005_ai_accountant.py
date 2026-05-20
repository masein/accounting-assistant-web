"""AI accountant infrastructure: audit_logs extension, ai_proposals,
ai_chat_sessions, ai_chat_messages.

Revision ID: 005
Revises: 004
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
            ),
            {"t": table},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ─── Extend audit_logs ───────────────────────────────────────────
    if not _column_exists(conn, "audit_logs", "actor_source"):
        op.add_column(
            "audit_logs",
            sa.Column("actor_source", sa.String(32), nullable=False, server_default="manual"),
        )
        op.create_index("ix_audit_logs_actor_source", "audit_logs", ["actor_source"])
    if not _column_exists(conn, "audit_logs", "session_id"):
        op.add_column("audit_logs", sa.Column("session_id", sa.String(64), nullable=True))
        op.create_index("ix_audit_logs_session_id", "audit_logs", ["session_id"])
    if not _column_exists(conn, "audit_logs", "tool_name"):
        op.add_column("audit_logs", sa.Column("tool_name", sa.String(64), nullable=True))
    if not _column_exists(conn, "audit_logs", "confirmation_token"):
        op.add_column(
            "audit_logs",
            sa.Column("confirmation_token", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        )
    if not _column_exists(conn, "audit_logs", "user_message"):
        op.add_column("audit_logs", sa.Column("user_message", sa.Text, nullable=True))

    # ─── ai_proposals ────────────────────────────────────────────────
    if not _table_exists(conn, "ai_proposals"):
        op.create_table(
            "ai_proposals",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "confirmation_token",
                sa.dialects.postgresql.UUID(as_uuid=True),
                nullable=False, unique=True,
            ),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("session_id", sa.String(64), nullable=True),
            sa.Column("tool_name", sa.String(64), nullable=False),
            sa.Column("tool_input", sa.dialects.postgresql.JSONB, nullable=False),
            sa.Column("user_message", sa.Text, nullable=True),
            sa.Column(
                "status", sa.String(16),
                nullable=False, server_default="pending",
            ),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "executed_audit_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_ai_proposals_user_status", "ai_proposals", ["user_id", "status"]
        )
        op.create_index(
            "ix_ai_proposals_token", "ai_proposals", ["confirmation_token"], unique=True
        )

    # ─── ai_chat_sessions ────────────────────────────────────────────
    if not _table_exists(conn, "ai_chat_sessions"):
        op.create_table(
            "ai_chat_sessions",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False, index=True),
            sa.Column("title", sa.String(256), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
        )
        op.create_index("ix_ai_chat_sessions_user", "ai_chat_sessions", ["user_id"])

    # ─── ai_chat_messages ────────────────────────────────────────────
    if not _table_exists(conn, "ai_chat_messages"):
        op.create_table(
            "ai_chat_messages",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "session_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("content", sa.dialects.postgresql.JSONB, nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
        )
        op.create_index(
            "ix_ai_chat_messages_session", "ai_chat_messages", ["session_id", "created_at"]
        )


def downgrade() -> None:
    # Drop in reverse order
    if _table_exists(op.get_bind(), "ai_chat_messages"):
        op.drop_index("ix_ai_chat_messages_session", table_name="ai_chat_messages")
        op.drop_table("ai_chat_messages")
    if _table_exists(op.get_bind(), "ai_chat_sessions"):
        op.drop_index("ix_ai_chat_sessions_user", table_name="ai_chat_sessions")
        op.drop_table("ai_chat_sessions")
    if _table_exists(op.get_bind(), "ai_proposals"):
        op.drop_index("ix_ai_proposals_token", table_name="ai_proposals")
        op.drop_index("ix_ai_proposals_user_status", table_name="ai_proposals")
        op.drop_table("ai_proposals")
    conn = op.get_bind()
    for col, idx in (
        ("user_message", None),
        ("confirmation_token", None),
        ("tool_name", None),
        ("session_id", "ix_audit_logs_session_id"),
        ("actor_source", "ix_audit_logs_actor_source"),
    ):
        if _column_exists(conn, "audit_logs", col):
            if idx:
                op.drop_index(idx, table_name="audit_logs")
            op.drop_column("audit_logs", col)
