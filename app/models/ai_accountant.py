"""SQLAlchemy models for the AI accountant feature.

Three tables:

* ``ai_proposals`` — every write tool calls into this table to register a
  pending action keyed by ``confirmation_token``. Calling ``executeAction``
  with the same token a second time is a no-op (idempotency).
* ``ai_chat_sessions`` — one per chat conversation. Carries metadata
  (title, owner, timestamps).
* ``ai_chat_messages`` — one row per assistant / user / tool message inside
  a session, preserving turn order.

All datetime columns are timezone-aware (UTC). The JSONB columns store
already-validated payloads (validated via Pydantic in the API layer).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Portable JSON: maps to JSONB on PostgreSQL and JSON / text on SQLite.
# Migration 005 explicitly uses JSONB on Postgres for index-friendliness;
# the ORM type-hint uses the generic JSON variant so tests on SQLite work.
_JSONType = JSON().with_variant(JSONB(), "postgresql")

from app.db.base import Base


class AIProposal(Base):
    """A pending AI-tool proposal waiting on user confirmation.

    Lifecycle states (``status``):
        pending   — waiting for confirmation; executable
        executed  — confirmed and committed; idempotent on re-confirm
        expired   — older than 10 minutes; no longer executable
        cancelled — explicitly dismissed by the user
    """

    __tablename__ = "ai_proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    confirmation_token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(_JSONType, nullable=False)
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class AIChatSession(Base):
    """A single AI accountant conversation. Manages turn history + session memory."""

    __tablename__ = "ai_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["AIChatMessage"]] = relationship(
        "AIChatMessage", back_populates="session", cascade="all, delete-orphan",
        order_by="AIChatMessage.created_at",
    )


class AIChatMessage(Base):
    """One assistant / user / tool message inside an ``AIChatSession``.

    ``role``:  ``user``  — typed by the human
               ``assistant`` — LLM text turn (may include tool requests)
               ``tool`` — output of a tool execution

    ``content`` (JSONB) holds whichever shape matches the role:
        user / assistant text  → {"text": "..."}
        assistant tool-use     → {"text": "...", "tool_calls": [...]}
        tool result            → {"tool_call_id": "...", "result": {...}}
    """

    __tablename__ = "ai_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(_JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["AIChatSession"] = relationship("AIChatSession", back_populates="messages")
