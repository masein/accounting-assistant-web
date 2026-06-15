"""Purchase orders — a commitment to buy, NOT a ledger posting.

A PO records what was ordered from a supplier (``Entity`` type="supplier") and
at what price. It never hits the books; only the resulting purchase invoice
(bill) recognition and payments post. Goods receipts update ``received_qty``
per line so a 3-way match (PO ↔ receipts ↔ bill) can flag discrepancies before
a bill is approved.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(128), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True
    )
    order_date: Mapped[date] = mapped_column(Date, index=True)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # draft | issued | partially_received | received | closed | cancelled
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a bill is matched cleanly against this PO (informational link).
    matched_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        "PurchaseOrderLine", back_populates="order", cascade="all, delete-orphan",
        order_by="PurchaseOrderLine.created_at",
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(String(256))
    ordered_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    received_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    unit_price: Mapped[int] = mapped_column(BigInteger, default=0)  # whole currency units
    line_total: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["PurchaseOrder"] = relationship("PurchaseOrder", back_populates="lines")
