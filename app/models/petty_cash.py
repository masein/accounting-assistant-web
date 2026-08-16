"""Petty cash (حساب تنخواه): per-user imprest accounts with an approval flow.

A user (تنخواه‌گردان) gets a petty cash account funded by the company. The
GL keeps ONE petty-cash control account (resolver category ``petty_cash``);
per-user balances live in this subledger:

    balance = approved deposits + approved adjustments − approved expenses

Money movements post real journal entries at decision time:
- deposit/charge (admin): DR petty_cash / CR bank            — funds handed out
- expense approved:       DR expense category / CR petty_cash — spend recognized
- adjustment (admin):     signed correction between petty_cash and a chosen
  counter account (e.g. returning unused float to the bank).

Users record expenses (with a receipt attachment) against their own account;
admins approve/reject, charge and adjust, and see every account.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class PettyCashAccount(Base, TenantMixin):
    __tablename__ = "petty_cash_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # holder's login id
    holder_name: Mapped[str] = mapped_column(String(256))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transactions: Mapped[list["PettyCashTransaction"]] = relationship(
        "PettyCashTransaction", back_populates="account", cascade="all, delete-orphan",
        order_by="PettyCashTransaction.created_at",
    )


class PettyCashTransaction(Base, TenantMixin):
    __tablename__ = "petty_cash_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("petty_cash_accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # deposit | expense | adjustment
    # positive minor units; adjustments carry their sign in `signed_amount`
    amount: Mapped[int] = mapped_column(BigInteger)
    signed_amount: Mapped[int] = mapped_column(BigInteger)  # +adds to the float, −spends it
    description: Mapped[str] = mapped_column(Text, default="")
    # expense category / deposit source (bank) / adjustment counter account
    counter_account_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transaction_attachments.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | approved | rejected
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[PettyCashAccount] = relationship("PettyCashAccount", back_populates="transactions")
