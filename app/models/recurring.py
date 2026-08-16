from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class RecurringRule(Base, TenantMixin):
    """A recurring payment/receipt that MATERIALIZES into real journal entries.

    When ``auto_post`` is on and the rule carries a bank account + counter
    account, ``recurring_service.materialize_due_rules`` posts a balanced
    transaction each time ``next_run_date`` comes due (payment: DR counter /
    CR bank; receipt reversed) and advances the schedule. Without accounts it
    behaves like the old reminder-only rule (surfaces in notifications and
    the cash forecast, posts nothing)."""

    __tablename__ = "recurring_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    direction: Mapped[str] = mapped_column(String(16), default="payment")  # payment | receipt
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")  # daily | weekly | monthly | quarterly | yearly
    amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    next_run_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True)
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The GL cash account the money moves through (a bank entity's code, e.g.
    # 1111) and the expense/income account on the other leg. Both are needed
    # for auto-posting.
    bank_account_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    counter_account_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    auto_post: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference_prefix: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
