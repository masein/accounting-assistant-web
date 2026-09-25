"""Two-factor sign-in with an authenticator app (roadmap 2026-09 §1.13).

TOTP per RFC 6238 — SHA-1, six digits, 30-second steps — which is what every
authenticator app (Google Authenticator, Microsoft Authenticator, 1Password,
Aegis …) expects by default. Stdlib only:

* the shared secret is stored encrypted (``app.core.secrets``), never plain;
* a code is accepted for the current step and one step either side (clock
  drift), and never twice — the last accepted step is remembered, so a code
  read over a shoulder cannot be replayed within its 90-second life;
* ten one-time recovery codes, stored as keyed hashes, for a lost phone;
* between the password and the code the browser holds a signed five-minute
  challenge, not a session: it is signed with a key derived for this purpose
  only, so it can never be presented as a session cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

from app.core.config import settings

ISSUER = "Accounting Assistant"
DIGITS = 6
STEP_SECONDS = 30
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
CHALLENGE_SECONDS = 300


# --- TOTP ----------------------------------------------------------------------

def new_secret() -> str:
    """160 random bits, base32 without padding (RFC 4226 recommends ≥128)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _clock() -> float:
    return time.time()  # one seam for tests to move time


def current_step(now: float | None = None) -> int:
    return int((_clock() if now is None else now) // STEP_SECONDS)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def normalize_code(code: str | None) -> str:
    """Accept '123 456', '123-456' and Persian/Arabic-Indic digits."""
    raw = (code or "").strip()
    raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return "".join(ch for ch in raw if ch not in " -")


def verify_totp(secret: str, code: str | None, *, last_step: int | None = None,
                now: float | None = None) -> int | None:
    """The step the code belongs to, or None. A step at or before
    ``last_step`` (already used) is refused."""
    code = normalize_code(code)
    if len(code) != DIGITS or not code.isdigit() or not secret:
        return None
    here = current_step(now)
    for step in range(here - DRIFT_STEPS, here + DRIFT_STEPS + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def was_already_used(secret: str, code: str | None, *, last_step: int | None,
                     now: float | None = None) -> bool:
    """True when the code is genuine but its step was already accepted — the
    user should wait for the next code, not re-check their clock."""
    if last_step is None:
        return False
    code = normalize_code(code)
    if len(code) != DIGITS or not code.isdigit() or not secret:
        return False
    here = current_step(now)
    return any(hmac.compare_digest(code_at(secret, step), code)
               for step in range(here - DRIFT_STEPS, here + DRIFT_STEPS + 1) if step <= last_step)


def provisioning_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


def qr_svg(text: str, *, module: int = 4, border: int = 4) -> str:
    """A compact SVG QR code (one path) — reportlab already ships the encoder,
    so no new dependency and no third-party service ever sees the secret."""
    from reportlab.graphics.barcode import qrencoder

    qr = None
    for version in range(1, 41):
        try:
            candidate = qrencoder.QRCode(version, qrencoder.QRErrorCorrectLevel.M)
            candidate.addData(text)
            candidate.make()
            qr = candidate
            break
        except Exception:  # data does not fit this version — try the next
            continue
    if qr is None:
        raise ValueError("text too long for a QR code")
    n = qr.getModuleCount()
    size = (n + 2 * border) * module
    parts = []
    for r in range(n):
        for c in range(n):
            if qr.isDark(r, c):
                parts.append(f"M{(c + border) * module} {(r + border) * module}h{module}v{module}h-{module}z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'width="{size}" height="{size}" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="#fff"/>'
            f'<path fill="#000" d="{"".join(parts)}"/></svg>')


# --- recovery codes ---------------------------------------------------------------

_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def _recovery_hash(code: str) -> str:
    key = hashlib.sha256(("aa-2fa-recovery|" + (settings.auth_secret or "")).encode()).digest()
    return hmac.new(key, normalize_recovery(code).encode(), hashlib.sha256).hexdigest()


def normalize_recovery(code: str | None) -> str:
    return "".join(ch for ch in (code or "").strip().lower() if ch.isalnum())


def new_recovery_codes(n: int = RECOVERY_CODE_COUNT) -> tuple[list[str], str]:
    """(plain codes to show once, JSON of their hashes to store)."""
    codes = []
    for _ in range(n):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_recovery_hash(c) for c in codes])


def recovery_codes_left(stored: str | None) -> int:
    try:
        return len(json.loads(stored or "[]"))
    except ValueError:
        return 0


def use_recovery_code(stored: str | None, code: str | None) -> str | None:
    """The stored JSON with ``code`` removed, or None when it is not a live
    recovery code. Every stored hash is compared (no early exit)."""
    if len(normalize_recovery(code)) != 10:
        return None
    try:
        hashes = list(json.loads(stored or "[]"))
    except ValueError:
        return None
    probe = _recovery_hash(code)
    match = None
    for h in hashes:
        if hmac.compare_digest(str(h), probe):
            match = h
    if match is None:
        return None
    hashes.remove(match)
    return json.dumps(hashes)


# --- the password-ok, code-pending challenge ----------------------------------------

def _challenge_key() -> bytes:
    return hashlib.sha256(("aa-2fa-challenge|" + (settings.auth_secret or "")).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_challenge(*, user_id: str, token_version: int, must_change_password: bool = False,
                    now: float | None = None) -> str:
    now = int(_clock() if now is None else now)
    body = _b64(json.dumps({"p": "2fa", "uid": str(user_id), "tv": int(token_version),
                            "pwc": bool(must_change_password), "exp": now + CHALLENGE_SECONDS},
                           separators=(",", ":")).encode())
    sig = hmac.new(_challenge_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"2fa.{body}.{sig}"


def parse_challenge(token: str | None, *, now: float | None = None) -> dict | None:
    if not token or not token.startswith("2fa.") or token.count(".") != 2:
        return None
    _, body, sig = token.split(".")
    expected = hmac.new(_challenge_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        return None
    if data.get("p") != "2fa" or int(data.get("exp", 0)) <= int(_clock() if now is None else now):
        return None
    return data
