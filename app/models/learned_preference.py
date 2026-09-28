"""What the books learned from the user's corrections (roadmap 2026-09 §5.4).

A bank narration or a description repeats month after month ("SNAPP TEHRAN",
"TESCO STORES 2231"). When the user corrects the account or the party chosen
for one — on a statement row, by editing an entry, or by telling the
assistant — the normalised narration is kept here with their choice, and the
statement categoriser, ``search_accounts``, ``find_entity`` and the
assistant's prompt use it before anything else. QuickBooks-style "rules",
learned instead of written.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class LearnedPreference(Base, TenantMixin):
    __tablename__ = "learned_preferences"
    __table_args__ = (
        Index("uq_learned_preferences_pattern", "company_id", "pattern", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # normalize_narration() of the text the correction was made on
    pattern: Mapped[str] = mapped_column(String(256))
    # one example of the original wording, for the list the user sees
    label: Mapped[str] = mapped_column(String(256))
    account_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # statement | edit | chat | manual — where the correction came from
    source: Mapped[str] = mapped_column(String(16), default="edit")
    # how often the user made this choice, and how often it was applied since
    times_chosen: Mapped[int] = mapped_column(Integer, default=1)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
