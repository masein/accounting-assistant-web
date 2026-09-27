"""Web Push for the bell (roadmap 2026-09 §4.10, part 2).

A phone or browser that turned notifications on holds a push subscription
(an endpoint at its push service plus two keys). When the notification feed
gains a new alert, the people who would see it in the bell get it pushed.

The protocol is small enough to speak directly with what the app already
ships (``cryptography``, ``httpx``):

* VAPID (RFC 8292): each request carries an ES256-signed JWT for the push
  service's origin and the server's public key;
* message encryption (RFC 8291, ``aes128gcm`` of RFC 8188): ECDH with the
  subscription's key, HKDF with its auth secret, AES-128-GCM — checked in the
  tests against the RFC's own worked example.

Only the major push services are accepted as endpoints, so a subscription
can't make the server POST to an arbitrary host. The payload is the alert's
title, message and page — nothing else leaves.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("app.web_push")

VAPID_KEY = "web_push_vapid"             # platform app_settings row
RECORD_SIZE = 4096
TTL_SECONDS = 24 * 3600
MAX_PER_USER_EACH_TICK = 3               # more than this: one summary push instead
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com", "android.googleapis.com",           # Chrome, Edge on Android, Samsung
    "push.services.mozilla.com",                               # Firefox
    "push.apple.com",                                          # Safari (web.push.apple.com)
    "notify.windows.com",                                      # Edge on Windows (WNS)
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private_from_raw(raw: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _raw_private(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 with a single expand block (all lengths here are ≤ 32)."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


# --- RFC 8291 message encryption ------------------------------------------------------------

def encrypt(payload: bytes, ua_public_b64: str, auth_b64: str, *, sender_private: bytes | None = None,
            salt: bytes | None = None, record_size: int = RECORD_SIZE) -> bytes:
    """The aes128gcm body for one push message (a single record)."""
    ua_public = b64url_decode(ua_public_b64)
    auth_secret = b64url_decode(auth_b64)
    if len(ua_public) != 65 or ua_public[0] != 4 or len(auth_secret) != 16:
        raise ValueError("not a valid push subscription key")
    if len(payload) > record_size - 17:
        raise ValueError("payload too large for one record")
    as_private = _private_from_raw(sender_private) if sender_private else ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, ecdh_secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt if salt is not None else os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)     # 0x02: the last (only) record
    header = salt + struct.pack(">I", record_size) + bytes([len(as_public)]) + as_public
    return header + ciphertext


# --- VAPID -------------------------------------------------------------------------------------

def vapid_authorization(endpoint: str, private_key: ec.EllipticCurvePrivateKey, subject: str,
                        *, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600),
              "sub": subject}
    signing_input = (b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
                     + "." + b64url(json.dumps(claims, separators=(",", ":")).encode()))
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    jwt = signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64url(_public_bytes(private_key.public_key()))}"


def subject() -> str:
    from app.core.config import settings
    if getattr(settings, "vapid_subject", None):
        return settings.vapid_subject
    if settings.smtp_from:
        return f"mailto:{settings.smtp_from}"
    return settings.app_public_url


def vapid_keys(db: Session) -> tuple[ec.EllipticCurvePrivateKey, str]:
    """(private key, public key as base64url). ``VAPID_PRIVATE_KEY`` (raw,
    base64url) wins; else the platform row, created on first use."""
    from app.core.config import settings
    from app.core.secrets import decrypt_secret, encrypt_secret
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting

    env = getattr(settings, "vapid_private_key", None)
    if env:
        key = _private_from_raw(b64url_decode(env))
        return key, b64url(_public_bytes(key.public_key()))
    with tenant_bypass():
        row = db.execute(select(AppSetting).where(AppSetting.key == VAPID_KEY,
                                                  AppSetting.company_id.is_(None))).scalars().first()
        raw = b""
        if row is not None and row.value:
            try:
                raw = b64url_decode(decrypt_secret(json.loads(row.value).get("private", "")))
            except (ValueError, TypeError):
                raw = b""
        if len(raw) != 32:
            if row is not None and row.value:
                log.warning("stored VAPID key unreadable (AUTH_SECRET changed?) — new key; devices re-subscribe")
            key = ec.generate_private_key(ec.SECP256R1())
            value = json.dumps({"private": encrypt_secret(b64url(_raw_private(key))),
                                "public": b64url(_public_bytes(key.public_key()))})
            if row is None:
                db.add(AppSetting(key=VAPID_KEY, value=value, company_id=None))
            else:
                row.value = value
            db.flush()
            return key, b64url(_public_bytes(key.public_key()))
        key = _private_from_raw(raw)
        return key, b64url(_public_bytes(key.public_key()))


# --- subscriptions ---------------------------------------------------------------------------------

def endpoint_allowed(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and not parts.username and any(
        host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


def _client():
    import httpx
    return httpx.Client(timeout=10)


def send(db: Session, sub, message: dict[str, Any], *, client=None) -> str:
    """Push one message to one subscription: "sent", "gone" (the device
    unsubscribed — the row is removed) or "failed"."""
    key, _public = vapid_keys(db)
    body = encrypt(json.dumps(message, ensure_ascii=False).encode("utf-8"), sub.p256dh, sub.auth)
    headers = {"Authorization": vapid_authorization(sub.endpoint, key, subject()),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
               "TTL": str(TTL_SECONDS), "Urgency": "normal"}
    own = client is None
    client = client or _client()
    try:
        res = client.post(sub.endpoint, content=body, headers=headers)
    except Exception as exc:  # noqa: BLE001 — a push service outage must not stop the job
        log.warning("push failed endpoint_host=%s error=%r", urlsplit(sub.endpoint).hostname, exc)
        sub.failures = int(sub.failures or 0) + 1
        return "failed"
    finally:
        if own:
            client.close()
    if res.status_code in (404, 410):
        db.delete(sub)
        return "gone"
    if res.status_code >= 400:
        log.warning("push refused status=%s endpoint_host=%s", res.status_code, urlsplit(sub.endpoint).hostname)
        sub.failures = int(sub.failures or 0) + 1
        return "failed"
    sub.failures = 0
    sub.last_success_at = datetime.now(timezone.utc)
    return "sent"


def _message(row) -> dict[str, Any]:
    import re
    page = row.link_page if row.link_page and re.fullmatch(r"[a-z0-9-]{1,40}", row.link_page) else None
    return {"title": (row.title or "")[:120], "body": (row.message or "")[:300], "page": page,
            "tag": (row.dedupe_key or str(row.id))[:64]}


def deliver_pending(db: Session, *, client=None) -> dict[str, int]:
    """Push every new, open alert of the current company to the devices of the
    people who would see it in the bell, then mark it pushed. More than a few
    at once for one person become one summary push."""
    from app.db.tenant import get_current_company
    from app.models.notification import Notification
    from app.models.push_subscription import PushSubscription
    from app.models.user import User
    from app.services.notification_service import visible_to

    now = datetime.now(timezone.utc)
    rows = db.execute(select(Notification).where(Notification.pushed_at.is_(None))
                      .order_by(Notification.created_at)).scalars().all()
    stats = {"alerts": len(rows), "sent": 0, "gone": 0, "failed": 0}
    if not rows:
        return stats
    open_rows = [r for r in rows if r.dismissed_at is None and r.read_at is None]
    subs = db.execute(select(PushSubscription)).scalars().all()
    if subs and open_rows:
        cid = get_current_company()
        users = db.execute(select(User.id, User.role).where(
            User.company_id == uuid.UUID(str(cid)), User.is_active.is_(True))).all() if cid else []
        by_user: dict[str, list] = {}
        for r in open_rows:
            for uid, role in users:
                if visible_to(r, user_id=str(uid), role=role or "owner"):
                    by_user.setdefault(str(uid), []).append(r)
        for uid, alerts in by_user.items():
            devices = [s for s in subs if s.user_id == uid]
            if not devices:
                continue
            if len(alerts) > MAX_PER_USER_EACH_TICK:
                messages = [{"title": f"{len(alerts)} new alerts", "body": "; ".join(a.title for a in alerts[:3])[:300],
                             "page": None, "tag": "summary"}]
            else:
                messages = [_message(a) for a in alerts]
            for dev in devices:
                for msg in messages:
                    outcome = send(db, dev, msg, client=client)
                    stats[outcome] += 1
                    if outcome == "gone":
                        break
    for r in rows:
        r.pushed_at = now
    db.flush()
    return stats
