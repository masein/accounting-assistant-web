"""State that must be shared by every worker process (roadmap §2.2).

Before this, login/chat rate limits, the dashboard and insights caches and
Excel upload tokens lived in process memory: with two uvicorn workers a
brute-force got twice the attempts, a dashboard could stay stale on the
worker that didn't see the write, and an Excel preview on one worker could
not be confirmed on the other. The deployment has Postgres and no Redis, so
these live in small tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class RateLimitEvent(Base):
    """One counted attempt. Naive UTC timestamps (same on SQLite and PG)."""

    __tablename__ = "rate_limit_events"
    __table_args__ = (Index("ix_rate_limit_events_bucket_identity_at", "bucket", "identity", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bucket: Mapped[str] = mapped_column(String(32))
    identity: Mapped[str] = mapped_column(String(256))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class BooksVersion(Base):
    """Bumped in the same transaction as any write to the books of a company;
    caches key on it, so every worker sees a change at once."""

    __tablename__ = "books_versions"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)  # company id or "platform"
    version: Mapped[int] = mapped_column(BigInteger, default=0)


class UploadToken(Base, TenantMixin):
    """An uploaded file waiting for a second step (preview → confirm)."""

    __tablename__ = "upload_tokens"
    __table_args__ = (UniqueConstraint("company_id", "kind", "token", name="uq_upload_tokens_company_kind_token"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    token: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str] = mapped_column(String(1024))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
