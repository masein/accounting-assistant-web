"""
Immutable audit log and transaction versioning models.
Every write operation (create, update, delete) is logged.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """Immutable append-only audit trail for all state changes."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    # create, update, delete, approve, reject, reconcile, login, export
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    # transaction, bank_statement, entity, account, invoice, etc.
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON string of changed fields or context
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TransactionVersion(Base):
    """Snapshot of a transaction at a point in time for rollback/comparison."""

    __tablename__ = "transaction_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    snapshot: Mapped[str] = mapped_column(Text)
    # Full JSON snapshot of the transaction + lines + entity_links at this version
    action: Mapped[str] = mapped_column(String(32))
    # create, update, delete
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrityCheck(Base):
    """Periodic accounting integrity check results."""

    __tablename__ = "integrity_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_type: Mapped[str] = mapped_column(String(64), index=True)
    # equation_balance, duplicate_detection, anomaly_scan, negative_balance, backdated
    status: Mapped[str] = mapped_column(String(32), index=True)
    # pass, warning, fail
    score: Mapped[int] = mapped_column(Integer, default=100)
    # 0-100 integrity score
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
