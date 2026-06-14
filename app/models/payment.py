from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Payment(Base):
    """A payment applied to an invoice — a sales receipt (money in) or a bill
    payment (money out). Each payment posts its own balanced journal entry
    against trade debtors / creditors and bank, linked via ``transaction_id``.
    """

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)  # whole currency units, > 0
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    method: Mapped[str] = mapped_column(String(16), default="bank")  # cash | bank | transfer
    direction: Mapped[str] = mapped_column(String(8), index=True)  # in (receipt) | out (bill payment)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
