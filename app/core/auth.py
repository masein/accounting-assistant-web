from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings

CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "aa_csrf"


@dataclass
class SessionUser:
    user_id: str
    username: str
    is_admin: bool


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode((token + padding).encode("ascii"))


MIN_PASSWORD_LENGTH = 8


def validate_password_strength(password: str) -> None:
    """Enforce minimum password complexity for new passwords."""
    password = (password or "").strip()
    if not password:
        raise ValueError("Password cannot be empty")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.isdigit():
        raise ValueError("Password cannot be all digits")
    if password.isalpha():
        raise ValueError("Password must contain at least one digit or special character")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    password = (password or "").strip()
    if not password:
        raise ValueError("Password cannot be empty")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return digest.hex(), salt


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    try:
        calculated, _ = hash_password(password, salt=salt)
        return hmac.compare_digest(calculated, expected_hash)
    except Exception:
        return False


def create_session_token(*, user_id: str, username: str, is_admin: bool) -> str:
    now = int(time.time())
    exp = now + int(settings.auth_session_hours * 3600)
    payload = {
        "uid": user_id,
        "usr": username,
        "adm": bool(is_admin),
        "iat": now,
        "exp": exp,
    }
    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_json)
    sig = hmac.new(settings.auth_secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return payload_b64 + "." + sig


def parse_session_token(token: str | None) -> SessionUser | None:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    expected_sig = hmac.new(
        settings.auth_secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    now = int(time.time())
    if int(payload.get("exp", 0)) <= now:
        return None
    uid = str(payload.get("uid", "")).strip()
    usr = str(payload.get("usr", "")).strip()
    if not uid or not usr:
        return None
    return SessionUser(user_id=uid, username=usr, is_admin=bool(payload.get("adm", False)))


def get_current_user(request: Request) -> SessionUser:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def require_admin(request: Request) -> SessionUser:
    user = get_current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# CSRF helpers (Double Submit Cookie pattern)
# ---------------------------------------------------------------------------
def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request) -> bool:
    """
    Validate CSRF by comparing the cookie value with the header value.
    Returns True if valid, False otherwise.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get(CSRF_HEADER)
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)
