"""Shareholder equity: the cap table (Shareholding) and the equity-movement
events (EquityEvent) that back the changes-in-equity statement.

The GL postings themselves live in normal Transactions/TransactionLines (balanced
double-entry, linked to the shareholder via a TransactionEntity with
role="shareholder"). An EquityEvent is a lightweight *tag* on top of that: it
records what kind of equity movement a posting represents (a contribution / آورده,
a capital increase / افزایش سرمایه, a dividend declaration or payment / سود سهام,
or a shareholder current-account movement / حساب جاری), so the changes-in-equity
report can surface real movement rows instead of zero placeholders, and so the
cap table can attribute paid-in capital per shareholder.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

# EquityEvent.event_type values.
EQUITY_EVENT_TYPES = (
    "contribution",           # آورده — shareholder injects cash/asset (→ share capital or current acct)
    "capital_increase",       # افزایش سرمایه — registered capital raised
    "dividend_declared",      # سود سهام مصوب — dividend declared from retained earnings
    "dividend_paid",          # dividend paid out (settles dividends payable)
    "current_account_in",     # shareholder lends to / is owed by the company
    "current_account_out",    # shareholder withdraws from the company
)

# Where a capital increase / contribution was funded from.
EQUITY_FUNDING_SOURCES = ("cash", "retained_earnings", "revaluation_surplus")

SHARE_CLASSES = ("ordinary", "preferred")


class Shareholding(Base, TenantMixin):
    """A shareholder's stake in the company — one row per shareholder (cap table)."""

    __tablename__ = "shareholdings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The shareholder party (Entity.type == "shareholder").
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Ownership percent (0–100), used to allocate dividends when shares aren't tracked.
    percent: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    par_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    since: Mapped[date | None] = mapped_column(Date, nullable=True)
    share_class: Mapped[str] = mapped_column(String(16), default="ordinary", server_default="ordinary")
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquityEvent(Base, TenantMixin):
    """A tagged equity movement, linked to the GL transaction(s) that posted it.

    Read by the changes-in-equity report to populate real movement rows, and by
    the cap table to attribute per-shareholder paid-in capital and dividends.
    """

    __tablename__ = "equity_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units, always positive
    # The shareholder this movement belongs to (null = company-wide, e.g. a
    # capital increase from retained earnings not split by holder).
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The GL transaction that posted this movement (null-safe: SET NULL on delete).
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # For contributions / capital increases: where the money came from.
    funded_from: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Groups a dividend declaration's per-shareholder rows (one declaration → N rows).
    group_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
