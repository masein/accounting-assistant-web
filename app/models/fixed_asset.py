"""Fixed-asset register (roadmap 2026-09 §4.3).

One card per asset — cost, residual value, method and life, the three ledger
accounts it posts to — and one row per month of depreciation already posted,
so a run never posts a month twice and the accumulated figure is a sum.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

STRAIGHT_LINE = "straight_line"
DECLINING_BALANCE = "declining_balance"
METHODS = (STRAIGHT_LINE, DECLINING_BALANCE)

ACTIVE = "active"
DISPOSED = "disposed"


class FixedAsset(Base, TenantMixin):
    __tablename__ = "fixed_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(32), index=True)            # FA-0001, per company
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(48), default="other")     # a preset key or "other"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )                                                                       # the supplier
    currency: Mapped[str] = mapped_column(String(8), default="IRR")

    acquired_on: Mapped[date] = mapped_column(Date)
    in_service_on: Mapped[date] = mapped_column(Date)
    # First day of the first month that is depreciated (Iran: the month after
    # the asset came into use — art. 6 of the art. 149 rules).
    depreciation_start: Mapped[date] = mapped_column(Date, index=True)

    cost: Mapped[int] = mapped_column(BigInteger)                           # whole currency units
    residual: Mapped[int] = mapped_column(BigInteger, default=0)
    method: Mapped[str] = mapped_column(String(24), default=STRAIGHT_LINE)
    life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)  # straight-line
    rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)     # declining balance, per year

    # Depreciation from before the register (an asset brought over from
    # another system): accumulated up to ``opening_date``; months before it
    # are never posted.
    opening_accumulated: Mapped[int] = mapped_column(BigInteger, default=0)
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    asset_account_code: Mapped[str] = mapped_column(String(16))
    accumulated_account_code: Mapped[str] = mapped_column(String(16))
    expense_account_code: Mapped[str] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(16), default=ACTIVE, index=True)
    acquisition_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    disposed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    disposal_proceeds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disposal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FixedAssetDepreciation(Base, TenantMixin):
    """One posted month of one asset's depreciation."""

    __tablename__ = "fixed_asset_depreciation"
    __table_args__ = (UniqueConstraint("asset_id", "period_start", name="uq_fixed_asset_depreciation_month"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixed_assets.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date)                        # first day of the month
    amount: Mapped[int] = mapped_column(BigInteger)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
