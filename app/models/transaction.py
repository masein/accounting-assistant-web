from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Transaction(Base):
    """Journal entry header. Each transaction has one or more lines (debit/credit)."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, index=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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


class TransactionLine(Base):
    """Single debit or credit line of a journal entry."""

    __tablename__ = "transaction_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), index=True)

    debit: Mapped[int] = mapped_column(BigInteger, default=0)
    credit: Mapped[int] = mapped_column(BigInteger, default=0)
    line_description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    transaction: Mapped["Transaction"] = relationship("Transaction", back_populates="lines")
    account: Mapped["Account"] = relationship("Account")


class TransactionAttachment(Base):
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
