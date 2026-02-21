from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FeeType(str, enum.Enum):
    FLAT = "flat"
    PERCENT = "percent"
    HYBRID = "hybrid"
    FREE = "free"


class FeeApplicationStatus(str, enum.Enum):
    PENDING = "pending"
    APPLIED = "applied"
    SKIPPED = "skipped"


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # normalized slug (e.g. card_to_card)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # display name (e.g. Card-to-Card)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fee_rules: Mapped[list["TransactionFee"]] = relationship("TransactionFee", back_populates="method")


class TransactionFee(Base):
    __tablename__ = "transaction_fees"
    __table_args__ = (
        UniqueConstraint("method_id", "bank_id", "effective_from", name="uq_transaction_fee_method_bank_effective"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_methods.id", ondelete="CASCADE"), index=True
    )
    bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    fee_type: Mapped[FeeType] = mapped_column(Enum(FeeType, name="fee_type"), default=FeeType.FLAT, index=True)
    # fee_value is kept for compatibility with integrations expecting one value:
    # flat => flat amount, percent => bps (1% = 100), hybrid/free => 0
    fee_value: Mapped[int] = mapped_column(BigInteger, default=0)
    flat_fee: Mapped[int] = mapped_column(BigInteger, default=0)
    percent_bps: Mapped[int] = mapped_column(Integer, default=0)  # 1% = 100 bps
    max_fee: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    method: Mapped["PaymentMethod"] = relationship("PaymentMethod", back_populates="fee_rules")
    bank = relationship("Entity")


class TransactionFeeApplication(Base):
    __tablename__ = "transaction_fee_applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), unique=True, index=True, nullable=True
    )
    method_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_methods.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bank_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fee_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transaction_fees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[FeeApplicationStatus] = mapped_column(
        Enum(FeeApplicationStatus, name="fee_application_status"), default=FeeApplicationStatus.PENDING, index=True
    )
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)  # payment | receipt
    amount_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # net | gross
    base_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gross_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    method: Mapped["PaymentMethod | None"] = relationship("PaymentMethod")
    bank = relationship("Entity")
    fee_rule: Mapped["TransactionFee | None"] = relationship("TransactionFee")
    transaction = relationship("Transaction")
