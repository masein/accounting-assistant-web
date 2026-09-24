"""Secrets at rest.

API keys that operators paste into Settings → AI providers used to be saved
in app_settings as plain text (security review 2026-09-24, M10). They are now
stored as Fernet tokens keyed from AUTH_SECRET, so a database dump or a
read-only DB user does not expose them. Values are tagged ``enc:v1:`` so a
legacy plain-text value can still be read once and re-saved encrypted.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_log = logging.getLogger("app.secrets")
_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(("aa-secrets-v1|" + (settings.auth_secret or "")).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str | None) -> str:
    """Empty stays empty; anything else becomes an ``enc:v1:`` token."""
    if not plain:
        return ""
    return _PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_PREFIX)


def decrypt_secret(value: str | None) -> str:
    """Return the plain secret. Legacy plain-text values pass through; a token
    that no longer decrypts (AUTH_SECRET rotated) yields "" with a warning
    rather than a crash — the operator re-enters the key."""
    if not value:
        return ""
    if not is_encrypted(value):
        return str(value)
    try:
        return _fernet().decrypt(str(value)[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        _log.warning("stored secret could not be decrypted (AUTH_SECRET changed?) — treating it as unset")
        return ""
