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
from app.db.tenant import TenantMixin


class AuditLog(Base, TenantMixin):
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
    # The acting user's RBAC role at the time of the action (migration 018).
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON string of changed fields or context
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ── AI-accountant additions (migration 005) ──
    # 'manual' for human writes via the regular UI, 'ai-assistant' for writes
    # initiated by the AI accountant chat. Filterable to spot AI activity.
    actor_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual", index=True
    )
    # Chat session that produced this write (FK is logical only; the audit log is
    # append-only and must outlive its session row).
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Which AI tool produced this write (proposeCreateTransaction, etc.).
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The idempotency token of the proposal that confirmed this write.
    confirmation_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # The original user message that produced the proposal (free-text).
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TransactionVersion(Base, TenantMixin):
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


class IntegrityCheck(Base, TenantMixin):
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


# --- Append-only guard (ORM level) -----------------------------------------
# The database has a trigger for the same rule (migration 036); this catches
# it earlier, in every environment including SQLite, so no code path can
# quietly rewrite history. Bulk core DELETEs (test teardown) are not ORM
# instance operations and are left to the database trigger.
from sqlalchemy import event as _event  # noqa: E402
from sqlalchemy.orm import Session as _Session  # noqa: E402


class AuditLogImmutableError(RuntimeError):
    pass


import contextlib as _contextlib  # noqa: E402
import contextvars as _contextvars  # noqa: E402

_mutation_allowed: _contextvars.ContextVar[bool] = _contextvars.ContextVar("audit_mutation_allowed", default=False)


@_contextlib.contextmanager
def allow_audit_log_mutation():
    """Test-only escape hatch (e.g. to age a row past the undo window). Never
    used by application code; the database trigger still applies in
    production."""
    token = _mutation_allowed.set(True)
    try:
        yield
    finally:
        _mutation_allowed.reset(token)


@_event.listens_for(_Session, "before_flush")
def _audit_logs_are_append_only(session, flush_context, instances):
    if _mutation_allowed.get():
        return
    for obj in session.deleted:
        if isinstance(obj, AuditLog):
            raise AuditLogImmutableError("audit_logs is append-only: rows cannot be deleted")
    for obj in session.dirty:
        if isinstance(obj, AuditLog) and session.is_modified(obj, include_collections=False):
            raise AuditLogImmutableError("audit_logs is append-only: rows cannot be modified")
