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
