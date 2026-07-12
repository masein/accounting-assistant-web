"""API-key generation/verification for the /api/v1 integration surface."""
from __future__ import annotations

import hashlib
import secrets


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, sha256_hash, display_prefix). The raw key is shown to
    the owner exactly once; only the hash is stored."""
    raw = "ak_" + secrets.token_urlsafe(32)
    return raw, hash_api_key(raw), raw[:11]


def hash_api_key(raw: str) -> str:
    return hashlib.sha256((raw or "").strip().encode("utf-8")).hexdigest()


def extract_api_key(headers) -> str | None:
    """Pull the key from Authorization: Bearer <key> or X-API-Key."""
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return (headers.get("X-API-Key") or headers.get("x-api-key") or "").strip() or None
