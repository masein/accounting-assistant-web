"""Per-company API keys for service-to-service integrations (/api/v1).

The raw key is shown ONCE at creation and stored only as a SHA-256 hash (keys
are high-entropy random tokens, so an indexed unhashed-lookup hash is the right
trade-off — per-row salted KDFs would force a full-table scan per request).
The key resolves to its company; tenant scoping then applies automatically and
a key can never cross companies.

NOT TenantMixin: the key lookup happens in middleware BEFORE any company
context exists (the key is what establishes it) — same pattern as User.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(128), default="integration")
    # SHA-256 hex of the raw key. Unique + indexed → O(1) middleware lookup.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First characters of the raw key ("ak_xxxxxxxx…") for display/identification.
    prefix: Mapped[str] = mapped_column(String(16))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
