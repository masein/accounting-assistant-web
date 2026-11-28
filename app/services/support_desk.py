"""The support desk's side of the app (the owner, 2026-11-28, scenarios DA1–DA8).

A person writes to support from the web page's support button, the phone apps or the Telegram/Bale bot. The
server keeps the conversation (app/models/support.py) and nothing else: the desk — Claude in Nightshift's
support desk, on the owner's Mac, and the night shift that fixes what it reports — fetches the waiting messages
with its desk key and posts its replies (app/api/support.py, ``/desk/…``). The server holds only the key's hash,
and no Claude or AI credentials for support at all.

* **The person's side:** one conversation each; a message with screenshots and what the app knew when it was
  sent (the page, the app's version, the language, recent errors' request ids); at most ``DAILY_LIMIT``
  messages a day; the desk's replies, unread until seen — the support button's badge and the bell.
* **The desk's side:** ``pending`` hands over the waiting messages with who wrote them (their company, role,
  language, the books' kind and currency) and the conversation so far, and marks them taken; one taken and not
  answered for ``LEASE`` is offered again, so a desk that stopped half way loses nothing. ``reply`` answers,
  ``attachment`` gives a screenshot's bytes (only one attached to a support message), ``hello`` says the desk
  is online — the button shows "online" while it was seen in the last ``ONLINE_WINDOW``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.support import SupportMessage, SupportThread

SETTINGS_KEY = "support_desk"
DAILY_LIMIT = 30                     # a person's messages a day: the desk runs on the owner's Claude plan
LEASE = timedelta(minutes=10)        # a message taken and not answered is offered to the desk again after this
ONLINE_WINDOW = timedelta(minutes=3)  # the desk checks in every minute; "online" while seen within this
MAX_TEXT = 4000
CHANNELS = ("web", "android", "ios", "telegram", "bale")
_CONTEXT_KEYS = {"page": 64, "app_version": 64, "language": 8, "user_agent": 300, "screen": 32, "platform": 32}


class SupportLimit(Exception):
    """Over the day's messages."""


class SupportError(ValueError):
    """A message the desk can't take (empty, an attachment that isn't the person's)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# --- the desk key (platform settings) ------------------------------------------------------------------------

def _row(db: Session):
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    with tenant_bypass():
        return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY,
                                                   AppSetting.company_id.is_(None))).scalars().first()


def _settings(db: Session) -> dict[str, Any]:
    row = _row(db)
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _save(db: Session, value: dict[str, Any]) -> None:
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    with tenant_bypass():
        row = _row(db)
        if row is None:
            db.add(AppSetting(key=SETTINGS_KEY, value=json.dumps(value), company_id=None))
        else:
            row.value = json.dumps(value)
        db.flush()


def _hash(key: str) -> str:
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def new_desk_key(db: Session) -> str:
    """A fresh desk key, returned once; only its hash is kept. Replaces the old one."""
    key = "sd_" + secrets.token_urlsafe(32)
    _save(db, {"key_hash": _hash(key), "created_at": _now().isoformat(), "seen_at": None})
    return key


def turn_off(db: Session) -> None:
    _save(db, {})


def desk_key_ok(db: Session, key: str | None) -> bool:
    want = _settings(db).get("key_hash")
    return bool(want and key) and secrets.compare_digest(_hash(key or ""), want)


def hello(db: Session) -> None:
    value = _settings(db)
    value["seen_at"] = _now().isoformat()
    _save(db, value)


def status(db: Session) -> dict[str, Any]:
    value = _settings(db)
    seen = value.get("seen_at")
    online = False
    if seen:
        try:
            online = _now() - _utc(datetime.fromisoformat(seen)) <= ONLINE_WINDOW
        except ValueError:
            online = False
    return {"connected": bool(value.get("key_hash")), "created_at": value.get("created_at"), "seen_at": seen,
            "online": online}


# --- the person's side ---------------------------------------------------------------------------------------

def thread_of(db: Session, user_id: str, *, create: bool = False) -> SupportThread | None:
    uid = uuid.UUID(str(user_id))
    row = db.execute(select(SupportThread).where(SupportThread.user_id == uid)).scalars().first()
    if row is None and create:
        row = SupportThread(user_id=uid, channel="web")
        db.add(row)
        db.flush()
    return row


def _clean_context(context: dict | None) -> dict[str, Any]:
    """What the app sent about where the person was: known keys only, each cut to size, at most ten recent
    errors (method, path, status, request id)."""
    out: dict[str, Any] = {}
    for key, limit in _CONTEXT_KEYS.items():
        value = (context or {}).get(key)
        if isinstance(value, (str, int, float)):
            out[key] = str(value)[:limit]
    errors = []
    for e in ((context or {}).get("errors") or [])[:10]:
        if isinstance(e, dict):
            errors.append({k: str(e.get(k))[:200] for k in ("method", "path", "status", "request_id", "at", "detail")
                           if e.get(k) is not None})
    if errors:
        out["errors"] = errors
    return out


def _attachments_of_person(db: Session, ids: list[str] | None) -> list[str]:
    """The attachment ids that are this company's and not a voucher's (a screenshot uploaded for support)."""
    from app.models.transaction import TransactionAttachment
    out = []
    for raw in (ids or [])[:5]:
        try:
            aid = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            raise SupportError("That attachment isn't one of yours.") from None
        row = db.get(TransactionAttachment, aid)
        if row is None or row.transaction_id is not None:
            raise SupportError("That attachment isn't one of yours.")
        out.append(str(aid))
    return out


def sent_today(db: Session, thread: SupportThread) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.execute(select(func.count(SupportMessage.id)).where(
        SupportMessage.thread_id == thread.id, SupportMessage.author == "user",
        SupportMessage.created_at >= start)).scalar() or 0)


def post(db: Session, *, user_id: str, text: str, attachment_ids: list[str] | None = None,
         context: dict | None = None, channel: str = "web") -> SupportMessage:
    """A person's message to the desk. Raises SupportLimit, SupportError. The caller commits."""
    text = (text or "").strip()[:MAX_TEXT]
    attachments = _attachments_of_person(db, attachment_ids)
    if not text and not attachments:
        raise SupportError("Write a message or attach a screenshot.")
    thread = thread_of(db, user_id, create=True)
    if sent_today(db, thread) >= DAILY_LIMIT:
        raise SupportLimit(f"You've sent {DAILY_LIMIT} messages to support today. The desk has them all; "
                           "write again tomorrow.")
    channel = channel if channel in CHANNELS else "web"
    now = _now()
    thread.channel, thread.last_user_at = channel, now
    msg = SupportMessage(thread_id=thread.id, author="user", channel=channel, text=text,
                         attachment_ids=attachments or None, context=_clean_context(context) or None, created_at=now)
    db.add(msg)
    db.flush()
    return msg


