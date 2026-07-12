"""Parked inbound time entries (/api/v1 pushes that couldn't be auto-mapped).

When an external tool pushes a worklog whose worker (or data) can't be matched,
the push is PARKED here — never silently dropped, never auto-creating people.
The employer resolves it in-app (assign the right employee), which creates a
normal TimeEntry that flows into payroll and billing.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class PendingTimeEntry(Base, TenantMixin):
    __tablename__ = "pending_time_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Idempotency key within the company: one parked row per (source, external_id).
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    # What the pusher sent, verbatim (matched later by the employer).
    worker_ref: Mapped[str] = mapped_column(String(256))
    client_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    project_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_type: Mapped[str] = mapped_column(String(16), default="work")
    billable: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | resolved | rejected
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    resolved_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
