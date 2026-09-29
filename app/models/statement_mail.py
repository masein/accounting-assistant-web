"""Messages read from a company's statements mailbox (roadmap 2026-09 §4.1).

One row per message from an allowed bank sender, whatever came of it:
``imported`` (the statements it filed are in ``statement_ids``),
``duplicate``, ``needs_mapping``, ``needs_password``, ``failed``,
``no_attachment`` or ``too_large``. ``message_key`` (the SHA-256 of the Message-ID) is what makes
a check idempotent: the mailbox itself is opened read-only and never changed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class StatementMailMessage(Base, TenantMixin):
    __tablename__ = "statement_mail_messages"
    __table_args__ = (Index("uq_statement_mail_company_key", "company_id", "message_key", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_key: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    statement_ids: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON list
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # a Python default too: SQLite's now() has whole seconds, and the log is read newest first
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 default=lambda: datetime.now(timezone.utc))
