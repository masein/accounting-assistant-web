"""Super-admin provisioning console: create/list/update companies and reset a
company login's password. All endpoints gated to is_superadmin."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import hash_password, require_superadmin, validate_password_strength
from app.db.session import get_db
from app.db.tenant import tenant_bypass
from app.models.company import Company
from app.models.user import User
from app.services.company_service import SUPPORTED_LOCALES, provision_company

router = APIRouter(prefix="/admin/companies", tags=["companies"])

# Branding lives under uploads/branding/<company_id>/logo.<ext> (see
# company_profile._save_image), so the super-admin can serve any tenant's logo
# by id without crossing the ORM tenant scope.
_UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
_LOGO_EXTS = (".png", ".jpg", ".gif", ".webp")


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    locale: str = Field(default="default")
    base_currency: str = Field(default="IRR", max_length=8)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class CompanyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    locale: str | None = None
    base_currency: str | None = Field(default=None, max_length=8)
    status: str | None = None  # active | suspended


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def _company_login(db: Session, company_id) -> User | None:
    with tenant_bypass():
        return db.execute(
            select(User).where(User.company_id == company_id, User.is_superadmin == False)  # noqa: E712
        ).scalars().first()


def _serialize(db: Session, c: Company) -> dict:
    login = _company_login(db, c.id)
    return {
        "id": str(c.id),
        "name": c.name,
        "slug": c.slug,
        "locale": c.locale,
        "base_currency": c.base_currency,
        "status": c.status,
        "login_username": login.username if login else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("")
def list_companies(db: Session = Depends(get_db), _=Depends(require_superadmin)) -> list[dict]:
    with tenant_bypass():
        companies = db.execute(select(Company).order_by(Company.created_at.asc())).scalars().all()
        return [_serialize(db, c) for c in companies]


@router.get("/{company_id}/logo")
def company_logo(company_id: UUID, _=Depends(require_superadmin)) -> FileResponse:
    """A tenant's logo by id, for the Companies console thumbnail. Super-admin
    only; the path is built from a validated UUID, so it can't escape the
    branding dir. 404 when the company hasn't uploaded one."""
    brand_dir = _UPLOADS_DIR / "branding" / str(company_id)
    for ext in _LOGO_EXTS:
        candidate = brand_dir / f"logo{ext}"
        if candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="No logo")


@router.post("", status_code=201)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db), _=Depends(require_superadmin)) -> dict:
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        company, user = provision_company(
            db,
            name=payload.name,
            locale=payload.locale,
            base_currency=payload.base_currency,
            username=payload.username,
            password=payload.password,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _serialize(db, company)


@router.patch("/{company_id}")
def update_company(company_id: UUID, payload: CompanyPatch, db: Session = Depends(get_db), _=Depends(require_superadmin)) -> dict:
    with tenant_bypass():
        company = db.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        if payload.name is not None:
            company.name = payload.name.strip() or company.name
        if payload.locale is not None:
            loc = payload.locale.strip().lower()
            if loc not in SUPPORTED_LOCALES:
                raise HTTPException(status_code=400, detail=f"Unsupported locale '{payload.locale}'")
            company.locale = loc
        if payload.base_currency is not None:
            company.base_currency = payload.base_currency.strip().upper() or company.base_currency
        if payload.status is not None:
            st = payload.status.strip().lower()
            if st not in {"active", "suspended"}:
                raise HTTPException(status_code=400, detail="status must be active or suspended")
            if st != company.status:
                company.status = st
                # Suspending bumps the company token version → any live session
                # for that login is invalidated on its next request.
                company.token_version = (company.token_version or 0) + 1
        db.commit()
        db.refresh(company)
        return _serialize(db, company)


@router.post("/{company_id}/reset-password")
def reset_company_password(company_id: UUID, payload: PasswordReset, db: Session = Depends(get_db), _=Depends(require_superadmin)) -> dict:
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    with tenant_bypass():
        company = db.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        user = _company_login(db, company_id)
        if not user:
            raise HTTPException(status_code=404, detail="Company has no login")
        h, s = hash_password(payload.password)
        user.password_hash = h
        user.password_salt = s
        # Invalidate the user's existing sessions.
        user.token_version = (user.token_version or 0) + 1
        db.commit()
        return {"ok": True, "username": user.username}
