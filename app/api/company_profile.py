"""Company branding profile — Settings → Company profile. The company's own
admin edits its issuer identity (logo, brand colour, address, tax id, bank
details, footer). Logos/stamps are stored tenant-scoped and embedded into PDFs
server-side, never served from the public /uploads mount."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin
from app.db.session import get_db
from app.db.tenant import get_current_company
from app.models.company import Company
from app.models.company_profile import DEFAULT_BRAND_COLOR, CompanyProfile

router = APIRouter(prefix="/admin/company-profile", tags=["company-profile"])

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
_ALLOWED_IMG = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

_FIELDS = [
    "legal_name", "brand_color", "address", "tax_id", "registration_number",
    "economic_code", "national_id", "province", "city", "postal_code",
    "bank_account_no", "iban",
    "email", "phone", "website", "bank_details", "default_payment_terms",
    "invoice_footer", "invoice_number_prefix",
]


class CompanyProfilePut(BaseModel):
    legal_name: str | None = Field(None, max_length=256)
    brand_color: str | None = Field(None, max_length=16)
    address: str | None = None
    tax_id: str | None = Field(None, max_length=128)
    registration_number: str | None = Field(None, max_length=128)
    economic_code: str | None = Field(None, max_length=32)
    national_id: str | None = Field(None, max_length=32)
    province: str | None = Field(None, max_length=128)
    city: str | None = Field(None, max_length=128)
    postal_code: str | None = Field(None, max_length=16)
    bank_account_no: str | None = Field(None, max_length=64)
    iban: str | None = Field(None, max_length=34)
    email: str | None = Field(None, max_length=256)
    phone: str | None = Field(None, max_length=64)
    website: str | None = Field(None, max_length=256)
    bank_details: str | None = None
    default_payment_terms: str | None = Field(None, max_length=128)
    invoice_footer: str | None = None
    invoice_number_prefix: str | None = Field(None, max_length=32)


def _get_or_create(db: Session) -> CompanyProfile:
    profile = db.execute(select(CompanyProfile)).scalars().first()
    if profile is None:
        profile = CompanyProfile(brand_color=DEFAULT_BRAND_COLOR)
        db.add(profile)
        db.flush()
    return profile


def _serialize(db: Session, profile: CompanyProfile) -> dict:
    cid = get_current_company()
    company = db.get(Company, cid) if cid else None
    return {
        "company": {
            "name": company.name if company else None,
            "locale": company.locale if company else None,
            "base_currency": company.base_currency if company else None,
        },
        "legal_name": profile.legal_name,
        "brand_color": profile.brand_color or DEFAULT_BRAND_COLOR,
        "address": profile.address,
        "tax_id": profile.tax_id,
        "registration_number": profile.registration_number,
        "economic_code": profile.economic_code,
        "national_id": profile.national_id,
        "province": profile.province,
        "city": profile.city,
        "postal_code": profile.postal_code,
        "bank_account_no": profile.bank_account_no,
        "iban": profile.iban,
        "email": profile.email,
        "phone": profile.phone,
        "website": profile.website,
        "bank_details": profile.bank_details,
        "default_payment_terms": profile.default_payment_terms,
        "invoice_footer": profile.invoice_footer,
        "invoice_number_prefix": profile.invoice_number_prefix,
        "has_logo": bool(profile.logo_path),
        "has_signature": bool(profile.signature_path),
    }


@router.get("")
def get_profile(db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    profile = _get_or_create(db)
    db.commit()
    return _serialize(db, profile)


@router.get("/logo")
def get_logo(db: Session = Depends(get_db), _=Depends(get_current_user)) -> FileResponse:
    """Serve the current tenant's logo to any of its signed-in users (it appears
    on every document — not a secret), so the sidebar/brand can render it. Still
    tenant-scoped: the query only sees this company's row, never another's."""
    profile = db.execute(select(CompanyProfile)).scalars().first()
    if profile is None or not profile.logo_path:
        raise HTTPException(status_code=404, detail="No logo")
    abs_path = (UPLOADS_DIR / profile.logo_path).resolve()
    try:
        abs_path.relative_to(UPLOADS_DIR.resolve())  # guard against traversal
    except ValueError:
        raise HTTPException(status_code=404, detail="No logo")
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="No logo")
    return FileResponse(abs_path)


@router.put("")
def put_profile(payload: CompanyProfilePut, db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    profile = _get_or_create(db)
    data = payload.model_dump(exclude_unset=True)
    for f in _FIELDS:
        if f in data:
            val = data[f]
            if isinstance(val, str):
                val = val.strip() or None
            setattr(profile, f, val)
    if not profile.brand_color:
        profile.brand_color = DEFAULT_BRAND_COLOR
    db.commit()
    db.refresh(profile)
    return _serialize(db, profile)


def _save_image(db: Session, file: UploadFile, kind: str) -> dict:
    if file.content_type not in _ALLOWED_IMG:
        raise HTTPException(status_code=400, detail="Logo must be PNG, JPG, GIF or WEBP")
    raw = file.file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 2 MB)")
    cid = get_current_company()
    if not cid:
        raise HTTPException(status_code=400, detail="No company context")
    ext = _ALLOWED_IMG[file.content_type]
    rel_dir = Path("branding") / str(cid)
    abs_dir = UPLOADS_DIR / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / f"{kind}{ext}"
    (UPLOADS_DIR / rel_path).write_bytes(raw)
    profile = _get_or_create(db)
    if kind == "logo":
        profile.logo_path = str(rel_path)
    else:
        profile.signature_path = str(rel_path)
    db.commit()
    return {"ok": True, "kind": kind}


@router.post("/logo")
def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    return _save_image(db, file, "logo")


@router.post("/signature")
def upload_signature(file: UploadFile = File(...), db: Session = Depends(get_db), _=Depends(require_admin)) -> dict:
    return _save_image(db, file, "signature")
