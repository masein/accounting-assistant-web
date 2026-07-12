"""Per-company low-cash / daily-digest configuration (one row per company)."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, BigInteger, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DigestSetting(Base):
    __tablename__ = "digest_settings"

    # company_id IS the primary key — exactly one settings row per company.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    cash_threshold: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    runway_months: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=3, server_default="3")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="all", server_default="all")
