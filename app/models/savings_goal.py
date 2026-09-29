"""Savings goals for personal books (roadmap §4.12).

A goal is a target on one asset account — a savings deposit, the gold
holdings account, a dollar account: its progress is that account's value
today (the market value where holdings revalue it), so the ledger stays the
only record of what was put aside.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, String, func
from sqlalchemy.sql.expression import false as sa_false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class SavingsGoal(Base, TenantMixin):
    __tablename__ = "savings_goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128))
    account_code: Mapped[str] = mapped_column(String(64), index=True)
    target_amount: Mapped[int] = mapped_column(BigInteger)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
