"""Mileage claim — an employee reimbursement for distance travelled.

amount = round(distance · rate). Below the company approval threshold the claim
posts immediately (DR mileage_expense / CR expenses_payable) and is "approved";
above it the claim is "pending_approval" and posts only when an approver
approves it. Reimbursing the employee (DR expenses_payable / CR bank) is a
separate, explicitly confirmed step — money never moves automatically.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MileageClaim(Base):
    __tablename__ = "mileage_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True, index=True
    )
    employee_name: Mapped[str] = mapped_column(String(256))
    claim_date: Mapped[date] = mapped_column(Date, index=True)
    distance: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    unit: Mapped[str] = mapped_column(String(8), default="mile")  # mile | km
    rate: Mapped[float] = mapped_column(Numeric(12, 4), default=0)  # currency units per unit distance
    amount: Mapped[int] = mapped_column(BigInteger, default=0)  # whole currency units
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pending_approval | approved | rejected | reimbursed
    status: Mapped[str] = mapped_column(String(24), default="approved", index=True)

    # Posting on approval; reimbursement on pay.
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    reimbursement_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )

    # Audit trail of the approval decision.
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
