from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TrialBalance(Base):
    __tablename__ = "trial_balances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["TrialBalanceLine"]] = relationship(
        "TrialBalanceLine", back_populates="trial_balance", cascade="all, delete-orphan"
    )


class TrialBalanceLine(Base):
    __tablename__ = "trial_balance_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trial_balance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trial_balances.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), index=True)

    # denormalized for reporting (and to preserve import-time labels)
    account_code: Mapped[str] = mapped_column(String(64), index=True)
    account_name: Mapped[str] = mapped_column(String(512))
    detail_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    debit_turnover: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_turnover: Mapped[int] = mapped_column(BigInteger, default=0)
    debit_balance: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_balance: Mapped[int] = mapped_column(BigInteger, default=0)

    trial_balance: Mapped["TrialBalance"] = relationship("TrialBalance", back_populates="lines")
    account: Mapped["Account"] = relationship("Account")

