"""Pay run — one payroll batch for a pay period, with a computed line per
employee. A run is calculated on create (``draft``), posted to the ledger
(``posted``), and finally settled when staff are paid (``paid``). The posting
and the payment are separate confirm-gated steps; money never moves on its own.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class PayRun(Base, TenantMixin):
    __tablename__ = "pay_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    pay_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft | posted | paid

    # Batch totals (whole currency units), summed from the lines.
    total_gross: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tax: Mapped[int] = mapped_column(BigInteger, default=0)
    total_social: Mapped[int] = mapped_column(BigInteger, default=0)
    total_deductions: Mapped[int] = mapped_column(BigInteger, default=0)
    total_net: Mapped[int] = mapped_column(BigInteger, default=0)

    # Ledger links: the gross→net accrual on post, the bank settlement on pay.
    post_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    pay_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["PayRunLine"]] = relationship(
        "PayRunLine", back_populates="run", cascade="all, delete-orphan",
        order_by="PayRunLine.created_at",
    )


class PayRunLine(Base, TenantMixin):
    """One employee's computed pay within a run. Stores the full breakdown so a
    payslip and the year-end summary can be reproduced exactly from the row."""

    __tablename__ = "pay_run_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pay_runs.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    employee_name: Mapped[str] = mapped_column(String(256))

    # Inputs captured at calculation time.
    hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    overtime_hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    proration: Mapped[float] = mapped_column(Numeric(6, 4), default=1)  # 1.0 = full period

    # Breakdown (whole currency units). taxable_base = gross − pre_tax_deductions.
    gross: Mapped[int] = mapped_column(BigInteger, default=0)
    pre_tax_deductions: Mapped[int] = mapped_column(BigInteger, default=0)
    taxable_base: Mapped[int] = mapped_column(BigInteger, default=0)
    income_tax: Mapped[int] = mapped_column(BigInteger, default=0)
    social_security: Mapped[int] = mapped_column(BigInteger, default=0)
    net_pay: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped["PayRun"] = relationship("PayRun", back_populates="lines")
