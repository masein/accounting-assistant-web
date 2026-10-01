"""Two feeds refreshed at the same moment don't make the second one fail:
both found a notification missing and both added it, and the second commit
hit the (company, dedupe_key) constraint — an intermittent 500 on
/notifications/feed (seen in the browser suite: the page and its service
worker load the feed together)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.tenant import use_company
from app.models.company import Company
from app.models.invoice import Invoice
from app.models.notification import Notification
from app.services import notification_service as svc


@pytest.fixture()
def company(db):
    c = Company(id=uuid.uuid4(), name="Race Co", slug=f"race-{uuid.uuid4().hex[:8]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    yield c
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def test_a_refresh_that_loses_the_race_redoes_its_work(db, company, monkeypatch):
    with use_company(company.id):
        inv = Invoice(number=f"RACE-{uuid.uuid4().hex[:6]}", kind="sales", status="issued",
                      issue_date=date.today() - timedelta(days=40), due_date=date.today() - timedelta(days=10), amount=1000)
        db.add(inv)
        db.commit()
        key = f"inv-{inv.id}-overdue"
        svc.refresh_notifications(db)                      # the other request got there first
        count = lambda: db.execute(select(func.count()).select_from(Notification)  # noqa: E731
                                   .where(Notification.dedupe_key == key)).scalar()
        assert count() == 1

        # this one looked before that row existed: it adds the same notification again, once
        real_upsert, state = svc._upsert, {"blind": True}

        def raced(db_, seen, *, dedupe_key, **kw):
            if state["blind"] and dedupe_key == key:
                state["blind"] = False
                seen.add(dedupe_key)
                db_.add(Notification(dedupe_key=dedupe_key, **{k: v for k, v in kw.items() if k != "user_id"}))
                return
            real_upsert(db_, seen, dedupe_key=dedupe_key, **kw)

        monkeypatch.setattr(svc, "_upsert", raced)
        assert svc.refresh_notifications(db) >= 1          # no IntegrityError reaches the caller
        db.expire_all()
        assert count() == 1 and state["blind"] is False
        row = db.execute(select(Notification).where(Notification.dedupe_key == key)).scalar_one()
        assert row.dismissed_at is None and inv.number in row.title
