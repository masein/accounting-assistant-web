from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin
from app.core.money import MoneyType


class InventoryMovementType(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"
    ADJUSTMENT = "ADJUSTMENT"


class InventoryItem(Base, TenantMixin):
    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    unit: Mapped[str] = mapped_column(String(32), default="unit")
    is_active: Mapped[bool] = mapped_column(default=True)
    list_price: Mapped[int] = mapped_column(MoneyType(), default=0, doc="Current list/selling price")
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Reorder point (roadmap §4.4): at or below it the item shows as low stock.
    reorder_level: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    reorder_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    movements: Mapped[list["InventoryMovement"]] = relationship(
        "InventoryMovement", back_populates="item", cascade="all, delete-orphan"
    )


class InventoryMovement(Base, TenantMixin):
    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    movement_date: Mapped[date] = mapped_column(Date, index=True)
    movement_type: Mapped[InventoryMovementType] = mapped_column(
        Enum(InventoryMovementType, name="inventory_movement_type"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    unit_cost: Mapped[int] = mapped_column(MoneyType(), default=0)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True, index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True
    )
    # What the invoice that made this movement posted to stock, in the base
    # currency (docs/PLAN_INVENTORY_GL.md): the sync posts any difference from
    # the costing engine's value. None for a movement entered by hand.
    ledger_value: Mapped[int | None] = mapped_column(MoneyType(), nullable=True)
    # The credit note goods came back on (a customer's return, or ours to a
    # supplier): tells a return from the invoice's own movement (phase G3).
    credit_note_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_notes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Set in Python (microseconds): SQLite's CURRENT_TIMESTAMP has one-second
    # resolution, so same-second movements sorted randomly by id.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc)
    )

    item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="movements")


class InventoryBomLine(Base, TenantMixin):
    """One component of a finished item's bill of materials: ``quantity`` of
    ``component`` goes into one unit of ``product`` (roadmap §4.4)."""

    __tablename__ = "inventory_bom_lines"
    __table_args__ = (UniqueConstraint("product_id", "component_id", name="uq_inventory_bom_component"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryPosting(Base, TenantMixin):
    """What the stock sync has posted for one month and kind
    (docs/PLAN_INVENTORY_GL.md): ``cogs``, the cost of what was sold, or
    ``adjust``, movements entered by hand and catch-ups. ``amount`` is signed:
    what stock went down by for ``cogs``, what it went up by for ``adjust``.
    A month's total is the sum of its rows; each re-run adds the difference."""

    __tablename__ = "inventory_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    month: Mapped[str] = mapped_column(String(7), index=True)          # "2026-10", Gregorian
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(MoneyType())
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
