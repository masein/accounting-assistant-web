from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.api.reports import get_owner_dashboard
from app.core.config import settings
from app.db.session import get_db
from app.schemas.notification import NotificationCheckResponse, NotificationItem
from app.services.digest_service import (
    build_daily_digest,
    format_digest,
    get_digest_settings,
    set_digest_settings,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _fmt(items: list[NotificationItem]) -> str:
    if not items:
        return "No active alerts."
    return "\n".join(f"- [{i.level.upper()}] {i.title}: {i.message}" for i in items)


async def _send_slack(text: str) -> bool:
    if not settings.slack_webhook_url:
        return False
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(settings.slack_webhook_url, json={"text": text})
        return r.status_code < 300


async def _send_telegram(text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data={"chat_id": settings.telegram_chat_id, "text": text})
        return r.status_code < 300


def _send_email(text: str) -> bool:
    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password, settings.smtp_to]):
        return False
    msg = EmailMessage()
    msg["Subject"] = "Accounting Assistant Alerts"
    msg["From"] = settings.smtp_user
    msg["To"] = settings.smtp_to
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        s.starttls()
        s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)
    return True


@router.post("/check", response_model=NotificationCheckResponse)
async def check_notifications(
    deliver: bool = True,
    db: Session = Depends(get_db),
) -> NotificationCheckResponse:
    dashboard = get_owner_dashboard(currency=None, db=db)
    items = [NotificationItem(level=a.level, title=a.title, message=a.message) for a in dashboard.alerts]
    delivered: list[str] = []
    text = "Business alerts\n" + _fmt(items)
    if deliver and items:
        if await _send_slack(text):
            delivered.append("slack")
        if await _send_telegram(text):
            delivered.append("telegram")
        try:
            if _send_email(text):
                delivered.append("email")
        except Exception:
            pass
    return NotificationCheckResponse(items=items, delivered=delivered)


# ---------------------------------------------------------------------------
# Low-cash / daily digest (Owner + CFO). Company-scoped; safe content only.
# ---------------------------------------------------------------------------
class DigestSettingsPayload(BaseModel):
    enabled: bool | None = None
    cash_threshold: int | None = None
    runway_months: float | None = None
    channel: str | None = None


def _company_name(db: Session) -> str:
    from app.db.tenant import get_current_company, tenant_bypass
    from app.models.company import Company
    cid = get_current_company()
    if not cid:
        return "Company"
    import uuid
    try:
        with tenant_bypass():
            c = db.get(Company, uuid.UUID(str(cid)))
        return c.name if c else "Company"
    except Exception:
        return "Company"


@router.get("/digest-settings")
def read_digest_settings(db: Session = Depends(get_db)) -> dict:
    return get_digest_settings(db)


