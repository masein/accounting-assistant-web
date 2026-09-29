"""Statements of account and payslips that were e-mailed (roadmap §4.9).

One row per attempt, sent or not, like ``invoice_emails``: a statement to a
client or supplier, or one employee's payslip from a pay run. ``status`` is
``sent`` or ``failed`` (the reason in ``error``); an employee with no address
isn't attempted and isn't logged — the send reports them instead.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin


class DocumentEmail(Base, TenantMixin):
    __tablename__ = "document_emails"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), index=True)                  # statement | payslip
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True)
    pay_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pay_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    to_address: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), index=True)                # sent | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
