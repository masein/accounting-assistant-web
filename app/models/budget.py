from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin
from app.core.money import MoneyType


class BudgetLimit(Base, TenantMixin):
    """Monthly spending cap per expense category/account."""

    __tablename__ = "budget_limits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(256), index=True)
    limit_amount: Mapped[int] = mapped_column(MoneyType(), default=0)
    # empty: the books' own currency; else the one it counts, in its own amounts (docs/PLAN_CURRENCIES.md)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
