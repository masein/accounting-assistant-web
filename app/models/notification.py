"""Stored notifications + user reminders.

``notifications`` is the app's first persisted alert feed (everything before
was computed per-request): rows are upserted by ``notification_service`` from
the live data (invoice due dates, payroll paydays, pending approvals,
recurring schedules, reminders), deduplicated by ``dedupe_key`` so a
recomputation updates rather than spams. ``user_id`` set → personal
notification; NULL → role-based visibility decided by ``kind`` at read time.

``reminders`` are user-created: a date, an optional repeat interval, and how
many days before the date the alert should start showing.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class Notification(Base, TenantMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("company_id", "dedupe_key", name="uq_notifications_dedupe"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)   # invoice_due | invoice_overdue | payroll | approvals | petty_cash | recurring | reminder
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | warning | high
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text, default="")
    link_page: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SPA page to open
    dedupe_key: Mapped[str] = mapped_column(String(160))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Reminder(Base, TenantMixin):
    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # the owner
    title: Mapped[str] = mapped_column(String(256))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    repeat: Mapped[str] = mapped_column(String(16), default="none")  # none | daily | weekly | monthly | yearly
    days_before: Mapped[int] = mapped_column(Integer, default=3)     # alert lead time
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
