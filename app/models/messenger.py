"""Messenger bots (roadmap 2026-09 §5.7, part 2): a Telegram or Bale chat
linked to one login, so "۵۰ هزار نان" sent from the phone reaches the same
assistant as the web chat, under that user's company, role and AI budget.

A link starts ``pending`` with a one-time ``code`` (10 minutes) the user opens
as ``https://t.me/<bot>?start=<code>`` / ``https://ble.ir/<bot>?start=<code>``;
the bot's ``/start <code>`` fills ``chat_id`` and makes it ``active``.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class MessengerLink(Base, TenantMixin):
    __tablename__ = "messenger_links"
    __table_args__ = (
        # one login per chat on a platform
        Index("uq_messenger_links_chat", "platform", "chat_id", unique=True,
              postgresql_where=text("chat_id IS NOT NULL"), sqlite_where=text("chat_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))                    # telegram | bale
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active
    code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # the web chat session the bot continues (a /new command starts another)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessengerUpdate(Base):
    """Updates already handled — a platform re-sends one it thinks we missed."""
    __tablename__ = "messenger_updates"
    __table_args__ = (Index("uq_messenger_updates", "platform", "update_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    update_id: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
