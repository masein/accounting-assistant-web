from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class Adjustment(Base, TenantMixin):
    """A period-end adjustment: an accrual, a prepayment with an amortization
    schedule, or a fixed-asset straight-line depreciation schedule.

    * accrual — posts DR expense / CR accrued-liability (or the symmetric
      accrued-income) at creation; optionally auto-reverses on the first day
      of the next period.
    * prepayment — posts DR prepaid-asset / CR bank at creation, then releases
      to expense evenly across ``periods`` via the /release endpoint.
    * depreciation — no cash entry; releases DR depreciation-expense /
      CR accumulated-depreciation each period (straight-line).
    """

    __tablename__ = "adjustments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # accrual | prepayment | depreciation
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")

    # accrual: the accrual amount. prepayment: the total prepaid amount that
    # amortizes. depreciation: the asset cost.
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    residual: Mapped[int] = mapped_column(BigInteger, default=0)  # depreciation salvage value
    periods: Mapped[int] = mapped_column(Integer, default=1)       # release count (prepayment/depreciation)
    period_months: Mapped[int] = mapped_column(Integer, default=1)
    start_date: Mapped[date] = mapped_column(Date, index=True)

    # accrual flavour: 'expense' (DR expense / CR accrued liability) or
    # 'income' (DR accrued income / CR revenue).
    direction: Mapped[str] = mapped_column(String(8), default="expense")
    auto_reverse: Mapped[bool] = mapped_column(Boolean, default=False)

    periods_posted: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | complete

    # The initial journal entry (prepayment outlay / accrual). Releases get
    # their own transactions referencing the adjustment number.
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    reversal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
