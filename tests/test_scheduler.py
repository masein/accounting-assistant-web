"""In-process scheduler (roadmap 2026-09 §2.1): recurring postings, feed
refresh and the daily digest run for every active company without a browser,
once per day where that matters, and one tenant's failure never stops the rest."""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.jobs import scheduler as sched
from app.models.company import Company


@pytest.fixture()
def two_companies(db, monkeypatch):
    from app.db import session as db_session
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)  # jobs open their own sessions
    a = Company(id=uuid.uuid4(), name="A", slug=f"a-{uuid.uuid4().hex[:6]}", locale="ir", base_currency="IRR", status="active", token_version=0)
    b = Company(id=uuid.uuid4(), name="B", slug=f"b-{uuid.uuid4().hex[:6]}", locale="uk", base_currency="GBP", status="active", token_version=0)
    s = Company(id=uuid.uuid4(), name="S", slug=f"s-{uuid.uuid4().hex[:6]}", locale="ir", base_currency="IRR", status="suspended", token_version=0)
    db.add_all([a, b, s]); db.commit()
    sched.STATUS.clear()
    sched._last_refresh_tick = None
    return a, b, s


def test_jobs_run_per_active_company_and_daily_ones_only_once(two_companies, monkeypatch):
    a, b, s = two_companies
    seen: dict[str, list[str]] = {"recurring": [], "refresh": [], "digest": [], "reminders": [], "rinv": []}
    from app.db.tenant import get_current_company
    monkeypatch.setattr(sched, "job_recurring_run_due", lambda db, today: seen["recurring"].append(get_current_company()) or {"posted": 0})
    monkeypatch.setattr(sched, "job_notifications_refresh", lambda db, today: seen["refresh"].append(get_current_company()) or {"created": 0})
    monkeypatch.setattr(sched, "job_daily_digest", lambda db, today: seen["digest"].append(get_current_company()) or {"delivered": []})
    monkeypatch.setattr(sched, "job_invoice_reminders", lambda db, today: seen["reminders"].append(get_current_company()) or {"sent": 0})
    monkeypatch.setattr(sched, "job_recurring_invoices", lambda db, today: seen["rinv"].append(get_current_company()) or {"issued": 0})

    now = datetime(2026, 9, 24, 9, 0)  # after the default digest hour (8)
    ran = sched.run_pending_jobs(now)
    assert ran == ["recurring_run_due", "notifications_refresh", "daily_digest", "recurring_invoices", "invoice_reminders"]
    for key in seen:
        assert set(seen[key]) >= {str(a.id), str(b.id)}, key
        assert str(s.id) not in seen[key], key  # suspended tenants are left alone

    # Same day again: daily jobs skip (marker), the feed refresh waits 15 minutes.
    before = {k: len(v) for k, v in seen.items()}
    ran2 = sched.run_pending_jobs(datetime(2026, 9, 24, 9, 5))
    assert "notifications_refresh" not in ran2
    assert len(seen["recurring"]) == before["recurring"] and len(seen["digest"]) == before["digest"]
    assert len(seen["reminders"]) == before["reminders"] and len(seen["rinv"]) == before["rinv"]
    # 15 minutes later the feed refreshes again; next day the daily jobs run again.
    sched.run_pending_jobs(datetime(2026, 9, 24, 9, 20))
    assert len(seen["refresh"]) > before["refresh"]
    sched.run_pending_jobs(datetime(2026, 9, 25, 9, 0))
    assert len(seen["recurring"]) > before["recurring"] and len(seen["digest"]) > before["digest"]


def test_digest_waits_for_its_hour(two_companies, monkeypatch):
    calls, reminders = [], []
    monkeypatch.setattr(sched, "job_daily_digest", lambda db, today: calls.append(1) or {})
    monkeypatch.setattr(sched, "job_invoice_reminders", lambda db, today: reminders.append(1) or {})
    monkeypatch.setattr(sched, "job_recurring_invoices", lambda db, today: reminders.append(2) or {})
    monkeypatch.setattr(sched, "job_recurring_run_due", lambda db, today: {})
    monkeypatch.setattr(sched, "job_notifications_refresh", lambda db, today: {})
    ran = sched.run_pending_jobs(datetime(2026, 9, 24, 6, 30))
    assert "daily_digest" not in ran and not calls
    assert "invoice_reminders" not in ran and not reminders   # customers are never mailed at night
    ran = sched.run_pending_jobs(datetime(2026, 9, 24, 8, 0))
    assert "daily_digest" in ran and calls
    assert "invoice_reminders" in ran and "recurring_invoices" in ran and set(reminders) == {1, 2}


def test_one_failing_company_does_not_stop_the_others(two_companies, monkeypatch):
    a, b, _ = two_companies
    from app.db.tenant import get_current_company

    def boom(db, today):
        if get_current_company() == str(a.id):
            raise RuntimeError("tenant A broke")
        return {"ok": True}

    st = sched.run_job_for_all_companies("recurring_run_due", boom, today=datetime(2026, 9, 24).date(), once_per_day=True)
    assert st.companies_failed >= 1 and st.companies_ok >= 1
    assert "tenant A broke" in (st.last_error or "")
    assert str(b.id) in st.detail
    # the failed company has no marker, so it is retried on the next tick
    from app.db.tenant import use_company
    from tests.conftest import _TestSession
    db = _TestSession()
    try:
        with use_company(str(a.id)):
            assert sched._marker(db, "recurring_run_due") is None
        with use_company(str(b.id)):
            assert sched._marker(db, "recurring_run_due") == "2026-09-24"
    finally:
        db.close()


def test_real_jobs_run_end_to_end_on_the_test_db(two_companies):
    # No monkeypatching of the jobs: the real recurring/notification/digest code
    # runs for both companies without raising (digest disabled → nothing sent).
    st_r = sched.run_job_for_all_companies("recurring_run_due", sched.job_recurring_run_due, today=datetime(2026, 9, 24).date(), once_per_day=True)
    st_n = sched.run_job_for_all_companies("notifications_refresh", sched.job_notifications_refresh, today=datetime(2026, 9, 24).date(), once_per_day=False)
    st_d = sched.run_job_for_all_companies("daily_digest", sched.job_daily_digest, today=datetime(2026, 9, 24).date(), once_per_day=True)
    for st in (st_r, st_n, st_d):
        assert st.companies_failed == 0, st.last_error
        assert st.companies_ok >= 2
    snap = sched.status_snapshot()
    assert set(snap["jobs"]) >= {"recurring_run_due", "notifications_refresh", "daily_digest"}


def test_status_route_is_superadmin_only(client):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient

    def _as(superadmin: bool):
        client.cookies.clear()  # one jar: build the two sessions one after the other
        tok = create_session_token(user_id=str(uuid.uuid4()), username="u", is_admin=True, is_superadmin=superadmin)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        return _CSRFTestClient(client, csrf)

    assert _as(False).get("/admin/jobs/status").status_code == 403
    r = _as(True).get("/admin/jobs/status")
    assert r.status_code == 200 and "jobs" in r.json() and "digest_hour" in r.json()
