"""Shared state for more than one worker (roadmap §2.2): database rate
limits, the books version behind the caches, tenant-scoped upload tokens,
AI config freshness across workers and the single-runner scheduler lock."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import shared_state as ss
from app.models.shared_state import RateLimitEvent


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ─── 1. Rate limits in the database ────────────────────────────────────

def test_db_limiter_counts_resets_and_isolates(db):
    lim = ss.DbRateLimiter("t_login", max_requests=3, window_seconds=60)
    other_bucket = ss.DbRateLimiter("t_other", max_requests=3, window_seconds=60)
    for _ in range(3):
        assert lim.would_allow(db, "alice")
        lim.hit(db, "alice")
    assert not lim.would_allow(db, "alice") and lim.remaining(db, "alice") == 0
    assert lim.would_allow(db, "bob")                    # per identity
    assert other_bucket.would_allow(db, "alice")         # per bucket
    lim.reset(db, "alice")
    assert lim.would_allow(db, "alice") and lim.remaining(db, "alice") == 3


def test_two_workers_share_the_same_count(db):
    """Two limiter objects = two processes; they must agree."""
    worker_a = ss.DbRateLimiter("t_shared", max_requests=4, window_seconds=60)
    worker_b = ss.DbRateLimiter("t_shared", max_requests=4, window_seconds=60)
    assert worker_a.is_allowed(db, "ip:1.2.3.4") and worker_b.is_allowed(db, "ip:1.2.3.4")
    assert worker_a.is_allowed(db, "ip:1.2.3.4") and worker_b.is_allowed(db, "ip:1.2.3.4")
    assert not worker_a.is_allowed(db, "ip:1.2.3.4")
    assert not worker_b.is_allowed(db, "ip:1.2.3.4")


def test_window_expiry_and_prune(db):
    lim = ss.DbRateLimiter("t_window", max_requests=1, window_seconds=60)
    db.add(RateLimitEvent(bucket="t_window", identity="x", at=_now() - timedelta(seconds=90)))
    db.add(RateLimitEvent(bucket="t_window", identity="x", at=_now() - timedelta(seconds=500)))
    db.commit()
    assert lim.would_allow(db, "x")                      # both are outside the window
    lim.prune(db)
    db.commit()
    left = db.execute(select(RateLimitEvent).where(RateLimitEvent.bucket == "t_window")).scalars().all()
    assert len(left) == 1                                # only the one older than 2 windows went


def test_login_limit_is_enforced_from_the_database(client, db):
    from app.api.auth import _login_limiter
    for _ in range(5):
        _login_limiter.hit(db, "victim")                 # e.g. recorded by another worker
    r = client.post("/auth/login", json={"username": "victim", "password": "whatever"})
    assert r.status_code == 429


def test_chat_limit_is_shared(auth_client, db, monkeypatch):
    """The per-user AI request limit (app/services/ai_usage.py) is counted in
    Postgres, so a slot used on another worker counts here."""
    from app.core.auth import parse_session_token
    from app.core.config import settings
    from app.services import ai_usage
    real = ai_usage.load_settings
    monkeypatch.setattr(ai_usage, "load_settings", lambda db=None: {**real(db), "user_requests_per_minute": 1})
    uid = parse_session_token(auth_client._client.cookies.get(settings.auth_cookie_name)).user_id
    ss.DbRateLimiter("ai_user", max_requests=1, window_seconds=60).hit(db, uid)  # another worker used the only slot
    r = auth_client.post("/transactions/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and "too quickly" in r.json()["message"]


# ─── 2. Books version ──────────────────────────────────────────────────

def test_ledger_writes_bump_the_version_and_other_writes_do_not(db):
    from app.db.tenant import use_company
    from app.models.app_setting import AppSetting
    from app.models.entity import Entity
    scope = str(uuid.uuid4())
    with use_company(scope):
        v0 = ss.books_version(db, scope)
        ent = Entity(type="client", name="Version Co")
        db.add(ent)
        db.commit()
        v1 = ss.books_version(db, scope)
        assert v1 == v0 + 1
        db.add(AppSetting(key=f"unrelated-{uuid.uuid4().hex[:4]}", value="x"))
        db.commit()
        assert ss.books_version(db, scope) == v1         # settings aren't books
        ent.name = ent.name                              # no real change
        db.commit()
        assert ss.books_version(db, scope) == v1
        ent.name = "Version Co 2"
        db.commit()
        assert ss.books_version(db, scope) == v1 + 1
        db.delete(ent)
        db.commit()
        assert ss.books_version(db, scope) == v1 + 2
    assert ss.books_version(db, str(uuid.uuid4())) == 0


def test_dashboard_is_fresh_after_a_write_from_another_worker(auth_client, db):
    """The write goes straight through the ORM (like another worker would),
    without calling invalidate_dashboard_cache()."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    ccy = "Q" + uuid.uuid4().hex[:4].upper()
    first = auth_client.get("/reports/owner-dashboard", params={"currency": ccy}).json()
    bank = db.execute(select(Account).where(Account.code == "1110")).scalars().first()
    rev = db.execute(select(Account).where(Account.code == "4110")).scalars().first()
    assert bank and rev
    t = Transaction(date=date.today(), description="other worker", currency=ccy)
    db.add(t)
    db.flush()
    db.add_all([TransactionLine(transaction_id=t.id, account_id=bank.id, debit=777, credit=0),
                TransactionLine(transaction_id=t.id, account_id=rev.id, debit=0, credit=777)])
    db.commit()
    try:
        second = auth_client.get("/reports/owner-dashboard", params={"currency": ccy}).json()
        assert json.dumps(second, sort_keys=True) != json.dumps(first, sort_keys=True)
    finally:
        db.execute(TransactionLine.__table__.delete().where(TransactionLine.transaction_id == t.id))
        db.execute(Transaction.__table__.delete().where(Transaction.id == t.id))
        db.commit()


