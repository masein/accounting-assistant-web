from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    validate_password_strength,
    create_session_token,
    get_current_user,
    hash_password,
    needs_rehash,
    require_admin,
    verify_password,
)
from app.core.audit import audit_log, get_client_ip
from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.db.session import get_db
from app.models.user import User
from app.services.company_service import SUPPORTED_LOCALES, provision_company

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
_signup_limiter = RateLimiter(max_requests=5, window_seconds=3600)
SUPPORTED_LANGUAGES = {"en", "fa", "es", "ar"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    # What to call their books; defaults to the username.
    display_name: str | None = Field(default=None, max_length=256)
    locale: str = Field(default="default")


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

    # Upgrade a stale hash now — a successful verify is the only moment the
    # plaintext exists, so raising the work factor can only ever take effect
    # here. Never block the login on it: a failed rehash is not the user's
    # problem, and the old hash still verifies fine.
    try:
        if needs_rehash(user.password_hash):
            user.password_hash, user.password_salt = hash_password(payload.password)
            db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()

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
            "role": getattr(user, "role", None) or "owner",
            "entity_id": str(user.entity_id) if getattr(user, "entity_id", None) else None,
            "preferred_language": user.preferred_language or "en",
        },
        "company": _company_dict(company),
    }


@router.post("/signup", status_code=201)
def signup(payload: SignupRequest, request: Request, response: Response,
           db: Session = Depends(get_db)) -> dict:
    """Create a personal-finance account and sign the user in.

    Only ever creates a **personal** tenant: an anonymous stranger must not be
    able to provision a business company with payroll, approvals and user
    management attached. Business tenants stay super-admin provisioned.

    Off unless the operator sets ALLOW_SELF_SIGNUP. There is no email
    verification, because this app has no mail infrastructure and is routinely
    deployed air-gapped — so the protections here are the opt-in flag and a
    per-IP rate limit. Anyone exposing this to the open internet should add
    verification before doing so.
    """
    if not settings.allow_self_signup:
        raise HTTPException(status_code=403, detail="Self-signup is disabled on this server.")

    ip = get_client_ip(request)
    if not _signup_limiter.is_allowed(ip or "unknown"):
        raise HTTPException(status_code=429, detail="Too many sign-up attempts. Try again later.")

    username = payload.username.strip()
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    locale = (payload.locale or "default").strip().lower()
    if locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=400, detail=f"Unsupported locale '{payload.locale}'")

    try:
        company, user = provision_company(
            db,
            name=(payload.display_name or "").strip() or username,
            locale=locale,
            base_currency="GBP" if locale == "uk" else "IRR",
            username=username,
            password=payload.password,
            kind="personal",
        )
    except ValueError as e:
        db.rollback()
        # Covers the duplicate-username case; the message is the service's.
        raise HTTPException(status_code=400, detail=str(e)) from e

    audit_log(db, action="signup", entity_type="user", entity_id=str(user.id),
              detail=f"Self-signup for '{username}'", ip_address=ip)
    db.commit()

    # Sign them in: they just proved the password, so a second step would be
    # friction with no security gain.
    token = create_session_token(
        user_id=str(user.id), username=user.username, is_admin=user.is_admin,
        company_id=str(user.company_id) if user.company_id else None,
        is_superadmin=user.is_superadmin, token_version=user.token_version,
        role=user.role, entity_id=None,
    )
    cookie_secure = (
        settings.auth_cookie_secure
        if settings.auth_cookie_secure is not None
        else request.url.scheme == "https"
    )
    response.set_cookie(
        key=settings.auth_cookie_name, value=token, httponly=True, samesite="lax",
        secure=cookie_secure, max_age=int(settings.auth_session_hours * 3600), path="/",
    )
    return {
        "ok": True,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "is_admin": user.is_admin,
            "is_superadmin": user.is_superadmin,
            "role": user.role,
            "entity_id": None,
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
            "role": getattr(current, "role", None) or "owner",
            "entity_id": (
                str(user_row.entity_id) if user_row and user_row.entity_id else None
            ),
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
