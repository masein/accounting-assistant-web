from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecurringRule(Base):
    """Recurring reminder/rule for repeated payments or receipts."""

    __tablename__ = "recurring_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    direction: Mapped[str] = mapped_column(String(16), default="payment")  # payment | receipt
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")  # monthly | yearly
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    next_run_date: Mapped[date] = mapped_column(Date, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True)
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reference_prefix: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
