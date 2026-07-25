"""Migration from another accounting system (chart + تفصیلی + opening balances).

Two tables back the confirm-gated import flow:

- ``migration_batches`` — one row per uploaded file set. The parsed rows live
  in ``payload`` (JSONB) so Confirm never re-reads the upload (durable across
  restarts and workers, unlike the excel-import temp-file store). ``status``
  gives idempotency: confirming an ``applied`` batch returns the original
  result instead of re-writing.
- ``migration_pending_records`` — the "Complete imported records" queue.
  Imported entities arrive with only name + code + balance; each row tracks
  which required fields (address, bank account, …) are still missing, plus
  review flags (e.g. counterparty type inferred ambiguously). Non-blocking:
  the import applies fully regardless.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin

# Portable JSON: JSONB on PostgreSQL, plain JSON on SQLite (tests).
_JSONType = JSON().with_variant(JSONB(), "postgresql")


class MigrationBatch(Base, TenantMixin):
    __tablename__ = "migration_batches"
    __table_args__ = (UniqueConstraint("company_id", "token", name="uq_migration_batch_token"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | applied | cancelled
    source_files: Mapped[list[Any]] = mapped_column(_JSONType, default=list)  # [{filename, kind, rows}]
    payload: Mapped[dict[str, Any]] = mapped_column(_JSONType, default=dict)  # parsed rows per tier
    summary: Mapped[dict[str, Any]] = mapped_column(_JSONType, default=dict)  # preview summary + validation
    result: Mapped[dict[str, Any] | None] = mapped_column(_JSONType, nullable=True)  # apply result
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    opening_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    pending_records: Mapped[list["MigrationPendingRecord"]] = relationship(
        "MigrationPendingRecord", back_populates="batch", cascade="all, delete-orphan"
    )


class MigrationPendingRecord(Base, TenantMixin):
    __tablename__ = "migration_pending_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("migration_batches.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    source_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # كد تفصيلي
    missing_fields: Mapped[list[Any]] = mapped_column(_JSONType, default=list)
    review_flags: Mapped[list[Any]] = mapped_column(_JSONType, default=list)  # e.g. ["type_ambiguous"]
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | resolved | dismissed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[MigrationBatch] = relationship("MigrationBatch", back_populates="pending_records")
