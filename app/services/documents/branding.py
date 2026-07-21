"""Build the shared branded context (issuer identity, logo, colours, locale)
from the current company + its CompanyProfile."""
from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tenant import get_current_company, tenant_bypass
from app.models.company import Company
from app.models.company_profile import DEFAULT_BRAND_COLOR, CompanyProfile

# app/services/documents/branding.py → app/uploads
UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads"

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}

_FONT_RTL = "'Vazirmatn', 'Noto Naskh Arabic', 'Noto Sans Arabic', 'DejaVu Sans', sans-serif"
_FONT_LTR = "'Noto Sans', 'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif"


def _data_uri(rel_path: str | None) -> str | None:
    """Embed an uploaded image as a base64 data URI — never a public URL."""
    if not rel_path:
        return None
    p = (UPLOADS_DIR / rel_path).resolve()
    try:
        # Guard against path traversal outside the uploads dir.
        p.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return None
    if not p.is_file():
        return None
    mime = _MIME.get(p.suffix.lower(), "image/png")
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def resolve_company(db: Session) -> Company | None:
    import uuid
    cid = get_current_company()
    if not cid:
        return None
    try:
        cid = uuid.UUID(str(cid))
    except (ValueError, TypeError):
        pass
    with tenant_bypass():
        return db.get(Company, cid)


def _profile(db: Session) -> CompanyProfile | None:
    return db.execute(select(CompanyProfile)).scalars().first()


def build_brand(db: Session) -> dict:
    """The branded header/identity/theme shared by every document."""
    company = resolve_company(db)
    profile = _profile(db)
    locale = (company.locale if company else "default") or "default"
    rtl = locale.lower() == "ir"

    name = (profile.legal_name if profile and profile.legal_name else None) \
        or (company.name if company else "Company")
    brand_color = (profile.brand_color if profile and profile.brand_color else None) or DEFAULT_BRAND_COLOR

    # Structured account + شبا/IBAN compose the "how to pay" line when the
    # free-text bank_details is empty (official invoices print both).
    bank_details = profile.bank_details if profile else None
    if not bank_details and profile and (profile.bank_account_no or profile.iban):
        parts = []
        if profile.bank_account_no:
            parts.append(("شماره حساب" if rtl else "Account no.") + f": {profile.bank_account_no}")
        if profile.iban:
            parts.append(("شبا" if rtl else "IBAN") + f": {profile.iban}")
        parts.append(name)
        bank_details = " — ".join(parts)

    issuer = {
        "name": name,
        "address": profile.address if profile else None,
        "tax_id": profile.tax_id if profile else None,
        "registration_number": profile.registration_number if profile else None,
        "economic_code": profile.economic_code if profile else None,
        "national_id": profile.national_id if profile else None,
        "province": profile.province if profile else None,
        "city": profile.city if profile else None,
        "postal_code": profile.postal_code if profile else None,
        "email": profile.email if profile else None,
        "phone": profile.phone if profile else None,
        "website": profile.website if profile else None,
        "bank_details": bank_details,
        "payment_terms": profile.default_payment_terms if profile else None,
        "footer": profile.invoice_footer if profile else None,
        "logo": _data_uri(profile.logo_path) if profile else None,
        "signature": _data_uri(profile.signature_path) if profile else None,
        "number_prefix": profile.invoice_number_prefix if profile else None,
    }
    return {
        "locale": locale,
        "dir": "rtl" if rtl else "ltr",
        "rtl": rtl,
        "brand_color": brand_color,
        "font_family": _FONT_RTL if rtl else _FONT_LTR,
        "base_currency": (company.base_currency if company else "IRR") or "IRR",
        "issuer": issuer,
    }


def build_recipient(entity) -> dict | None:
    """The Bill-To / recipient party card from an Entity's saved billing fields."""
    if entity is None:
        return None
    return {
        "name": (entity.legal_name or entity.name) if entity else None,
        "secondary": entity.name if (entity.legal_name and entity.legal_name != entity.name) else None,
        "address": getattr(entity, "address", None),
        "tax_id": getattr(entity, "tax_id", None),
        "economic_code": getattr(entity, "economic_code", None),
        "national_id": getattr(entity, "national_id", None),
        "province": getattr(entity, "province", None),
        "city": getattr(entity, "city", None),
        "postal_code": getattr(entity, "postal_code", None),
        "email": getattr(entity, "email", None),
        "phone": getattr(entity, "phone", None),
        "contact_person": getattr(entity, "contact_person", None),
        "payment_terms": getattr(entity, "payment_terms", None),
    }