def message_dict(m: SupportMessage) -> dict[str, Any]:
    return {"id": str(m.id), "author": m.author, "channel": m.channel, "text": m.text,
            "attachments": list(m.attachment_ids or []),
            "at": _utc(m.created_at).isoformat() if m.created_at else None,
            "seen": m.seen_at is not None if m.author == "desk" else None}


def conversation(db: Session, user_id: str, *, after: datetime | None = None, limit: int = 100) -> dict[str, Any]:
    """The person's conversation (the newest ``limit``, or those after ``after``), its unread count, and whether
    the desk is online."""
    thread = thread_of(db, user_id)
    out: dict[str, Any] = {"thread_id": str(thread.id) if thread else None, "messages": [], "unread": 0,
                           "desk": {"online": status(db)["online"]}, "daily_limit": DAILY_LIMIT}
    if thread is None:
        return out
    q = select(SupportMessage).where(SupportMessage.thread_id == thread.id)
    if after is not None:
        q = q.where(SupportMessage.created_at > after)
    rows = db.execute(q.order_by(SupportMessage.created_at.desc()).limit(max(1, min(limit, 200)))).scalars().all()
    out["messages"] = [message_dict(m) for m in reversed(rows)]
    out["unread"] = unread(db, user_id)
    return out


def unread(db: Session, user_id: str) -> int:
    thread = thread_of(db, user_id)
    if thread is None:
        return 0
    return int(db.execute(select(func.count(SupportMessage.id)).where(
        SupportMessage.thread_id == thread.id, SupportMessage.author == "desk",
        SupportMessage.seen_at.is_(None))).scalar() or 0)


def mark_seen(db: Session, user_id: str) -> int:
    """The person read the desk's replies: the badge and the bell's support line go."""
    thread = thread_of(db, user_id)
    if thread is None:
        return 0
    rows = db.execute(select(SupportMessage).where(SupportMessage.thread_id == thread.id,
                                                   SupportMessage.author == "desk",
                                                   SupportMessage.seen_at.is_(None))).scalars().all()
    now = _now()
    for m in rows:
        m.seen_at = now
    _clear_bell(db, thread)
    return len(rows)


# --- the bell: an unread reply stays a line in the bell until it is read ---------------------------------------

_BELL_TITLE = {"en": "Support replied", "fa": "پشتیبانی پاسخ داد", "es": "Soporte respondió", "ar": "رد الدعم"}


def bell_key(thread: SupportThread) -> str:
    return f"support-{thread.id}"


def bell_lines(db: Session) -> list[dict[str, Any]]:
    """For the bell's refresh (app/services/notification_service.py): one line per person with an unread reply,
    the newest reply's first words — kept while unread, cleared once read."""
    rows = db.execute(select(SupportThread, SupportMessage).join(
        SupportMessage, and_(SupportMessage.thread_id == SupportThread.id, SupportMessage.author == "desk",
                             SupportMessage.seen_at.is_(None))).order_by(SupportMessage.created_at)).all()
    latest: dict[uuid.UUID, tuple[SupportThread, SupportMessage]] = {}
    for thread, msg in rows:
        latest[thread.id] = (thread, msg)
    return [{"dedupe_key": bell_key(t), "user_id": str(t.user_id), "snippet": (m.text or "")[:160]}
            for t, m in latest.values()]


