"""Support conversations (the owner, 2026-11-28, scenarios DA1–DA8).

Every signed-in person has one support conversation: a chat with the support desk, from the web page's
support button, the phone apps or the Telegram/Bale bot (``/support``). The server only keeps it. The desk
itself — Claude, answering, and the night shift fixing what it reports — runs on the owner's Mac
(Nightshift's support desk), which fetches new messages with a desk key and posts its replies back
(``/desk/…``, app/api/support.py). No AI and no Claude credentials on the server.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

_JSONType = JSON().with_variant(JSONB(), "postgresql")


class SupportThread(Base, TenantMixin):
    """One person's conversation with the support desk."""
    __tablename__ = "support_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, unique=True)
    # where the person last wrote from: web | android | ios | telegram | bale
    channel: Mapped[str] = mapped_column(String(16), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_desk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportMessage(Base, TenantMixin):
    """One message of a support conversation: the person's, or the desk's."""
    __tablename__ = "support_messages"
    __table_args__ = (
        Index("ix_support_messages_thread_created", "thread_id", "created_at"),
        # the desk's queue: the people's messages not yet handed to it
        Index("ix_support_messages_waiting", "author", "desk_taken_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_threads.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(8))                     # user | desk
    channel: Mapped[str] = mapped_column(String(16), default="web")   # web | android | ios | telegram | bale | desk
    text: Mapped[str] = mapped_column(Text, default="")
    # screenshots and files the person attached (transaction_attachments ids, never tied to a voucher)
    attachment_ids: Mapped[list[str] | None] = mapped_column(_JSONType, nullable=True)
    # what the app knew when it was sent: the page, the app's version, the language, recent errors' request ids
    context: Mapped[dict[str, Any] | None] = mapped_column(_JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # a person's message: when the desk fetched it (empty: still waiting for the desk)
    desk_taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # a desk message: when the person saw it (empty: unread, a badge on the support button)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
