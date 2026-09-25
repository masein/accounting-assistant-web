"""Recurring sales invoices (roadmap §4.2): a template that issues a real
invoice every period and, optionally, e-mails it to the customer.

The schedule is anchored on ``start_date``: occurrence n is start + n
periods (never "previous date + one month", which drifts after the 31st),
and for Iranian companies a monthly schedule can follow Jalali months, so an
invoice raised on 1 Mehr is raised on 1 Aban, 1 Azar …
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class RecurringInvoice(Base, TenantMixin):
    __tablename__ = "recurring_invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Single-amount template (used when ``items`` is empty).
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    # JSON list of invoice lines (the InvoiceItemCreate shape).
    items: Mapped[str] = mapped_column(Text, default="[]")
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")   # weekly | monthly | quarterly | yearly
    calendar: Mapped[str] = mapped_column(String(16), default="gregorian")  # gregorian | jalali
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_occurrences: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_date: Mapped[date] = mapped_column(Date, index=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    terms_days: Mapped[int] = mapped_column(Integer, default=30)
    issue_status: Mapped[str] = mapped_column(String(16), default="issued")  # issued | draft
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | paused | ended
    last_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
