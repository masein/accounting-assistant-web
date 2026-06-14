from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CreditNote(Base):
    """A credit issued against an invoice (reduces AR for sales / AP for
    purchases) or standalone against an entity (e.g. the excess from an
    overpayment, an available credit that can offset future invoices).

    Each credit note posts a balanced reversing journal entry linked via
    ``transaction_id``.
    """

    __tablename__ = "credit_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="sales", index=True)  # sales | purchase
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)  # whole currency units, > 0
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'reduction' = reduces the invoice's open balance; 'credit' = available
    # entity credit (e.g. overpayment) that can offset future invoices.
    note_type: Mapped[str] = mapped_column(String(16), default="reduction", index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
