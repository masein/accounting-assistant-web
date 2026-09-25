from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class Invoice(Base, TenantMixin):
    """Simple invoice record for receivable/payable tracking."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # sales | purchase
    status: Mapped[str] = mapped_column(String(16), index=True, default="draft")  # draft | issued | paid | canceled
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True)
    # The recognition journal entry posted when the invoice is issued
    # (DR AR / CR revenue for sales; DR expense / CR AP for purchases).
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True
    )
    # The recurring template that raised this invoice (roadmap §4.2), if any.
    recurring_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recurring_invoices.id", ondelete="SET NULL", use_alter=True,
                                       name="fk_invoices_recurring_invoice"),
        nullable=True, index=True,
    )
    # سامانه مودیان (roadmap §3.1). The serial is allocated once, on the first
    # export, and never reused; the 22-char tax number is derived from it,
    # the company's memory id and the issue date. Status: exported (handed to
    # the provider) | confirmed | rejected; NULL = not sent yet.
    moadian_serial: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    moadian_taxid: Mapped[str | None] = mapped_column(String(22), nullable=True)
    moadian_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    moadian_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    moadian_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    moadian_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A planned date to pay a bill. Informational only — moves no money.
    scheduled_payment_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )
