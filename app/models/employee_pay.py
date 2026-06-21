"""Employee pay profile — the standing payroll setup for one employee Entity.

Employees themselves live in ``entities`` (type="employee"); this table holds
how they're paid (salaried vs hourly, rate, tax/withholding rates, pre-tax
pension %, currency). One profile per employee entity.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmployeePayProfile(Base):
    __tablename__ = "employee_pay_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"),
        unique=True, index=True,
    )
    pay_type: Mapped[str] = mapped_column(String(16), default="salaried", index=True)  # salaried | hourly

    # Whole currency units. base_salary is per pay period for salaried staff;
    # hourly_rate is per hour for hourly staff.
    base_salary: Mapped[int] = mapped_column(default=0)
    hourly_rate: Mapped[int] = mapped_column(default=0)
    # Standard hours in a pay period — the overtime threshold for hourly staff
    # and the proration baseline for a mid-period salary change.
    standard_hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    overtime_multiplier: Mapped[float] = mapped_column(Numeric(5, 2), default=1.5)

    # Rates as fractions: 0.20 == 20%.
    income_tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    social_security_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)
    pension_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0)  # pre-tax deduction

    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    # Client-billing rate per hour (major currency units) — SEPARATE from the
    # payroll cost above. Used by time-based billing; null = not billable by default.
    billable_rate: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
