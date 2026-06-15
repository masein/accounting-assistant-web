"""Goods receipts — a record of quantity received against a purchase-order line.

Receipts track quantities only; they do NOT post to the ledger (inventory /
GRNI accounting is out of scope for this module). Each receipt event bumps the
denormalized ``received_qty`` on its PO line, which the 3-way match reads.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    receipt_date: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["GoodsReceiptLine"]] = relationship(
        "GoodsReceiptLine", back_populates="receipt", cascade="all, delete-orphan",
    )


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True
    )
    po_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_order_lines.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0)

    receipt: Mapped["GoodsReceipt"] = relationship("GoodsReceipt", back_populates="lines")
