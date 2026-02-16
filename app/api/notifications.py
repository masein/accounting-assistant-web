from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.reports import get_owner_dashboard
from app.core.config import settings
from app.db.session import get_db
from app.schemas.notification import NotificationCheckResponse, NotificationItem

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
    dashboard = get_owner_dashboard(db)
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
