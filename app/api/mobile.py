"""The Android app's API, ``/api/mobile/v1`` (roadmap ROADMAP_ANDROID_CHAT
P0.1 and P0.2).

Sign-in hands the phone a short bearer access token and a refresh token, both
tied to a ``MobileDevice``. The bearer is accepted on every protected route
(the middleware skips CSRF for it: a header the app sets is not sent by a
browser on its own), but this namespace is the contract the app is written
against: versioned, documented, and covered by contract tests.

Two routers: ``auth_router`` holds the public sign-in steps (no session yet);
``router`` holds everything that needs one, behind the RBAC guard.
"""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import _company_dict, _load_user, password_step, second_factor_step, two_factor_on
from app.core.audit import audit_log, get_client_ip
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.db.tenant import tenant_bypass
from app.models.mobile_device import MobileDevice
from app.services import mobile_sessions as ms

PREFIX = "/api/mobile/v1"


class UpgradeRequired(Exception):
    """Raised by the version check; answered with 426 by the app's handler."""


def check_app_version(x_app_version: str | None = Header(default=None)) -> None:
    """An app older than the server's minimum is told to update (N5)."""
    if ms.too_old(x_app_version):
        raise UpgradeRequired()


def upgrade_required_response() -> JSONResponse:
    return JSONResponse(status_code=426, content={
        "code": "upgrade_required",
        "min_version": settings.mobile_min_app_version,
        "detail": "This version of the app is too old. Update it to keep going.",
    })


auth_router = APIRouter(prefix=PREFIX + "/auth", tags=["mobile"], dependencies=[Depends(check_app_version)])
router = APIRouter(prefix=PREFIX, tags=["mobile"], dependencies=[Depends(check_app_version)])


class DeviceInfo(BaseModel):
    device_name: str = Field(default="Phone", min_length=1, max_length=80)
    platform: str = Field(default="android", max_length=16)
    app_version: str | None = Field(default=None, max_length=32)


class MobileLoginRequest(DeviceInfo):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class MobileTwoFactorRequest(DeviceInfo):
    challenge: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=1, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)
    app_version: str | None = Field(default=None, max_length=32)


def _refuse_default_password() -> HTTPException:
    return HTTPException(status_code=403, detail=(
        "You signed in with the default password. Set a new password on the web first, then sign in here."),
        headers={"X-Error-Code": "password_change_required"})


def _session_body(db: Session, user, device: MobileDevice, refresh: str, company) -> dict:
    access, ttl = ms.access_token_for(user, device)
    return {
        "ok": True,
        "access_token": access,
        "token_type": "bearer",
        "expires_in": ttl,
        "refresh_token": refresh,
        "device_id": str(device.id),
        "user": _user_dict(user),
        "company": _company_dict(company),
    }


def _user_dict(user) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "role": getattr(user, "role", None) or "owner",
        "is_admin": bool(user.is_admin),
        "entity_id": str(user.entity_id) if getattr(user, "entity_id", None) else None,
        "preferred_language": user.preferred_language or "en",
        "two_factor_enabled": two_factor_on(user),
    }


def _sign_in(db: Session, request: Request, user, company, info: DeviceInfo) -> dict:
    device, refresh = ms.open_device(db, user, name=info.device_name, platform=info.platform,
                                     app_version=info.app_version)
    audit_log(db, action="mobile_device_signed_in", entity_type="mobile_device", entity_id=str(device.id),
              user_id=str(user.id), username=user.username, detail=f"{device.platform}: {device.name}",
              ip_address=get_client_ip(request))
    db.commit()
    return _session_body(db, user, device, refresh, company)


