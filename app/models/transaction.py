from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Index, String, Text, event, exists, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, with_loader_criteria

from app.db.base import Base
from app.db.tenant import TenantMixin


class Transaction(Base, TenantMixin):
    """Journal entry header. Each transaction has one or more lines (debit/credit)."""

    __tablename__ = "transactions"
    # Every report reads live journals of one company in one currency over a
    # date range (roadmap §2.6): one index serves them all, and replaced or
    # undone journals are not in it.
    __table_args__ = (
        Index("ix_transactions_live", "company_id", "currency", "date",
              postgresql_where=text("deleted_at IS NULL"), sqlite_where=text("deleted_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, index=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="IRR", server_default="IRR", index=True)
    # Base-currency values (roadmap §4.6, option 2 — as Xero and QuickBooks
    # keep them): 1 unit of ``currency`` = ``fx_rate`` units of the company's
    # base currency, fixed when the entry is posted (1 for a base-currency
    # entry). NULL = no rate was known yet: the lines' base amounts are NULL
    # too until one is added (app/services/fx_base.py fills them).
    fx_rate: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # Entries whose base amounts the system sets itself and no rate may
    # recompute: "revaluation" (period-end, base-only lines),
    # "legacy_revaluation" (a pre-2026-09-28 mirror revaluation, neutralised in
    # base by migration 053). NULL for every ordinary entry.
    fx_role: Mapped[str | None] = mapped_column(String(24), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    lines: Mapped[list["TransactionLine"]] = relationship(
        "TransactionLine", back_populates="transaction", cascade="all, delete-orphan"
    )
    entity_links: Mapped[list["TransactionEntity"]] = relationship(
        "TransactionEntity", back_populates="transaction", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["TransactionAttachment"]] = relationship(
        "TransactionAttachment", back_populates="transaction"
    )


class TransactionLine(Base, TenantMixin):
    """Single debit or credit line of a journal entry."""

    __tablename__ = "transaction_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), index=True)

    debit: Mapped[int] = mapped_column(BigInteger, default=0)
    credit: Mapped[int] = mapped_column(BigInteger, default=0)
    # The same amounts in the company's base currency at the entry's fx_rate,
    # rounded half-up with the entry kept balanced. NULL while no rate is
    # known. A line with no foreign amount and a base amount is base-only (a
    # revaluation or a settlement difference).
    base_debit: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    base_credit: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    line_description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    transaction: Mapped["Transaction"] = relationship("Transaction", back_populates="lines")
    account: Mapped["Account"] = relationship("Account")


class TransactionAttachment(Base, TenantMixin):
    """Uploaded receipt/invoice file that can be linked to a transaction."""

    __tablename__ = "transaction_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), index=True, nullable=True
    )
    file_name: Mapped[str] = mapped_column(String(256))
    file_path: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transaction: Mapped["Transaction | None"] = relationship("Transaction", back_populates="attachments")


# --- Soft delete: an undone or replaced journal is invisible (roadmap §1.6) ---
# ``deleted_at`` marks a journal as undone (chat undo, a correction, a
# re-import). Every SELECT leaves out deleted journals, and journal lines whose
# journal is deleted, unless the caller asks with ``include_deleted_transactions()``
# (or the ``include_deleted`` execution option). Refreshing an object already
# in the session and loading a related collection are left alone, so a
# journal just soft-deleted can still be read back and its lines listed.
_include_deleted: ContextVar[bool] = ContextVar("include_deleted_transactions", default=False)
# A plain table alias for the line check: it never auto-correlates with a
# ``transactions`` the outer query already selects from.
_live_txn = Transaction.__table__.alias("live_txn")


@contextmanager
def include_deleted_transactions():
    token = _include_deleted.set(True)
    try:
        yield
    finally:
        _include_deleted.reset(token)


@event.listens_for(Session, "do_orm_execute")
def _hide_deleted_transactions(execute_state) -> None:
    if not execute_state.is_select or execute_state.is_column_load or execute_state.is_relationship_load:
        return
    if _include_deleted.get() or execute_state.execution_options.get("include_deleted"):
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(Transaction, lambda cls: cls.deleted_at.is_(None),
                             include_aliases=True, propagate_to_loaders=False),
        with_loader_criteria(
            TransactionLine,
            lambda cls: exists().where(_live_txn.c.id == cls.transaction_id, _live_txn.c.deleted_at.is_(None)),
            include_aliases=True, propagate_to_loaders=False,
        ),
    )
