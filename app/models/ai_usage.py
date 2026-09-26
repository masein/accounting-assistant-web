"""One row per AI provider call (roadmap 2026-09 §2.5).

Written by ``app/services/ai_usage.metered_llm`` at the five places a request
leaves for a provider, from the request's own context (company, user, request
id). Not a TenantMixin table: a call can have no company (a super-admin, a
background job), and the Default-company fold must never re-home those.
Reads filter by company explicitly.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_company_created", "company_id", "created_at"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE", name="fk_ai_usage_events_company"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # provider profile (metis, lmstudio, custom, anthropic, gemini) and model id
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(24))          # chat | suggest | categorize | ocr
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)   # whole prompt, cached part included
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0)  # prompt tokens read from a cache
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    # the provider sent no usage block: counted from the text length instead
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    # estimated cost in millionths of a US dollar; NULL = no price for the model
    cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome: Mapped[str] = mapped_column(String(8), default="ok")      # ok | error
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
