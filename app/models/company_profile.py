"""Per-company branding & issuer identity — drives every document's header,
party cards and footer. One row per company (tenant-scoped)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenant import TenantMixin

DEFAULT_BRAND_COLOR = "#0f766e"  # app teal


class CompanyProfile(Base, TenantMixin):
    __tablename__ = "company_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Issuer identity (falls back to Company.name when unset).
    legal_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    brand_color: Mapped[str] = mapped_column(String(16), default=DEFAULT_BRAND_COLOR)
    # Relative paths under uploads/branding/<company_id>/… — embedded into PDFs
    # server-side, never served from the public /uploads mount.
    logo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signature_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Official-invoice identity (فاکتور رسمی): economic code (شماره اقتصادی),
    # national ID (شناسه ملی), province/county, city, 10-digit postal code.
    economic_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    province: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Structured payment identity — printed as the "how to pay" line when the
    # free-text bank_details is empty.
    bank_account_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)

    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)

    bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_payment_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_footer: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_number_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