@router.put("/digest-settings")
def update_digest_settings(payload: DigestSettingsPayload, db: Session = Depends(get_db)) -> dict:
    from fastapi import HTTPException
    try:
        return set_digest_settings(
            db, enabled=payload.enabled, cash_threshold=payload.cash_threshold,
            runway_months=payload.runway_months, channel=payload.channel,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/daily-digest")
async def send_daily_digest(deliver: bool = True, db: Session = Depends(get_db)) -> dict:
    """Build the current company's cash-health digest and (optionally) deliver it
    to the configured channel. Meant to be triggered daily by an external
    scheduler with an Owner/CFO session. Skips delivery when disabled."""
    d = build_daily_digest(db)
    conf = d["settings"]
    text = format_digest(_company_name(db), d)
    delivered: list[str] = []
    if deliver and conf["enabled"]:
        ch = conf["channel"]
        if ch in ("all", "slack") and await _send_slack(text):
            delivered.append("slack")
        if ch in ("all", "telegram") and await _send_telegram(text):
            delivered.append("telegram")
        if ch in ("all", "email"):
            try:
                if _send_email(text):
                    delivered.append("email")
            except Exception:
                pass
    return {"digest": d, "body": text, "delivered": delivered, "enabled": conf["enabled"]}


# ---------------------------------------------------------------------------
# Persisted feed (the in-app bell) + user reminders
# ---------------------------------------------------------------------------
import uuid as _uuid
from datetime import date as _date, datetime as _dt, timezone as _tz

from fastapi import HTTPException
from sqlalchemy import select as _select

from app.core.auth import SessionUser, get_current_user
from app.models.notification import Notification, Reminder


class FeedItem(BaseModel):
    id: str
    kind: str
    level: str
    title: str
    message: str
    link_page: str | None = None
    due_date: str | None = None
    read: bool
    created_at: str


class ReminderPayload(BaseModel):
    title: str
    note: str | None = None
    due_date: _date
    repeat: str = "none"        # none|daily|weekly|monthly|yearly
    days_before: int = 3


class ReminderUpdate(BaseModel):
    title: str | None = None
    note: str | None = None
    due_date: _date | None = None
    repeat: str | None = None
    days_before: int | None = None
    status: str | None = None   # active|paused|done


class ReminderRead(BaseModel):
    id: str
    title: str
    note: str | None = None
    due_date: str
    repeat: str
    days_before: int
    status: str


_REPEATS = {"none", "daily", "weekly", "monthly", "yearly"}


@router.get("/feed", response_model=list[FeedItem])
def notifications_feed(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[FeedItem]:
    """Refresh + return the caller's visible notifications (undismissed,
    newest/most-urgent first). Personal rows (reminders, petty-cash decisions)
    are user-scoped; company rows are role-gated by kind."""
    from app.services.notification_service import refresh_notifications, visible_to

    refresh_notifications(db)
    rows = db.execute(
        _select(Notification).where(Notification.dismissed_at.is_(None))
        .order_by(Notification.level.desc(), Notification.due_date.nulls_last(),
                  Notification.created_at.desc())
    ).scalars().all()
    role = (user.role or "owner").lower()
    out = []
    for row in rows:
        if not visible_to(row, user_id=user.user_id, role=role):
            continue
        out.append(FeedItem(
            id=str(row.id), kind=row.kind, level=row.level, title=row.title,
            message=row.message, link_page=row.link_page,
            due_date=row.due_date.isoformat() if row.due_date else None,
            read=row.read_at is not None,
            created_at=row.created_at.isoformat() if row.created_at else "",
        ))
    level_rank = {"high": 0, "warning": 1, "info": 2}
    out.sort(key=lambda i: (level_rank.get(i.level, 3), i.due_date or "9999"))
    return out


@router.post("/feed/{notification_id}/read")
def mark_read(
    notification_id: _uuid.UUID,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    row = db.get(Notification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read_at = _dt.now(_tz.utc)
    db.commit()
    return {"ok": True}


@router.post("/feed/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> dict:
    from app.services.notification_service import visible_to

    rows = db.execute(
        _select(Notification).where(
            Notification.dismissed_at.is_(None), Notification.read_at.is_(None))
    ).scalars().all()
    role = (user.role or "owner").lower()
    now = _dt.now(_tz.utc)
    n = 0
    for row in rows:
        if visible_to(row, user_id=user.user_id, role=role):
            row.read_at = now
            n += 1
    db.commit()
    return {"ok": True, "marked": n}


# --- reminders (personal; any authenticated role) --------------------------

def _reminder_read(r: Reminder) -> ReminderRead:
    return ReminderRead(
        id=str(r.id), title=r.title, note=r.note, due_date=r.due_date.isoformat(),
        repeat=r.repeat, days_before=r.days_before, status=r.status,
    )


def _own_reminder(db: Session, user: SessionUser, reminder_id: _uuid.UUID) -> Reminder:
    row = db.get(Reminder, reminder_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if row.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not your reminder")
    return row


@router.get("/reminders", response_model=list[ReminderRead])
def list_reminders(
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> list[ReminderRead]:
    rows = db.execute(
        _select(Reminder).where(Reminder.user_id == user.user_id,
                                Reminder.status != "done")
        .order_by(Reminder.due_date)
    ).scalars().all()
    return [_reminder_read(r) for r in rows]


@router.post("/reminders", response_model=ReminderRead, status_code=201)
def create_reminder(
    payload: ReminderPayload,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ReminderRead:
    repeat = (payload.repeat or "none").lower()
    if repeat not in _REPEATS:
        raise HTTPException(status_code=400, detail=f"repeat must be one of {sorted(_REPEATS)}")
    row = Reminder(
        user_id=user.user_id, title=payload.title.strip()[:256],
        note=(payload.note or "").strip() or None, due_date=payload.due_date,
        repeat=repeat, days_before=max(0, min(payload.days_before, 60)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _reminder_read(row)


@router.patch("/reminders/{reminder_id}", response_model=ReminderRead)
def update_reminder(
    reminder_id: _uuid.UUID,
    payload: ReminderUpdate,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> ReminderRead:
    row = _own_reminder(db, user, reminder_id)
    if payload.title is not None:
        row.title = payload.title.strip()[:256]
    if payload.note is not None:
        row.note = payload.note.strip() or None
    if payload.due_date is not None:
        row.due_date = payload.due_date
    if payload.repeat is not None:
        if payload.repeat.lower() not in _REPEATS:
            raise HTTPException(status_code=400, detail=f"repeat must be one of {sorted(_REPEATS)}")
        row.repeat = payload.repeat.lower()
    if payload.days_before is not None:
        row.days_before = max(0, min(payload.days_before, 60))
    if payload.status is not None and payload.status in ("active", "paused", "done"):
        row.status = payload.status
    db.commit()
    return _reminder_read(row)


@router.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(
    reminder_id: _uuid.UUID,
    db: Session = Depends(get_db),
    user: SessionUser = Depends(get_current_user),
) -> None:
    row = _own_reminder(db, user, reminder_id)
    db.delete(row)
    db.commit()