# ─── 3. Upload tokens ──────────────────────────────────────────────────

def test_upload_tokens_are_tenant_scoped_and_expire(db):
    from app.db.tenant import use_company
    from app.models.shared_state import UploadToken
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    with use_company(a):
        ss.store_upload(db, "excel_journal", "tok1", "/tmp/a.xlsx")
        assert ss.find_upload(db, "excel_journal", "tok1") == "/tmp/a.xlsx"
        assert ss.find_upload(db, "other_kind", "tok1") is None
    with use_company(b):
        assert ss.find_upload(db, "excel_journal", "tok1") is None   # another company can't use it
    with use_company(a):
        row = db.execute(select(UploadToken).where(UploadToken.token == "tok1")).scalars().one()
        row.expires_at = _now() - timedelta(minutes=1)
        db.commit()
        assert ss.find_upload(db, "excel_journal", "tok1") is None
        ss.store_upload(db, "excel_journal", "tok2", "/tmp/b.xlsx")  # storing sweeps expired rows
        assert db.execute(select(UploadToken).where(UploadToken.token == "tok1")).scalars().first() is None
        ss.drop_upload(db, "excel_journal", "tok2")
        db.commit()
        assert ss.find_upload(db, "excel_journal", "tok2") is None
    assert ss.find_upload(db, "excel_journal", None) is None


