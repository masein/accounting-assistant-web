from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class InvoiceItem(Base, TenantMixin):
    """Line items for sales/purchase invoices."""

    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    product_name: Mapped[str] = mapped_column(String(256), index=True)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=1)
    unit_price: Mapped[int] = mapped_column(BigInteger, default=0)
    unit_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    line_total: Mapped[int] = mapped_column(BigInteger, default=0)
    # VAT / sales-tax: percentage rate (e.g. 20 for 20%); taxable=False makes
    # the line exempt (contributes to subtotal but not to tax).
    tax_rate: Mapped[float] = mapped_column(Numeric(7, 4), default=0, server_default="0")
    taxable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Effective-dated tax code (e.g. UK_VAT_STANDARD) the rate was derived from,
    # and the treatment (standard | zero_rated | exempt | reverse_charge).
    tax_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_treatment: Mapped[str] = mapped_column(String(24), default="standard", server_default="standard")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")
    inventory_item: Mapped["InventoryItem | None"] = relationship("InventoryItem")