def _ring(db: Session, thread: SupportThread, text: str) -> None:
    from app.services.notification_service import _store
    _store(db, {bell_key(thread): dict(kind="support", level="info", title=_BELL_TITLE["en"],
                                       message=(text or "")[:160], link_page="support", due_date=None,
                                       user_id=str(thread.user_id), text_key="support_reply",
                                       params={"snippet": (text or "")[:160]})})


def _clear_bell(db: Session, thread: SupportThread) -> None:
    from app.models.notification import Notification
    row = db.execute(select(Notification).where(Notification.dedupe_key == bell_key(thread))).scalars().first()
    if row is not None and row.dismissed_at is None:
        row.dismissed_at = _now()


# --- the desk's side (across companies: the desk serves them all) ---------------------------------------------

def pending(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """The people's messages waiting for the desk, oldest first, each with who wrote it and the conversation so
    far; marked taken. One taken more than ``LEASE`` ago and not answered since is offered again."""
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    from app.models.user import User
    now = _now()
    with tenant_bypass():
        stale = now - LEASE
        rows = db.execute(select(SupportMessage).where(
            SupportMessage.author == "user",
            (SupportMessage.desk_taken_at.is_(None)) | (SupportMessage.desk_taken_at < stale),
        ).order_by(SupportMessage.created_at).limit(max(1, min(limit, 50)) * 3)).scalars().all()
        out: list[dict[str, Any]] = []
        for m in rows:
            thread = db.get(SupportThread, m.thread_id)
            if thread is None:
                continue
            if m.desk_taken_at is not None and thread.last_desk_at is not None and \
                    _utc(thread.last_desk_at) >= _utc(m.desk_taken_at):
                continue                                      # answered after it was taken: done
            user = db.get(User, thread.user_id)
            company = db.get(Company, thread.company_id) if thread.company_id else None
            history = db.execute(select(SupportMessage).where(SupportMessage.thread_id == thread.id,
                                                              SupportMessage.created_at < m.created_at)
                                 .order_by(SupportMessage.created_at.desc()).limit(20)).scalars().all()
            m.desk_taken_at = now
            out.append({
                "message": message_dict(m), "context": m.context or {},
                "thread_id": str(thread.id),
                "person": {
                    "user_id": str(thread.user_id), "username": user.username if user else None,
                    "role": (user.role if user else None) or "owner",
                    "language": ((user.preferred_language if user else None) or "en"),
                    "email": user.email if user else None,
                },
                "company": None if company is None else {
                    "id": str(company.id), "name": company.name, "kind": getattr(company, "kind", None),
                    "locale": company.locale, "base_currency": company.base_currency, "status": company.status,
                },
                "history": [message_dict(h) for h in reversed(history)],
            })
            if len(out) >= limit:
                break
        db.commit()
    return out


def reply(db: Session, *, thread_id: str, text: str) -> SupportMessage:
    """The desk answers a conversation: the person sees it in the support panel, and a line in the bell (and a
    push to their devices). Raises SupportError."""
    from app.db.tenant import tenant_bypass, use_company
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        raise SupportError("An empty reply.")
    try:
        tid = uuid.UUID(str(thread_id))
    except (TypeError, ValueError):
        raise SupportError("No such conversation.") from None
    with tenant_bypass():
        thread = db.get(SupportThread, tid)
    if thread is None:
        raise SupportError("No such conversation.")
    with use_company(str(thread.company_id)) if thread.company_id else tenant_bypass():
        now = _now()
        msg = SupportMessage(thread_id=thread.id, author="desk", channel="desk", text=text, created_at=now)
        db.add(msg)
        thread = db.get(SupportThread, tid)
        thread.last_desk_at = now
        _ring(db, thread, text)
        db.commit()
        db.refresh(msg)
        _push_now(db)
    return msg


def _push_now(db: Session) -> None:
    """The bell's new line to the person's devices now rather than at the next quarter-hour (best effort)."""
    try:
        from app.services import web_push
        web_push.deliver_pending(db)
        db.commit()
    except Exception:  # noqa: BLE001 — the reply is kept either way; the scheduler pushes it later
        db.rollback()


def attachment(db: Session, attachment_id: str):
    """A screenshot a person attached to a support message (the desk reads it); None for anything else."""
    from app.db.tenant import tenant_bypass
    from app.models.transaction import TransactionAttachment
    try:
        aid = uuid.UUID(str(attachment_id))
    except (TypeError, ValueError):
        return None
    with tenant_bypass():
        row = db.get(TransactionAttachment, aid)
        if row is None or row.transaction_id is not None:
            return None
        rows = db.execute(select(SupportMessage.attachment_ids).where(SupportMessage.author == "user",
                                                                      SupportMessage.attachment_ids.isnot(None))).all()
    if not any(str(aid) in (ids or []) for (ids,) in rows):
        return None
    return row
