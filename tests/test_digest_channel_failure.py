"""One operator channel down must not repeat the others (review 2026-11-28,
scenario CV6).

The scheduler sets a daily job's marker only after the job returns. Slack
took the digest, then Telegram raised a network error: no marker, and every
tick (a minute) sent the digest to Slack again until the day ended."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

import httpx
import pytest

from app.api import notifications
from app.core.config import settings
from app.db.tenant import use_company
from app.jobs import scheduler as sched
from app.models.company import Company
from app.services.digest_service import set_digest_settings


@pytest.fixture()
def channels(db, monkeypatch):
    """Slack answers, Telegram is unreachable; one active company with the
    digest on every channel."""
    from app.db import session as db_session
    from app.services import mail_service
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)
    posted, mailed = [], []

    class _Http:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            if "telegram" in url:
                raise httpx.ConnectError("telegram unreachable")
            posted.append(url)
            return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(notifications.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(notifications, "_operator_channels_open", lambda: True)
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.example/ops", raising=False)
    monkeypatch.setattr(settings, "telegram_bot_token", "tok", raising=False)
    monkeypatch.setattr(settings, "telegram_chat_id", "42", raising=False)
    monkeypatch.setattr(settings, "smtp_to", "ops@example.com", raising=False)
    monkeypatch.setattr(mail_service, "send_email", lambda **kw: mailed.append(kw) or True)
    co = Company(id=uuid.uuid4(), name="Digest Co", slug=f"dg-{uuid.uuid4().hex[:6]}", locale="uk",
                 base_currency="GBP", status="active", token_version=0)
    db.add(co)
    db.commit()
    cid = str(co.id)
    with use_company(cid):
        set_digest_settings(db, enabled=True, channel="all")
        db.commit()
    monkeypatch.setattr(sched, "_active_company_ids", lambda db: [cid])
    sched.STATUS.clear()
    return cid, posted, mailed


def test_cv6_a_failing_channel_is_logged_and_the_rest_go_once(channels, caplog):
    """CV6: the job finishes with Slack and e-mail delivered, the day's marker
    is set, and the next ticks send nothing again."""
    cid, posted, mailed = channels
    today = datetime(2026, 11, 28, 9, 0).date()
    with caplog.at_level(logging.WARNING):
        st = sched.run_job_for_all_companies("daily_digest", sched.job_daily_digest, today=today, once_per_day=True)
    assert st.companies_ok == 1 and st.companies_failed == 0
    assert st.detail[cid]["delivered"] == ["slack", "email"]
    assert "operator_channel_failed channel=telegram" in caplog.text
    for _ in range(3):                                              # the next minutes' ticks
        sched.run_job_for_all_companies("daily_digest", sched.job_daily_digest, today=today, once_per_day=True)
    assert posted == ["https://hooks.example/ops"] and len(mailed) == 1


def test_cv6_a_dead_channel_answers_false_and_never_raises(channels):
    """CV6: the senders the digest endpoint and the alert check share say a
    channel failed instead of raising (which was a 500 there)."""
    import asyncio
    with use_company(channels[0]):
        assert asyncio.run(notifications._send_telegram("x")) is False
        assert asyncio.run(notifications._send_slack("x")) is True
