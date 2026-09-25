"""Every invoice e-mail and reminder that was attempted, sent or not.

One row per attempt: the manual "Email invoice" button and each automatic
overdue reminder. ``stage`` is the reminder's day offset from the due date
(e.g. 7 = a week overdue, -3 = three days before due) and makes the daily
reminder job idempotent: an invoice never gets the same (or an earlier)
reminder twice. Failed attempts are kept with the reason so the owner can see
why a customer was not reminded.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class InvoiceEmail(Base, TenantMixin):
    __tablename__ = "invoice_emails"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)          # invoice | reminder
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)  # reminder day offset
    to_address: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), index=True)        # sent | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)  # username or "scheduler"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
