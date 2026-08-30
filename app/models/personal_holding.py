"""What a personal tenant actually holds, in the unit they hold it in.

The ledger records what gold or foreign currency *cost* in rials. In a
high-inflation economy that historical cost stops describing reality within
months, so a personal net-worth figure built from book values alone is
misleading — usually badly understated.

This table stores the missing half: the quantity, in its native unit (grams of
gold, coins, USD). Current value is quantity x today's rate, and the rate rides
the existing ``exchange_rates`` table by treating a unit like GOLD_GRAM as a
pseudo-currency — so manual rate entry, effective dating and history all work
already, which matters where no external price feed is reachable.

Deliberately reporting-only: nothing here posts a journal entry, so the books
stay at cost and the revaluation is presentation. Posting a real revaluation
entry (Dr asset / Cr unrealized gain) is a later step.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class PersonalHolding(Base, TenantMixin):
    """A quantity of a revaluable asset, backed by a chart account."""

    __tablename__ = "personal_holdings"
    __table_args__ = (
        UniqueConstraint("company_id", "account_code", "unit", name="uq_personal_holding_acct_unit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The asset account this backs (e.g. 1130 gold savings, 1140 FX savings).
    account_code: Mapped[str] = mapped_column(String(64), index=True)
    # Unit code, resolved against exchange_rates.from_currency:
    # GOLD_GRAM | GOLD_COIN | USD | EUR | ...
    unit: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
