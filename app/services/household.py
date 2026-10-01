"""A shared household: more than one person on one set of personal books (roadmap §4.12).

A member of a personal company invites someone. The invite is a random token —
only its SHA-256 is kept — that works once, for seven days, and only while the
household has room (six people). The invitee signs up through the link
(``POST /auth/signup`` with ``invite``) and becomes another personal user of
the same company: same books, same budgets and goals; the audit trail says
who recorded what. With mail configured and an address given, the link is
e-mailed (and clicking it proves the address); otherwise the inviter copies it.

Members can remove each other (the account is deactivated and its sessions
end) but not themselves, so a household never locks itself out.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.household_invite import HouseholdInvite

MAX_MEMBERS = 6
TTL = timedelta(days=7)


class HouseholdError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt   # SQLite is naive


def _company(db: Session, company_id):
    from app.models.company import Company
    return db.get(Company, uuid.UUID(str(company_id))) if company_id else None


def require_personal(db: Session, company_id) -> None:
    c = _company(db, company_id)
    if c is None or (c.kind or "") != "personal":
        raise HouseholdError("A household is for personal books.", 422)


def _members(db: Session, company_id) -> list:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    with tenant_bypass():
        return list(db.execute(select(User).where(User.company_id == uuid.UUID(str(company_id)))
                               .order_by(User.created_at)).scalars())


def _pending(db: Session, company_id) -> list[HouseholdInvite]:
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        rows = db.execute(select(HouseholdInvite).where(
            HouseholdInvite.company_id == uuid.UUID(str(company_id)), HouseholdInvite.accepted_at.is_(None),
            HouseholdInvite.revoked_at.is_(None))).scalars().all()
    return [r for r in rows if _aware(r.expires_at) > _now()]


def overview(db: Session, company_id, *, me: str | None = None) -> dict:
    require_personal(db, company_id)
    members = [{"id": str(u.id), "username": u.username, "active": bool(u.is_active), "you": str(u.id) == str(me),
                "joined": u.created_at.isoformat() if getattr(u, "created_at", None) else None}
               for u in _members(db, company_id)]
    invites = [{"id": str(i.id), "name": i.name, "email": i.email, "emailed": i.emailed_at is not None,
                "expires_at": _aware(i.expires_at).isoformat()} for i in _pending(db, company_id)]
    active = sum(1 for m in members if m["active"])
    return {"members": members, "invites": invites, "limit": MAX_MEMBERS,
            "room": max(0, MAX_MEMBERS - active - len(invites))}


def create_invite(db: Session, company_id, *, invited_by: str, name: str | None = None,
                  email: str | None = None) -> tuple[HouseholdInvite, str]:
    require_personal(db, company_id)
    active = sum(1 for u in _members(db, company_id) if u.is_active)
    if active + len(_pending(db, company_id)) >= MAX_MEMBERS:
        raise HouseholdError(f"A household has room for {MAX_MEMBERS} people — remove someone or cancel an invite first.",
                             409)
    email = (email or "").strip() or None
    if email and ("@" not in email or email.startswith("@") or email.endswith("@") or len(email) > 254):
        raise HouseholdError("That e-mail address doesn't look right.")
    token = secrets.token_urlsafe(32)
    inv = HouseholdInvite(company_id=uuid.UUID(str(company_id)), token_hash=_hash(token),
                          name=(name or "").strip()[:128] or None, email=email, invited_by=str(invited_by),
                          expires_at=_now() + TTL)
    db.add(inv)
    db.flush()
    return inv, token


def link(token: str) -> str:
    from app.core.config import settings
    return f"{(settings.app_public_url or '').rstrip('/')}/login?invite={token}"


# in the inviter's language (en, fa, es, ar): they know who they are inviting
_INVITE = {
    "en": {"subject": "{inviter} invited you to share their books", "hello": "Hello{name},",
           "body": "{inviter} invited you to share the books “{books}”.", "join": "Join here (the link works once, for 7 days):",
           "button": "Join the household", "note": "If you weren't expecting this, you can ignore it.", "default": "the household"},
    "fa": {"subject": "{inviter} شما را به دفاتر خود دعوت کرده است", "hello": "سلام{name}،",
           "body": "{inviter} شما را به دفاتر «{books}» دعوت کرده است.", "join": "از این پیوند وارد شوید (یک بار و تا ۷ روز کار می‌کند):",
           "button": "پیوستن به خانوار", "note": "اگر منتظر این پیام نبودید، آن را نادیده بگیرید.", "default": "خانوار"},
    "es": {"subject": "{inviter} te invitó a compartir sus libros", "hello": "Hola{name}:",
           "body": "{inviter} te invitó a compartir los libros «{books}».", "join": "Únete aquí (el enlace funciona una vez, durante 7 días):",
           "button": "Unirme al hogar", "note": "Si no lo esperabas, puedes ignorarlo.", "default": "el hogar"},
    "ar": {"subject": "دعاك {inviter} لمشاركة دفاتره", "hello": "مرحباً{name}،",
           "body": "دعاك {inviter} لمشاركة دفاتر «{books}».", "join": "انضم من هنا (الرابط يعمل مرة واحدة لمدة 7 أيام):",
           "button": "الانضمام إلى الأسرة", "note": "إذا لم تكن تتوقع هذا فتجاهله.", "default": "الأسرة"},
}


def send_invite(db: Session, inv: HouseholdInvite, token: str, *, inviter: str, lang: str = "en") -> bool:
    """E-mail the link when the server can send mail and there's an address."""
    from html import escape

    from app.services.mail_service import mail_configured, send_email
    if not inv.email or not mail_configured():
        return False
    lang = lang if lang in _INVITE else "en"
    T = _INVITE[lang]
    books = (_company(db, inv.company_id).name if _company(db, inv.company_id) else "") or T["default"]
    url = link(token)
    hello = T["hello"].format(name=(" " + inv.name) if inv.name else "")
    body = T["body"].format(inviter=inviter, books=books)
    text = f"{hello}\n\n{body}\n\n{T['join']}\n\n{url}\n\n{T['note']}"
    html = (f"<div dir='{'rtl' if lang in ('fa', 'ar') else 'ltr'}'><p>{escape(hello)}</p><p>{escape(body)}</p>"
            f"<p><a href=\"{escape(url, quote=True)}\">{escape(T['button'])}</a></p>"
            f"<p style='color:#666;font-size:13px'>{escape(T['note'])}</p></div>")
    ok = send_email(to=inv.email, subject=T["subject"].format(inviter=inviter), text=text, html=html)
    if ok:
        inv.emailed_at = _now()
    return bool(ok)


