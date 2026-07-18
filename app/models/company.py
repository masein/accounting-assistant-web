"""Company = a tenant. Each has its own login, its own seeded chart of accounts
and its own fully-isolated books. A super-admin provisions companies."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    locale: Mapped[str] = mapped_column(String(16), default="default")  # uk | ir | default
    base_currency: Mapped[str] = mapped_column(String(8), default="IRR")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | suspended
    # Bumped on suspend / password reset to invalidate existing session tokens.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    # Registered/authorised share capital (minor units) — raised by capital
    # increases and paid-in contributions; surfaced on the cap table.
    registered_capital: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
