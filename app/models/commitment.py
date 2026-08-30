"""Dated money: loan installments (اقساط) and post-dated cheques (چک).

Both are the same object in practice — an amount, a date it falls due, a
counterparty, and a status — so they share one table. A cheque is a single row;
an installment plan is N rows sharing a ``plan_id``, which makes the questions
users actually ask cheap to answer: what's left to pay (sum of pending rows in
the plan) and what's next (earliest pending due date).

This is not covered by RecurringRule, which repeats indefinitely and has no
notion of an outstanding balance or of individual installments you tick off.

Post-dated cheques matter well beyond personal finance here: in Iran they are a
primary B2B payment instrument, so the SME side gets this too.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

# kind
INSTALLMENT = "installment"
CHEQUE = "cheque"

# direction: which way the money moves when it settles
PAY = "pay"
RECEIVE = "receive"

# status
PENDING = "pending"
SETTLED = "settled"
BOUNCED = "bounced"
CANCELLED = "cancelled"


class Commitment(Base, TenantMixin):
    """One dated obligation: a single installment, or a single cheque."""

    __tablename__ = "commitments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), default=INSTALLMENT, index=True)
    direction: Mapped[str] = mapped_column(String(8), default=PAY, index=True)
    title: Mapped[str] = mapped_column(String(256))
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, index=True)

    # Installments of one loan share a plan_id and carry their position in it,
    # so "3 of 12" and the remaining balance are a single grouped query.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cheque identity (unused for installments).
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    counterparty: Mapped[str | None] = mapped_column(String(256), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The liability/receivable leg settled against; the bank leg is resolved
    # from the locale at settle time.
    counter_account_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    settled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    settled_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
