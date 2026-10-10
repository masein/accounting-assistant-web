"""Phone sessions for the Android app (roadmap ROADMAP_ANDROID_CHAT P0.1).

A signed-in phone is a ``MobileDevice``. It carries two secrets:

* an **access token**: the usual signed session token with a ``did`` (device
  id) claim and a short life (``mobile_access_minutes``), sent as
  ``Authorization: Bearer``. It is checked like a web session on every
  request, plus "is this device still signed in";
* a **refresh token**: random, long-lived (``mobile_refresh_days``), stored
  only as a hash. Each refresh hands out a new one and remembers the old one;
  the old one presented again means someone copied it, and the device is
  revoked on the spot.

The user's ``token_version`` is copied onto the device at sign-in: a password
change or a log-out-everywhere bumps it, and the phone's next refresh fails.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import create_session_token
from app.core.config import settings
from app.db.tenant import tenant_bypass
from app.models.mobile_device import MobileDevice
from app.models.user import User

# A device's last_seen_at is written at most this often (not on every request).
LAST_SEEN_EVERY = timedelta(minutes=5)


class RefreshRefused(Exception):
    """The refresh token is unknown, expired, reused or its device is gone."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands timezone-aware columns back naive
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_refresh() -> str:
    return secrets.token_urlsafe(48)


def access_token_for(user: User, device: MobileDevice) -> tuple[str, int]:
    """A short bearer session for this user on this device, and its life in seconds."""
    ttl = int(settings.mobile_access_minutes * 60)
    token = create_session_token(
        user_id=str(user.id), username=user.username, is_admin=user.is_admin,
        company_id=str(user.company_id) if user.company_id else None,
        is_superadmin=user.is_superadmin, token_version=int(user.token_version or 0),
        role=getattr(user, "role", None) or "owner",
        entity_id=str(user.entity_id) if getattr(user, "entity_id", None) else None,
        ttl_seconds=ttl, device_id=str(device.id),
    )
    return token, ttl


def open_device(db: Session, user: User, *, name: str, platform: str = "android",
                app_version: str | None = None) -> tuple[MobileDevice, str]:
    """Sign a phone in: a new device row and its first refresh token."""
    refresh = _new_refresh()
    device = MobileDevice(
        id=uuid.uuid4(), user_id=user.id, name=(name or "Phone").strip()[:80] or "Phone",
        platform=(platform or "android").strip().lower()[:16] or "android",
        app_version=(app_version or None) and app_version.strip()[:32],
        token_version=int(user.token_version or 0),
        refresh_hash=_hash(refresh),
        refresh_expires_at=_now() + timedelta(days=settings.mobile_refresh_days),
        last_seen_at=_now(),
    )
    db.add(device)
    db.flush()
    return device, refresh


def revoke(device: MobileDevice) -> None:
    if device.revoked_at is None:
        device.revoked_at = _now()


def rotate(db: Session, refresh_token: str, *, app_version: str | None = None) -> tuple[User, MobileDevice, str]:
    """Trade a refresh token for a new one. Raises RefreshRefused, revoking the
    device when an already-used token comes back."""
    digest = _hash(refresh_token or "")
    with tenant_bypass():
        device = db.execute(select(MobileDevice).where(MobileDevice.refresh_hash == digest)).scalars().first()
        if device is None:
            reused = db.execute(
                select(MobileDevice).where(MobileDevice.previous_refresh_hash == digest)
            ).scalars().first()
            if reused is not None:
                # The phone already traded this one in: whoever presents it now
                # holds a copy. End the device for both of them.
                revoke(reused)
                db.commit()
            raise RefreshRefused("unknown")
        user = db.get(User, device.user_id)
        refused = (
            device.revoked_at is not None
            or _aware(device.refresh_expires_at) <= _now()
            or user is None or not user.is_active
            or int(user.token_version or 0) != int(device.token_version or 0)
        )
        if refused:
            revoke(device)
            db.commit()
            raise RefreshRefused("ended")
        if user.company_id is not None:
            from app.models.company import Company
            company = db.get(Company, user.company_id)
            if company is not None and company.status != "active":
                raise RefreshRefused("suspended")
        refresh = _new_refresh()
        device.previous_refresh_hash = device.refresh_hash
        device.refresh_hash = _hash(refresh)
        device.refresh_expires_at = _now() + timedelta(days=settings.mobile_refresh_days)
        device.last_seen_at = _now()
        if app_version:
            device.app_version = app_version.strip()[:32]
        db.commit()
        return user, device, refresh


def device_is_live(db: Session, device_id: str, user_id) -> bool:
    """For the auth middleware: is this bearer token's device still signed in?
    Also notes when the phone was last seen (at most every few minutes)."""
    try:
        key = uuid.UUID(str(device_id))
    except (ValueError, TypeError):
        return False
    with tenant_bypass():
        device = db.get(MobileDevice, key)
        if device is None or device.revoked_at is not None or str(device.user_id) != str(user_id):
            return False
        seen = _aware(device.last_seen_at)
        if seen is None or _now() - seen > LAST_SEEN_EVERY:
            device.last_seen_at = _now()
            db.commit()
    return True


def devices_of(db: Session, user_id) -> list[MobileDevice]:
    with tenant_bypass():
        return list(db.execute(
            select(MobileDevice).where(MobileDevice.user_id == user_id, MobileDevice.revoked_at.is_(None))
            .order_by(MobileDevice.last_seen_at.desc().nullslast(), MobileDevice.created_at.desc())
        ).scalars())


def version_tuple(version: str | None) -> tuple[int, ...]:
    out = []
    for part in str(version or "").strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def too_old(app_version: str | None) -> bool:
    """An app that says its version and is below the server's minimum."""
    if not app_version:
        return False
    have, need = version_tuple(app_version), version_tuple(settings.mobile_min_app_version)
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) < need + (0,) * (width - len(need))
