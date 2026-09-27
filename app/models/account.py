from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenant import TenantMixin


class AccountLevel(str, enum.Enum):
    GROUP = "GROUP"
    GENERAL = "GENERAL"
    SUB = "SUB"
    DETAIL = "DETAIL"


class Account(Base, TenantMixin):
    __tablename__ = "accounts"
    # Account codes are unique PER COMPANY — two companies can each have 1100.
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_account_company_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(512))
    level: Mapped[AccountLevel] = mapped_column(Enum(AccountLevel, name="account_level"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True)
    detail_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # A deactivated account keeps its history but takes no new postings and
    # leaves the pickers (roadmap §4.5).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped["Account | None"] = relationship("Account", remote_side="Account.id", back_populates="children")
    children: Mapped[list["Account"]] = relationship("Account", back_populates="parent")

