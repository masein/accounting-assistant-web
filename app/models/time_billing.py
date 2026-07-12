"""Time-based billing: projects, billable-rate overrides, and time entries.

A worker (an ``Entity`` of type employee OR supplier/contractor) logs hours
against a client (and optional project). Unbilled time is later turned into a
normal sales invoice at the worker's billable rate. Rate precedence is
project-specific → client-specific → the worker's default rate
(``EmployeePayProfile.billable_rate`` or a default override).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class Project(Base, TenantMixin):
    """A project / matter belonging to one client (Entity type=client)."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(256), index=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | closed
    default_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingRateOverride(Base, TenantMixin):
    """A billable rate for a worker, optionally scoped to a client or project.
    Both client_id and project_id null = the worker's default override."""

    __tablename__ = "billing_rate_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rate: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # major currency units
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimeEntry(Base, TenantMixin):
    """One logged block of work. ``employee_id`` is the worker (employee or
    supplier). ``client_id`` is optional — payroll-only entries (leave, internal
    work) have no client and are never billable.

    One entry has TWO independent settlement tracks:
      * client billing — ``billable`` + ``status``/``invoice_id``
      * employee payroll — ``payable`` + ``payroll_status``/``payroll_run_id``
    so the same hour can be invoiced to the client and paid to the employee on
    separate cycles, each counted exactly once on each side.
    """

    __tablename__ = "time_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, index=True)
    hours: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    billable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # unbilled | invoiced | written_off
    status: Mapped[str] = mapped_column(String(16), default="unbilled", index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # --- payroll dimension (migration 020) ---
    # work | leave | travel | unpaid. Fixed MVP behaviour: work/travel = worked
    # + payable (overtime-eligible); leave = payable, not worked; unpaid = neither.
    entry_type: Mapped[str] = mapped_column(
        String(16), default="work", server_default="work", index=True
    )
    # Counts toward EMPLOYEE PAY — independent of ``billable`` (client invoice).
    payable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    # unpaid | paid — payroll settlement, independent of billing ``status``.
    payroll_status: Mapped[str] = mapped_column(
        String(16), default="unpaid", server_default="unpaid", index=True
    )
    payroll_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pay_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # --- integration source (Part B, /api/v1 pushes) ---
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # The rate actually billed, stamped when invoiced (major currency units).
    rate_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
