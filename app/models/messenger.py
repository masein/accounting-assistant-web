"""Messenger bots (roadmap 2026-09 §5.7, part 2): a Telegram or Bale chat
linked to one login, so "۵۰ هزار نان" sent from the phone reaches the same
assistant as the web chat, under that user's company, role and AI budget.

A link starts ``pending`` with a one-time ``code`` (10 minutes) the user opens
as ``https://t.me/<bot>?start=<code>`` / ``https://ble.ir/<bot>?start=<code>``;
the bot's ``/start <code>`` fills ``chat_id`` and makes it ``active``. A chat can
also sign in on its own — username, password and the two-factor code typed in
the chat — or make a new account there (``MessengerSignIn`` keeps where that
conversation is), so someone who only uses Telegram or Bale never needs the app.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, text
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
    # the user's token_version when the chat got in: a password change, "sign out everywhere" or a suspended
    # company ends the chat's sign-in as it ends a browser's and a phone's (scenario CU6)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # how the chat got in: "code" (the app's link), "password" (signed in in the chat) or "signup"
    via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # "support" while the chat writes to the support desk (/support … /done); empty: the assistant (DA9)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)


class MessengerUpdate(Base):
    """Updates already handled — a platform re-sends one it thinks we missed."""
    __tablename__ = "messenger_updates"
    __table_args__ = (Index("uq_messenger_updates", "platform", "update_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    update_id: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # handled to the end; empty while it is handled — or when the server stopped half way, and then the platform's
    # (or the relay's) next try is handled again instead of lost (scenario CU9)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessengerSignIn(Base):
    """A chat in the middle of signing in or making an account: which step it is at. Not a company's (it has
    none yet). Gone once the chat is in, cancelled, or ten minutes old. The password is never kept: it is the
    last thing asked, and checked (or used) the moment it comes, then its message is deleted from the chat."""
    __tablename__ = "messenger_sign_ins"
    __table_args__ = (Index("uq_messenger_sign_ins_chat", "platform", "chat_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))                 # login | signup
    step: Mapped[str] = mapped_column(String(16))                    # username | email | password | code
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    # the signed five-minute two-factor challenge the password step gave (app/core/two_factor.py)
    challenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessengerOutbox(Base):
    """A message for a chat that no update is being answered for: the support desk's reply (DA9). A bot on a
    webhook sends it at once; a relay fetches it (``/bots/<platform>/relay/outbox``) — the server can't reach
    Telegram itself."""
    __tablename__ = "messenger_outbox"
    __table_args__ = (Index("ix_messenger_outbox_waiting", "platform", "sent_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(16))
    chat_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
