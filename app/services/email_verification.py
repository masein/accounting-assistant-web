"""Email verification for self-signup.

The rule is deliberately conditional: **verification is required only when the
server can actually send mail.** With no SMTP configured there is no way for a
user to confirm anything, so demanding it would lock them out of an account
they just created — worse than not verifying. Air-gapped installs therefore
sign people straight in, exactly as before this existed.

A pending ``verification_token`` is what gates login, so users provisioned by a
super-admin (who have no token and often no address) are untouched.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.services.mail_service import mail_configured, send_email

logger = logging.getLogger(__name__)

TOKEN_TTL_HOURS = 24


def verification_required() -> bool:
    return mail_configured()


def issue_token(user: User) -> str:
    user.verification_token = secrets.token_urlsafe(32)[:64]
    user.verification_sent_at = datetime.now(timezone.utc)
    return user.verification_token


def _verify_url(token: str) -> str:
    base = (settings.app_public_url or "").rstrip("/")
    return f"{base}/auth/verify?token={token}"


# in the language the person signed up in (en, fa, es, ar)
_VERIFY = {
    "en": {"subject": "Confirm your email address", "hello": "Hello {name},",
           "ask": "Confirm your email address to finish setting up your account:", "button": "Confirm my email",
           "note": "The link is valid for {hours} hours. If you did not create this account, you can ignore this message."},
    "fa": {"subject": "نشانی ایمیل خود را تأیید کنید", "hello": "سلام {name}،",
           "ask": "برای تکمیل ساخت حساب، نشانی ایمیل خود را تأیید کنید:", "button": "تأیید ایمیل",
           "note": "این پیوند {hours} ساعت معتبر است. اگر این حساب را شما نساخته‌اید، این پیام را نادیده بگیرید."},
    "es": {"subject": "Confirma tu dirección de correo", "hello": "Hola, {name}:",
           "ask": "Confirma tu dirección de correo para terminar de configurar tu cuenta:", "button": "Confirmar mi correo",
           "note": "El enlace es válido durante {hours} horas. Si no creaste esta cuenta, puedes ignorar este mensaje."},
    "ar": {"subject": "أكّد عنوان بريدك الإلكتروني", "hello": "مرحباً {name}،",
           "ask": "أكّد عنوان بريدك الإلكتروني لإكمال إعداد حسابك:", "button": "تأكيد بريدي",
           "note": "الرابط صالح لمدة {hours} ساعة. إذا لم تنشئ هذا الحساب فتجاهل هذه الرسالة."},
}


def send_verification_email(user: User) -> bool:
    from html import escape

    if not user.email or not user.verification_token:
        return False
    lang = (user.preferred_language or "en").strip().lower()
    T = _VERIFY.get(lang, _VERIFY["en"])
    link = _verify_url(user.verification_token)
    hello, note = T["hello"].format(name=user.username), T["note"].format(hours=TOKEN_TTL_HOURS)
    text = f"{hello}\n\n{T['ask']}\n\n{link}\n\n{note}"
    html = (
        f"<div dir='{'rtl' if lang in ('fa', 'ar') else 'ltr'}'>"
        f"<p>{escape(hello)}</p><p>{escape(T['ask'])}</p>"
        f'<p><a href="{escape(link, quote=True)}">{escape(T["button"])}</a></p>'
        f"<p style='color:#666;font-size:13px'>{escape(note)}</p></div>"
    )
    return send_email(to=user.email, subject=T["subject"], text=text, html=html)


def token_is_expired(user: User) -> bool:
    if user.verification_sent_at is None:
        return False
    sent = user.verification_sent_at
    if sent.tzinfo is None:  # SQLite hands back naive datetimes
        sent = sent.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - sent > timedelta(hours=TOKEN_TTL_HOURS)


def consume_token(db: Session, token: str) -> tuple[bool, str]:
    """Mark the matching user verified. Returns (ok, reason)."""
    token = (token or "").strip()
    if not token:
        return False, "Missing verification token."
    user = db.execute(
        select(User).where(User.verification_token == token)
    ).scalars().first()
    if user is None:
        # Also covers a token already used — the row no longer carries it.
        return False, "This link is invalid or has already been used."
    if token_is_expired(user):
        return False, "This link has expired. Request a new one."

    user.email_verified_at = datetime.now(timezone.utc)
    user.verification_token = None
    db.commit()
    logger.info("email verified for user %s", user.username)
    return True, "Email confirmed. You can sign in now."


def awaiting_verification(user: User) -> bool:
    """True when this account still has to confirm before signing in."""
    return bool(getattr(user, "verification_token", None))
