"""Sales quotes (پیش‌فاکتور).

A quote is an offer, not a receivable: it never touches the ledger. When the
customer accepts it, ``POST /quotes/{id}/convert`` creates a normal sales
invoice from the same lines (same tax resolution, same recognition entry) and
links the two, so the invoice records where it came from and the quote can't
be converted twice.

Kept in its own tables rather than as an invoice status so that no report,
AR ageing, dashboard or notification that reads ``invoices`` can ever count
an offer as money owed.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin

QUOTE_STATUSES = ("draft", "sent", "accepted", "declined", "converted")


class Quote(Base, TenantMixin):
    __tablename__ = "quotes"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_quotes_company_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(128), index=True)
    # draft | sent | accepted | declined | converted. "expired" is derived on
    # read (valid_until has passed while still draft/sent), never stored.
    status: Mapped[str] = mapped_column(String(16), index=True, default="draft")
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    valid_until: Mapped[date] = mapped_column(Date, index=True)
    # Tax-inclusive grand total, like ``invoices.amount``.
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True
    )
    converted_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["QuoteItem"]] = relationship(
        "QuoteItem", back_populates="quote", cascade="all, delete-orphan",
        order_by="QuoteItem.position",
    )


class QuoteItem(Base, TenantMixin):
    """Same shape as ``InvoiceItem`` so a conversion copies lines verbatim."""

    __tablename__ = "quote_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotes.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    product_name: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=1)
    unit_price: Mapped[int] = mapped_column(BigInteger, default=0)
    unit_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    line_total: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(7, 4), default=0, server_default="0")
    taxable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    tax_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_treatment: Mapped[str] = mapped_column(String(24), default="standard", server_default="standard")
    # سامانه مودیان: 13-digit goods/service id (شناسه کالا/خدمت) and the
    # tax organisation's measurement-unit code. Blank → the company default.
    sstid: Mapped[str | None] = mapped_column(String(13), nullable=True)
    mu: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    quote: Mapped["Quote"] = relationship("Quote", back_populates="items")
