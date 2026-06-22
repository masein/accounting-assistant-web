"""Tax rates with effective dates — the history behind the VAT engine.

A rate is keyed by a ``code`` (e.g. ``UK_VAT_STANDARD``) and carries an
``effective_from`` / ``effective_to`` window, so a rate that changed mid-year
applies the correct percentage to the correct period. ``tax_rate_for`` (in
app/services/tax_rate_service.py) picks the row in effect on a given date.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class TaxRate(Base, TenantMixin):
    __tablename__ = "tax_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), index=True)          # e.g. UK_VAT_STANDARD
    jurisdiction: Mapped[str] = mapped_column(String(32), index=True)  # e.g. UK, IR
    description: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rate: Mapped[float] = mapped_column(Numeric(7, 4), default=0)      # percent, e.g. 20 for 20%
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # null = still in force

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
