"""Statutory payroll parameters as data, one row per locale and year.

Iran's 1405 minimum wage, housing/grocery allowances, insurance rates and
ceiling, and the monthly income-tax brackets change every year by decree;
the UK's personal allowance, tax bands and NI thresholds change every April.
Keeping them in a table (rather than in ``payroll_service.py``) lets the
super-admin update next year's figures without a deploy, and lets a pay run
for a past period keep using the figures that were in force then.

Platform-wide: the row has no ``company_id`` — a statutory rate is the same
for every tenant in that locale. Only a super-admin may edit it; payroll
roles may read it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PayrollRuleSet(Base):
    __tablename__ = "payroll_rule_sets"
    __table_args__ = (
        UniqueConstraint("locale", "year", name="uq_payroll_rule_sets_locale_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    locale: Mapped[str] = mapped_column(String(16), index=True)      # ir | uk
    year: Mapped[str] = mapped_column(String(16))                    # "1405" | "2026/27"
    name: Mapped[str] = mapped_column(String(128))
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # JSON document validated by app.services.payroll_rules.RuleParams.
    params: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
