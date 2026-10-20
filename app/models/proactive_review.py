"""A review the app makes on its own: a week's or a month's, or a nudge (scenarios AL1–AL16).

``facts`` are the figures worked out from the books when it was made; each
reader sees them in their own language (app/services/proactive_reviews.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

_JSONType = JSON().with_variant(JSONB(), "postgresql")


class ProactiveReview(Base, TenantMixin):
    __tablename__ = "proactive_reviews"
    __table_args__ = (UniqueConstraint("company_id", "kind", "period_key", name="uq_proactive_reviews_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))            # weekly | monthly | nudge
    period_key: Mapped[str] = mapped_column(String(32))      # 1405-08-03 (a week's first day), 1405-07, a nudge's day
    facts: Mapped[dict] = mapped_column(_JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ProactiveReviewSeen(Base):
    """Who has been shown a review: in a conversation, once."""
    __tablename__ = "proactive_review_seen"

    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("proactive_reviews.id", ondelete="CASCADE"),
                                                 primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                               primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