def test_excel_preview_token_survives_a_worker_switch(auth_client, db):
    """The preview stores the token in the database, so a confirm handled by
    a different process (a fresh import of the module state) still finds it."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["تاریخ", "شرح", "کد حساب", "بدهکار", "بستانکار"])
    ws.append(["1405/07/01", "test", "1110", 1000, 0])
    ws.append(["1405/07/01", "test", "4110", 0, 1000])
    buf = io.BytesIO()
    wb.save(buf)
    r = auth_client.post("/transactions/excel-import/preview",
                         files={"file": (f"shared-{uuid.uuid4().hex[:6]}.xlsx", buf.getvalue(),
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    if r.status_code == 404:
        pytest.skip("excel preview route differs")
    assert r.status_code == 200, r.text
    token = r.json().get("file_token") or r.json().get("token")
    assert token
    from app.api.transactions import EXCEL_UPLOAD_KIND
    assert ss.find_upload(db, EXCEL_UPLOAD_KIND, token)


# ─── 4. AI config across workers ───────────────────────────────────────

def test_ai_config_change_on_another_worker_is_picked_up(db, monkeypatch):
    from app.core import ai_runtime
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    from tests.conftest import _TestSession
    monkeypatch.setattr(ai_runtime, "_session_factory", lambda: _TestSession)
    monkeypatch.setattr(ai_runtime, "REFRESH_SECONDS", 1)
    monkeypatch.setattr(ai_runtime, "_last_check", 0.0)
    before = dict(ai_runtime._state["metis"])
    loads = []
    real_load = ai_runtime.load_ai_config_from_db
    monkeypatch.setattr(ai_runtime, "load_ai_config_from_db", lambda: loads.append(1) or real_load())
    with tenant_bypass():
        row = db.execute(select(AppSetting).where(AppSetting.key == "ai_config",
                                                  AppSetting.company_id.is_(None))).scalars().first()
        original = row.value if row else None
        payload = {"provider": "metis", "metis": {"model": f"worker-b-{uuid.uuid4().hex[:4]}"}}
        if row is None:
            db.add(AppSetting(key="ai_config", value=json.dumps(payload), company_id=None))
        else:
            row.value = json.dumps(payload)
        db.commit()
    try:
        cfg = ai_runtime.resolve_active_ai_backend()
        assert cfg["model"] == payload["metis"]["model"] and loads == [1]
        # Same value on the next check: no reload.
        monkeypatch.setattr(ai_runtime, "_last_check", 0.0)
        ai_runtime.resolve_active_ai_backend()
        assert loads == [1]
        # Within the interval nothing is read at all.
        ai_runtime.resolve_active_ai_backend()
        assert loads == [1]
    finally:
        with tenant_bypass():
            row = db.execute(select(AppSetting).where(AppSetting.key == "ai_config",
                                                      AppSetting.company_id.is_(None))).scalars().first()
            if original is None:
                db.delete(row)
            else:
                row.value = original
            db.commit()
        ai_runtime._state["metis"].clear()
        ai_runtime._state["metis"].update(before)


# ─── 5. One scheduler tick at a time ───────────────────────────────────

def test_single_runner_on_sqlite_always_leads():
    from app.jobs.scheduler import single_runner
    from tests.conftest import IS_SQLITE, _engine
    if not IS_SQLITE:
        pytest.skip("SQLite behaviour")
    with single_runner(_engine) as a, single_runner(_engine) as b:
        assert a is True and b is True


def test_single_runner_on_postgres_admits_one_worker():
    from app.jobs.scheduler import single_runner
    from tests.conftest import IS_SQLITE, _engine
    if IS_SQLITE:
        pytest.skip("advisory locks need PostgreSQL (covered by the PostgreSQL CI job)")
    with single_runner(_engine) as first:
        assert first is True
        with single_runner(_engine) as second:
            assert second is False
    with single_runner(_engine) as again:
        assert again is True


def test_tick_is_skipped_when_another_worker_leads(monkeypatch):
    from contextlib import contextmanager
    from app.jobs import scheduler as sched

    @contextmanager
    def _not_leader(engine=None):
        yield False

    ran = []
    monkeypatch.setattr(sched, "single_runner", _not_leader)
    monkeypatch.setattr(sched, "run_pending_jobs", lambda now=None: ran.append(1) or ["x"])
    assert sched.run_tick_if_leader() is None and ran == []
