"""API-key generation/verification for the /api/v1 integration surface."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

# What an integration key may do (roadmap §1.13). Every /api/v1 route
# declares the scope it needs; a key carries the scopes it was granted.
SCOPES: dict[str, str] = {
    "time:read": "List worklogs the integration pushed",
    "time:write": "Push and delete worklogs",
    "bank_sms:write": "Forward bank SMS and notifications (roadmap §4.1)",
}
DEFAULT_SCOPES: tuple[str, ...] = ("time:read", "time:write")
DEFAULT_EXPIRY_DAYS = 365
MAX_EXPIRY_DAYS = 730


def normalize_scopes(scopes) -> list[str]:
    """Validated, de-duplicated, ordered scope list; raises ValueError."""
    out = []
    for s in scopes or []:
        s = str(s).strip().lower()
        if s not in SCOPES:
            raise ValueError(f"Unknown scope '{s}'. Known: {', '.join(SCOPES)}")
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("A key needs at least one scope.")
    return sorted(out)


def parse_scopes(stored: str | None) -> set[str]:
    """A NULL column predates scopes (the migration back-fills it, this is the
    belt): such a key keeps what every key could do then. An empty string is a
    key with no scopes at all and is allowed nothing."""
    if stored is None:
        return set(DEFAULT_SCOPES)
    return {s.strip() for s in stored.split(",") if s.strip()}


def is_expired(expires_at, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:  # SQLite hands back naive datetimes
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


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
