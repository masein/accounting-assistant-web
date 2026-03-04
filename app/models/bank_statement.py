"""
Bank statement models: imported statement batches and individual rows.
Supports CSV, Excel, and OCR-extracted statements.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BankStatement(Base):
    """An imported bank statement batch (one file upload = one batch)."""

    __tablename__ = "bank_statements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_name: Mapped[str] = mapped_column(String(256), index=True)
    account_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)  # csv, excel, ocr_image, ocr_pdf
    source_filename: Mapped[str] = mapped_column(String(512))
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # pending -> parsed -> reviewing -> approved -> committed
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    matched_rows: Mapped[int] = mapped_column(Integer, default=0)
    new_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    rows: Mapped[list["BankStatementRow"]] = relationship(
        "BankStatementRow", back_populates="statement", cascade="all, delete-orphan",
        order_by="BankStatementRow.row_index",
    )


class BankStatementRow(Base):
    """A single row extracted from a bank statement."""

    __tablename__ = "bank_statement_rows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bank_statements.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(Integer, index=True)
    tx_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    debit: Mapped[int] = mapped_column(BigInteger, default=0)
    credit: Mapped[int] = mapped_column(BigInteger, default=0)
    balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suggested_account_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Reconciliation status
    recon_status: Mapped[str] = mapped_column(String(32), default="unmatched", index=True)
    # unmatched, matched, partial, duplicate, skipped
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    user_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    statement: Mapped["BankStatement"] = relationship("BankStatement", back_populates="rows")