@auth_router.post("/login")
def mobile_login(payload: MobileLoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Sign a phone in (N1). With two-factor on, the answer is a challenge;
    the tokens come from ``/auth/2fa``."""
    user, company, must_change, challenge = password_step(payload.username, payload.password, request, db)
    if must_change:
        raise _refuse_default_password()
    if challenge:
        return {"ok": False, "two_factor_required": True, "challenge": challenge}
    audit_log(db, action="login", entity_type="user", entity_id=str(user.id), user_id=str(user.id),
              username=user.username, detail=f"phone: {payload.device_name}", ip_address=get_client_ip(request))
    return _sign_in(db, request, user, company, payload)


@auth_router.post("/2fa")
def mobile_login_two_factor(payload: MobileTwoFactorRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user, company, must_change, method, left = second_factor_step(
        payload.challenge, payload.code, request, db, via=f"phone: {payload.device_name}")
    if must_change:
        raise _refuse_default_password()
    body = _sign_in(db, request, user, company, payload)
    body["two_factor_method"] = method
    body["recovery_codes_left"] = left
    return body


@auth_router.post("/refresh")
def mobile_refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    """A new access token and a new refresh token for the old one (N3)."""
    try:
        user, device, refresh = ms.rotate(db, payload.refresh_token, app_version=payload.app_version)
    except ms.RefreshRefused:
        raise HTTPException(status_code=401, detail="Your session on this phone has ended. Sign in again.",
                            headers={"X-Error-Code": "session_ended"})
    company = None
    if user.company_id is not None:
        from app.models.company import Company
        with tenant_bypass():
            company = db.get(Company, user.company_id)
    return _session_body(db, user, device, refresh, company)


@router.get("/me")
def mobile_me(current=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Who is signed in, in which books, and on which device."""
    user = _load_user(db, current)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    company = None
    if user.company_id is not None:
        from app.models.company import Company
        with tenant_bypass():
            company = db.get(Company, user.company_id)
    body = {"user": _user_dict(user), "company": _company_dict(company),
            "device_id": getattr(current, "device_id", None)}
    if company is not None:
        body["company"]["kind"] = getattr(company, "kind", None) or "business"
    return body


class LanguageRequest(BaseModel):
    language: str = Field(min_length=2, max_length=8)


@router.put("/me/language")
def mobile_language(payload: LanguageRequest, current=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """The phone's language becomes the account's: the accountant answers,
    and says what it is doing, in it."""
    from app.api.auth import SUPPORTED_LANGUAGES
    lang = payload.language.strip().lower()[:2]
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language")
    user = _load_user(db, current)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user.preferred_language = lang
    db.commit()
    return {"language": lang}


@router.delete("/session")
def mobile_sign_out(request: Request, current=Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Sign this phone out (N4). The web and other phones stay signed in."""
    device_id = getattr(current, "device_id", None)
    if device_id:
        device = _own_device(db, current, device_id)
        if device is not None:
            ms.revoke(device)
            audit_log(db, action="mobile_device_signed_out", entity_type="mobile_device", entity_id=str(device.id),
                      user_id=str(current.user_id), username=current.username, ip_address=get_client_ip(request))
            db.commit()
    return {"ok": True}


def _device_dict(device: MobileDevice, current_id: str | None) -> dict:
    return {
        "id": str(device.id),
        "name": device.name,
        "platform": device.platform,
        "app_version": device.app_version,
        "signed_in_at": device.created_at.isoformat() if device.created_at else None,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "this_device": str(device.id) == str(current_id or ""),
    }


def _own_device(db: Session, current, device_id) -> MobileDevice | None:
    try:
        key = uuid.UUID(str(device_id))
    except (ValueError, TypeError):
        return None
    with tenant_bypass():
        device = db.get(MobileDevice, key)
    if device is None or str(device.user_id) != str(current.user_id):
        return None
    return device


@router.get("/devices")
def mobile_devices(current=Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    """The phones signed in to this account (N4). Works from the web too."""
    user = _load_user(db, current)
    if user is None:
        return []
    return [_device_dict(d, getattr(current, "device_id", None)) for d in ms.devices_of(db, user.id)]


@router.delete("/devices/{device_id}")
def mobile_revoke_device(device_id: str, request: Request, current=Depends(get_current_user),
                         db: Session = Depends(get_db)) -> dict:
    """Sign a phone out from anywhere: a lost phone, an old one (N4)."""
    device = _own_device(db, current, device_id)
    if device is None or device.revoked_at is not None:
        raise HTTPException(status_code=404, detail="No such phone is signed in to your account.")
    ms.revoke(device)
    audit_log(db, action="mobile_device_revoked", entity_type="mobile_device", entity_id=str(device.id),
              user_id=str(current.user_id), username=current.username, detail=device.name,
              ip_address=get_client_ip(request))
    db.commit()
    return {"ok": True}


# --- crash reports (roadmap ROADMAP_ANDROID_CHAT P1.8) --------------------------------------

class CrashReport(BaseModel):
    """What the app kept of a crash: where it happened, never what it said.
    No exception messages (they can hold amounts and typed words), no screen
    content; class names and stack frames, the app and phone versions."""
    at: str = Field(max_length=40)
    app_version: str = Field(max_length=32)
    android: int = Field(ge=1, le=99)
    device: str = Field(default="", max_length=80)
    thread: str = Field(default="", max_length=60)
    exception: str = Field(max_length=200)
    causes: list[str] = Field(default=[], max_length=8)
    frames: list[str] = Field(default=[], max_length=60)


class CrashBatch(BaseModel):
    reports: list[CrashReport] = Field(min_length=1, max_length=5)


_FRAME = re.compile(r"^[\w$.<>\-]+\([\w$.\- ]*(:\d+)?\)$", re.ASCII)   # class and file names, never words


@router.post("/crashes")
def mobile_crashes(payload: CrashBatch, current=Depends(get_current_user)) -> dict:
    """The app's crashes since it last sent them: logged, and passed to
    Sentry or GlitchTip when one is set (SENTRY_DSN), grouped by the
    exception and its first frame of our own code."""
    import logging
    log = logging.getLogger("app.mobile.crash")
    from app.core.observability import _sentry_on
    for r in payload.reports:
        frames = [f[:200] for f in r.frames if _FRAME.match(f[:200])][:40]
        ours = next((f for f in frames if f.startswith("app.accountingassistant.")), frames[0] if frames else "")
        log.warning("mobile_crash", extra={"crash": {
            "at": r.at, "app_version": r.app_version, "android": r.android, "device": r.device, "thread": r.thread,
            "exception": r.exception, "causes": r.causes[:8], "where": ours, "frames": frames,
            "user": getattr(current, "username", None), "device_id": getattr(current, "device_id", None)}})
        if _sentry_on:
            try:
                import sentry_sdk
                with sentry_sdk.new_scope() as scope:
                    scope.set_tag("source", "android")
                    scope.set_tag("app_version", r.app_version)
                    scope.set_tag("android", str(r.android))
                    scope.set_context("crash", {"device": r.device, "thread": r.thread, "causes": r.causes[:8],
                                                "frames": frames})
                    scope.fingerprint = ["android", r.exception, ours]
                    sentry_sdk.capture_message(f"Android: {r.exception} at {ours}", level="error")
            except Exception:  # monitoring must never fail the request
                log.warning("crash report not passed to Sentry", exc_info=True)
    return {"received": len(payload.reports)}
