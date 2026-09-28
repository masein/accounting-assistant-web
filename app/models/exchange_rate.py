"""Exchange rates table: historical rates used to convert between currencies.

A rate means: 1 unit of `from_currency` equals `rate` units of `to_currency`
on the given effective_date. The most recent rate on or before a query date
is used. Rates are stored as Float (double precision).

Rows with no company are shared by every company: the daily feeds (roadmap
§4.6) and rates entered before 2026-09-28. A company's own rows are visible
to it alone and, for any pair it has priced itself, replace the shared ones —
so one company can no longer change the rates another company reports in.
Not a ``TenantMixin`` model on purpose: a company reads its own rows *and*
the shared ones (see ``fx_service._latest_rate``).

Codes are up to 16 characters so a holding unit (GOLDG, GOLDC, or a longer
one such as GOLD_GRAM) can carry a price like any currency.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        # One rate per pair per day for each company, and one shared. A plain
        # UNIQUE (company_id, ...) would let shared rows (NULL) repeat.
        Index("uq_exchange_rates_shared", "from_currency", "to_currency", "effective_date", unique=True,
              postgresql_where=text("company_id IS NULL"), sqlite_where=text("company_id IS NULL")),
        Index("uq_exchange_rates_company", "company_id", "from_currency", "to_currency", "effective_date",
              unique=True, postgresql_where=text("company_id IS NOT NULL"),
              sqlite_where=text("company_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE", name="fk_exchange_rates_company"),
        nullable=True, index=True)
    from_currency: Mapped[str] = mapped_column(String(16), index=True)
    to_currency: Mapped[str] = mapped_column(String(16), index=True)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
