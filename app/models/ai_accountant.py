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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Portable JSON: maps to JSONB on PostgreSQL and JSON / text on SQLite.
# Migration 005 explicitly uses JSONB on Postgres for index-friendliness;
# the ORM type-hint uses the generic JSON variant so tests on SQLite work.
_JSONType = JSON().with_variant(JSONB(), "postgresql")

from app.db.base import Base
from app.db.tenant import TenantMixin


class AIProposal(Base, TenantMixin):
    """A pending AI-tool proposal waiting on user confirmation.

    Lifecycle states (``status``):
        pending   — waiting for confirmation; executable
        executed  — confirmed and committed; idempotent on re-confirm
        expired   — older than 10 minutes; no longer executable
        cancelled — explicitly dismissed by the user

    Two-person approval (roadmap 2026-09 §5.6): above the company's
    threshold, the requester's Confirm sets ``approval_status`` to
    ``requested`` (the proposal stays pending, for days rather than minutes)
    and someone else who can approve executes or rejects it.
    """

    __tablename__ = "ai_proposals"
    __table_args__ = (
        UniqueConstraint("confirmation_token", name="ai_proposals_confirmation_token_key"),
        # "this user's pending proposals" — also serves lookups by user alone
        Index("ix_ai_proposals_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    confirmation_token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
    # what the card said, and what it moves in the base currency (guardrails)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    approval_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIChatSession(Base, TenantMixin):
    """A single AI accountant conversation. Manages turn history + session memory."""

    __tablename__ = "ai_chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Soft delete — archived sessions disappear from the sidebar but keep
    # their history (and stay reachable for audit).
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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


class AIChatMessage(Base, TenantMixin):
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
    __table_args__ = (Index("ix_ai_chat_messages_session", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(_JSONType, nullable=False)
    # Stamped here to the microsecond (the database's now() is per second on
    # SQLite, and a message and its reply tied and came back in either order).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now(),
        nullable=False,
    )

    session: Mapped["AIChatSession"] = relationship("AIChatSession", back_populates="messages")


class AIReviewSample(Base, TenantMixin):
    """One chat turn kept for the company's owner to review (roadmap §5.5).

    About one turn in ten is kept (``app/services/ai_review.py``), as a
    snapshot: what was asked, what the assistant answered, the tools it
    called and the cards it made. It stays inside the company — nobody else,
    the platform included, reads the text — goes when its conversation is
    deleted, and expires after 90 days. The owner marks it ``good`` or
    ``bad`` (needs work) and may export it as an eval scenario.
    """

    __tablename__ = "ai_review_samples"
    __table_args__ = (Index("ix_ai_review_samples_created", "company_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")   # web | telegram | bale
    lang: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[list[Any]] = mapped_column(_JSONType, nullable=False)       # [{name, input, ok}], in order
    cards: Mapped[list[Any]] = mapped_column(_JSONType, nullable=False)       # [{tool, summary, amount}]
    tool_errors: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    turns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)  # good | bad
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # set in Python too: SQLite's now() has whole seconds, and the queue is newest first
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