def find(db: Session, token: str) -> HouseholdInvite:
    """The invite a link carries, if it can still be used."""
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        inv = db.execute(select(HouseholdInvite).where(HouseholdInvite.token_hash == _hash(token))).scalars().first()
    if inv is None or inv.revoked_at is not None:
        raise HouseholdError("This invitation isn't valid.", 404)
    if inv.accepted_at is not None:
        raise HouseholdError("This invitation has already been used.", 410)
    if _aware(inv.expires_at) <= _now():
        raise HouseholdError("This invitation has expired — ask for a new one.", 410)
    return inv


def describe(db: Session, token: str) -> dict:
    """What the sign-up page shows for a link — nothing about the books beyond their name."""
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    inv = find(db, token)
    c = _company(db, inv.company_id)
    with tenant_bypass():
        who = db.execute(select(User.username).where(User.id == uuid.UUID(inv.invited_by))).scalar() \
            if _is_uuid(inv.invited_by) else None
    return {"books": c.name if c else None, "invited_by": who, "name": inv.name, "email": inv.email,
            "expires_at": _aware(inv.expires_at).isoformat(), "locale": c.locale if c else None}


def _is_uuid(v) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


def accept(db: Session, token: str, *, username: str, password: str, email: str | None = None):
    """Create the invitee's account in the household. The caller commits."""
    from app.core.auth import hash_password, validate_password_strength
    from app.core.permissions import Role
    from app.core.release_notes import CURRENT_RELEASE
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    inv = find(db, token)
    require_personal(db, inv.company_id)
    active = sum(1 for u in _members(db, inv.company_id) if u.is_active)
    if active >= MAX_MEMBERS:
        raise HouseholdError("This household is full.", 409)
    username = (username or "").strip()
    try:
        validate_password_strength(password)
    except ValueError as e:
        raise HouseholdError(str(e), 400) from e
    with tenant_bypass():
        if db.execute(select(func.count(User.id)).where(User.username == username)).scalar():
            raise HouseholdError("That username is taken.", 400)
    ph, salt = hash_password(password)
    user = User(username=username, password_hash=ph, password_salt=salt, is_admin=False, is_superadmin=False,
                company_id=inv.company_id, is_active=True, role=Role.PERSONAL, last_seen_release=CURRENT_RELEASE)
    address = (email or "").strip() or inv.email
    if address:
        user.email = address
        if inv.emailed_at is not None and inv.email and address.lower() == inv.email.lower():
            user.email_verified_at = _now()             # the link reached this address
    db.add(user)
    inv.accepted_at = _now()
    db.flush()
    inv.accepted_user_id = str(user.id)
    return user, inv


def revoke(db: Session, company_id, invite_id) -> None:
    require_personal(db, company_id)
    inv = db.get(HouseholdInvite, uuid.UUID(str(invite_id)))
    if inv is None or str(inv.company_id) != str(company_id) or inv.accepted_at is not None:
        raise HouseholdError("No such invitation.", 404)
    inv.revoked_at = _now()


def remove_member(db: Session, company_id, user_id, *, me: str) -> None:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    require_personal(db, company_id)
    if str(user_id) == str(me):
        raise HouseholdError("You can't remove yourself — ask another member.", 400)
    with tenant_bypass():
        u = db.get(User, uuid.UUID(str(user_id)))
    if u is None or str(u.company_id) != str(company_id):
        raise HouseholdError("No such member.", 404)
    u.is_active = False
    u.token_version = int(u.token_version or 0) + 1                    # ends their sessions
