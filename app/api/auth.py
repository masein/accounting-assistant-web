from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    create_session_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.core.audit import audit_log, get_client_ip
from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.db.session import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _company_dict(company) -> dict | None:
    if company is None:
        return None
    return {
        "id": str(company.id),
        "name": company.name,
        "slug": company.slug,
        "locale": company.locale,
        "base_currency": company.base_currency,
        "status": company.status,
    }

# 5 login attempts per 15 minutes per username
_login_limiter = RateLimiter(max_requests=5, window_seconds=900)
SUPPORTED_LANGUAGES = {"en", "fa", "es", "ar"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PreferencesPatchRequest(BaseModel):
    language: str = Field(min_length=2, max_length=8)


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip()
    if not _login_limiter.is_allowed(username):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    user = db.execute(select(User).where(User.username == username)).scalars().first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash, user.password_salt):
        audit_log(db, action="login_failed", entity_type="user", detail=f"Failed login for '{username}'", ip_address=get_client_ip(request))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Refuse a login whose company is suspended. Checked AFTER credential
    # verification so it never leaks whether a username exists.
    company = None
    if user.company_id is not None:
        from app.models.company import Company
        from app.db.tenant import tenant_bypass
        with tenant_bypass():
            company = db.get(Company, user.company_id)
        if company is not None and company.status != "active":
            audit_log(db, action="login_refused", entity_type="user", entity_id=str(user.id),
                      detail=f"Suspended company for '{username}'", ip_address=get_client_ip(request))
            db.commit()
            raise HTTPException(status_code=403, detail="This company account is suspended")

    audit_log(db, action="login", entity_type="user", entity_id=str(user.id), user_id=str(user.id), username=user.username, ip_address=get_client_ip(request))
    token = create_session_token(
        user_id=str(user.id), username=user.username, is_admin=user.is_admin,
        company_id=str(user.company_id) if user.company_id else None,
        is_superadmin=user.is_superadmin, token_version=user.token_version,
        role=getattr(user, "role", None) or "owner",
        entity_id=str(user.entity_id) if getattr(user, "entity_id", None) else None,
    )
    # Secure flag: explicit override if set, else follow the request scheme so
    # plain-HTTP access still stores the cookie (a Secure cookie is dropped by
    # browsers over http://).
    cookie_secure = (
        settings.auth_cookie_secure
        if settings.auth_cookie_secure is not None
        else request.url.scheme == "https"
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
        max_age=int(settings.auth_session_hours * 3600),
        path="/",
    )
    return {
        "ok": True,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin,
            "is_superadmin": user.is_superadmin,
            "preferred_language": user.preferred_language or "en",
        },
        "company": _company_dict(company),
    }


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(current=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    from app.models.company import Company
    from app.db.tenant import tenant_bypass
    user_row = db.get(User, current.user_id)
    user_language = user_row.preferred_language if user_row and user_row.preferred_language else "en"
    company = None
    if current.company_id:
        with tenant_bypass():
            company = db.get(Company, current.company_id)
    return {
        "authenticated": True,
        "user": {
            "id": current.user_id,
            "username": current.username,
            "is_admin": current.is_admin,
            "is_superadmin": current.is_superadmin,
            "preferred_language": user_language,
        },
        "company": _company_dict(company),
    }


@router.post("/change-password")
def change_password(payload: PasswordChangeRequest, db: Session = Depends(get_db), current=Depends(get_current_user)) -> dict:
    user = db.get(User, current.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    h, s = hash_password(payload.password)
    user.password_hash = h
    user.password_salt = s
    db.commit()
    return {"ok": True}


@router.patch("/preferences")
def update_preferences(payload: PreferencesPatchRequest, db: Session = Depends(get_db), current=Depends(get_current_user)) -> dict:
    user = db.get(User, current.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    lang = (payload.language or "").strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    user.preferred_language = lang
    db.commit()
    return {"ok": True, "language": user.preferred_language}


@router.get("/admin-check")
def admin_check(_=Depends(require_admin)) -> dict:
    return {"ok": True}
