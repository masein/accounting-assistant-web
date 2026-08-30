"""Recurring auto-posting, the notifications feed, petty cash, and reminders.

The four features from "Accounting assistant modifs": recurring rules that
materialize real journal entries through a chosen bank; a persisted
notification feed (invoice due/overdue, payroll payday, pending approvals,
recurring, reminders); per-user petty cash (تنخواه) with an approval flow and
GL postings; and user reminders with repeat + lead time.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.notification import Notification, Reminder
from app.models.petty_cash import PettyCashAccount, PettyCashTransaction
from app.models.recurring import RecurringRule
from app.models.transaction import Transaction, TransactionLine
from app.services.recurring_service import materialize_due_rules, rule_reference


def _mk_rule(db, **kw):
    today = date.today()
    defaults = dict(
        name="اجاره دفتر", direction="payment", frequency="monthly",
        amount=50_000_000, start_date=today - timedelta(days=1),
        next_run_date=today - timedelta(days=1),
        bank_account_code="1110", counter_account_code="6112",
        auto_post=True, status="active",
    )
    defaults.update(kw)
    rule = RecurringRule(**defaults)
    db.add(rule)
    db.commit()
    return rule


# ---------------------------------------------------------------------------
# Recurring materialization
# ---------------------------------------------------------------------------

def test_due_rule_posts_balanced_transaction_and_advances(db):
    rule = _mk_rule(db)
    run_date = rule.next_run_date
    result = materialize_due_rules(db)
    assert len([p for p in result["posted"] if p["rule_id"] == str(rule.id)]) == 1

    ref = rule_reference(rule, run_date)
    txn = db.execute(
        select(Transaction).where(Transaction.reference == ref)
    ).scalars().one()
    lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
    ).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 50_000_000

    db.refresh(rule)
    assert rule.last_run_date == run_date
    assert rule.next_run_date > date.today() - timedelta(days=1)


def test_materialize_is_idempotent(db):
    rule = _mk_rule(db, name="سرویس اینترنت", reference_prefix="NET")
    materialize_due_rules(db)
    n1 = len(db.execute(select(Transaction).where(
        Transaction.reference.like("REC-NET-%"))).scalars().all())
    # calling again (page load + cron overlap) must not double-post
    materialize_due_rules(db)
    n2 = len(db.execute(select(Transaction).where(
        Transaction.reference.like("REC-NET-%"))).scalars().all())
    assert n1 == n2 == 1


def test_receipt_direction_reverses_legs(db):
    rule = _mk_rule(db, name="اجاره دریافتی", direction="receipt",
                    counter_account_code="4110", reference_prefix="RENT-IN")
    run_date = rule.next_run_date
    materialize_due_rules(db)
    txn = db.execute(select(Transaction).where(
        Transaction.reference == rule_reference(rule, run_date))).scalars().one()
    lines = {l.debit > 0: l for l in db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn.id)
    ).scalars().all()}
    debit_acc = lines[True]
    from app.models.account import Account
    assert db.get(Account, debit_acc.account_id).code == "1110"  # bank debited


def test_reminder_only_rule_posts_nothing_but_advances(db):
    rule = _mk_rule(db, name="یادآور بیمه", auto_post=False, reference_prefix="INS")
    materialize_due_rules(db)
    assert not db.execute(select(Transaction).where(
        Transaction.reference.like("REC-INS-%"))).scalars().all()
    db.refresh(rule)
    assert rule.next_run_date > date.today() - timedelta(days=1)


def test_catchup_posts_each_missed_period(db):
    start = date.today() - timedelta(days=20)
    rule = _mk_rule(db, name="سرویس هفتگی", frequency="weekly",
                    start_date=start, next_run_date=start, reference_prefix="WK")
    materialize_due_rules(db)
    posted = db.execute(select(Transaction).where(
        Transaction.reference.like("REC-WK-%"))).scalars().all()
    assert len(posted) == 3  # days 0, 7, 14 in the last 20 days


def test_manual_create_via_api_with_bank_and_account(auth_client):
    today = date.today().isoformat()
    resp = auth_client.post("/recurring", json={
        "name": "حقوق نگهبانی", "direction": "payment", "frequency": "monthly",
        "amount": 30_000_000, "start_date": today, "next_run_date": today,
        "bank_account_code": "1110", "counter_account_code": "6110",
        "auto_post": True,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["bank_account_code"] == "1110"
    assert body["counter_account_code"] == "6110"
    assert body["auto_post"] is True

    run = auth_client.post("/recurring/run-due")
    assert run.status_code == 200
    assert any(p["name"] == "حقوق نگهبانی" for p in run.json()["posted"])


# ---------------------------------------------------------------------------
# Notifications feed
# ---------------------------------------------------------------------------

def test_feed_surfaces_overdue_invoice_and_clears_when_paid(auth_client, db):
    from app.models.invoice import Invoice

    inv = Invoice(number=f"INV-{uuid.uuid4().hex[:6]}", kind="sales", status="issued",
                  issue_date=date.today() - timedelta(days=40),
                  due_date=date.today() - timedelta(days=10), amount=1000)
    db.add(inv)
    db.commit()

    feed = auth_client.get("/notifications/feed").json()
    hit = next((i for i in feed if i["kind"] == "invoice_overdue" and inv.number in i["title"]), None)
    assert hit is not None and hit["level"] == "high"

    inv.status = "paid"
    db.commit()
    feed = auth_client.get("/notifications/feed").json()
    assert all(inv.number not in i["title"] for i in feed)


def test_feed_includes_payroll_and_pending_approvals(auth_client, db):
    from app.models.mileage_claim import MileageClaim
    from app.models.pay_run import PayRun

    run = PayRun(period_start=date.today().replace(day=1),
                 period_end=date.today(), pay_date=date.today() + timedelta(days=1),
                 status="draft")
    claim = MileageClaim(claim_date=date.today(), distance=10, rate=1000,
                         amount=10000, status="pending_approval",
                         employee_name="کارمند آزمایشی")
    db.add_all([run, claim])
    db.commit()

    feed = auth_client.get("/notifications/feed").json()
    kinds = {i["kind"] for i in feed}
    assert "payroll" in kinds
    assert "approvals" in kinds

    # read-all marks everything visible as read
    r = auth_client.post("/notifications/feed/read-all")
    assert r.status_code == 200 and r.json()["marked"] >= 2
    feed = auth_client.get("/notifications/feed").json()
    assert all(i["read"] for i in feed)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def test_reminder_crud_and_feed_visibility(auth_client):
    created = auth_client.post("/notifications/reminders", json={
        "title": "پرداخت اجاره", "due_date": (date.today() + timedelta(days=2)).isoformat(),
        "repeat": "monthly", "days_before": 5,
    })
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    listed = auth_client.get("/notifications/reminders").json()
    assert any(r["id"] == rid for r in listed)

    # inside the 5-day lead window → appears in the caller's feed
    feed = auth_client.get("/notifications/feed").json()
    assert any(i["kind"] == "reminder" and i["title"] == "پرداخت اجاره" for i in feed)

    upd = auth_client.patch(f"/notifications/reminders/{rid}", json={"status": "paused"})
    assert upd.status_code == 200 and upd.json()["status"] == "paused"
    feed = auth_client.get("/notifications/feed").json()
    assert all(not (i["kind"] == "reminder" and i["title"] == "پرداخت اجاره") for i in feed)

    assert auth_client.delete(f"/notifications/reminders/{rid}").status_code == 204


def test_repeating_reminder_rolls_forward(auth_client, db):
    r = auth_client.post("/notifications/reminders", json={
        "title": "قسط وام", "due_date": (date.today() - timedelta(days=3)).isoformat(),
        "repeat": "weekly", "days_before": 2,
    }).json()
    auth_client.get("/notifications/feed")  # triggers refresh + roll-forward
    row = db.get(Reminder, uuid.UUID(r["id"]))
    db.refresh(row)
    assert row.due_date >= date.today()  # advanced to the next weekly occurrence


def test_foreign_reminder_forbidden(auth_client, db):
    other = Reminder(user_id="someone-else", title="private",
                     due_date=date.today())
    db.add(other)
    db.commit()
    assert auth_client.patch(f"/notifications/reminders/{other.id}",
                             json={"title": "x"}).status_code == 403
    assert auth_client.delete(f"/notifications/reminders/{other.id}").status_code == 403
    listed = auth_client.get("/notifications/reminders").json()
    assert all(r["id"] != str(other.id) for r in listed)


# ---------------------------------------------------------------------------
# Petty cash
# ---------------------------------------------------------------------------

def _petty_account(auth_client, db):
    from app.models.user import User

    username = f"petty-{uuid.uuid4().hex[:6]}"
    from app.core.auth import hash_password
    pw_hash, pw_salt = hash_password("x")
    holder = User(username=username, password_hash=pw_hash, password_salt=pw_salt,
                  is_admin=False, is_active=True, role="employee")
    db.add(holder)
    db.commit()
    resp = auth_client.post("/petty-cash/accounts", json={"username": username})
    assert resp.status_code == 201, resp.text
    return resp.json(), holder


def test_petty_cash_full_cycle(auth_client, db):
    acc, holder = _petty_account(auth_client, db)
    assert acc["balance"] == 0

    # charge the float: DR petty cash / CR bank — posts a journal entry
    dep = auth_client.post(f"/petty-cash/accounts/{acc['id']}/deposit", json={
        "amount": 5_000_000, "bank_account_code": "1110",
    })
    assert dep.status_code == 200, dep.text
    assert dep.json()["balance"] == 5_000_000
    gl_id = dep.json()["transaction"]["transaction_id"]
    lines = db.execute(select(TransactionLine).join(Transaction).where(
        Transaction.id == uuid.UUID(gl_id))).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 5_000_000

    # holder records an expense → pending, no GL yet
    exp = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={
        "amount": 1_200_000, "description": "خرید ملزومات دفتر",
    })
    assert exp.status_code == 201
    assert exp.json()["status"] == "pending"
    assert exp.json()["transaction_id"] is None
    # balance unchanged while pending
    detail = auth_client.get(f"/petty-cash/accounts/{acc['id']}").json()
    assert detail["balance"] == 5_000_000
    assert detail["pending_expenses"] == 1

    # approve → DR expense / CR petty cash; balance drops
    ap = auth_client.post(f"/petty-cash/expenses/{exp.json()['id']}/approve")
    assert ap.status_code == 200, ap.text
    assert ap.json()["balance"] == 3_800_000
    assert ap.json()["transaction"]["transaction_id"] is not None

    # reject path posts nothing and leaves the balance alone
    exp2 = auth_client.post(f"/petty-cash/accounts/{acc['id']}/expenses", json={
        "amount": 900_000, "description": "بدون رسید",
    }).json()
    rj = auth_client.post(f"/petty-cash/expenses/{exp2['id']}/reject")
    assert rj.status_code == 200
    assert auth_client.get(f"/petty-cash/accounts/{acc['id']}").json()["balance"] == 3_800_000

    # negative adjustment returns float to the bank
    adj = auth_client.post(f"/petty-cash/accounts/{acc['id']}/adjust", json={
        "signed_amount": -800_000, "counter_account_code": "1110",
        "description": "برگشت مانده به بانک",
    })
    assert adj.status_code == 200
    assert adj.json()["balance"] == 3_000_000

    # full history visible on the account
    detail = auth_client.get(f"/petty-cash/accounts/{acc['id']}").json()
    assert len(detail["transactions"]) == 4


def test_petty_cash_own_scope(client, db):
    """An employee sees only their own account and cannot approve."""
    from tests.conftest import _CSRFTestClient

    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token, hash_password
    from app.core.config import settings
    from app.models.user import User

    pw_hash, pw_salt = hash_password("x")
    emp = User(username=f"emp-{uuid.uuid4().hex[:6]}", password_hash=pw_hash,
               password_salt=pw_salt, is_admin=False, is_active=True, role="employee")
    db.add(emp)
    db.commit()

    mine = PettyCashAccount(user_id=str(emp.id), holder_name=emp.username)
    other = PettyCashAccount(user_id="someone-else", holder_name="other")
    db.add_all([mine, other])
    db.commit()

    token = create_session_token(user_id=str(emp.id), username=emp.username,
                                 is_admin=False, role="employee")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    emp_client = _CSRFTestClient(client, csrf)

    listed = emp_client.get("/petty-cash/accounts").json()
    assert [a["id"] for a in listed] == [str(mine.id)]
    assert emp_client.get(f"/petty-cash/accounts/{other.id}").status_code == 403

    # employee can record an expense on their own account…
    exp = emp_client.post(f"/petty-cash/accounts/{mine.id}/expenses", json={
        "amount": 100, "description": "تاکسی",
    })
    assert exp.status_code == 201
    # …but cannot approve (route needs petty:manage) or deposit
    assert emp_client.post(f"/petty-cash/expenses/{exp.json()['id']}/approve").status_code == 403
    assert emp_client.post(f"/petty-cash/accounts/{mine.id}/deposit", json={
        "amount": 100, "bank_account_code": "1110"}).status_code == 403


def test_duplicate_active_account_rejected(auth_client, db):
    acc, holder = _petty_account(auth_client, db)
    dup = auth_client.post("/petty-cash/accounts", json={"username": holder.username})
    assert dup.status_code == 400


def test_feed_budget_alert_warning_over_and_cleared(auth_client, db):
    """Budget alerts: >=85% utilization -> warning, >=100% -> high, and the
    row auto-dismisses when the condition clears (limit raised)."""
    from app.models.account import Account, AccountLevel
    from app.models.budget import BudgetLimit

    # A dedicated expense account so spending posted by OTHER tests in this
    # module (e.g. recurring auto-posting against 6112) can't skew the pct.
    exp = Account(code="6119", name=f"BUDTEST-{uuid.uuid4().hex[:6]}",
                  level=AccountLevel.GENERAL)
    db.add(exp)
    db.flush()
    cash = db.execute(select(Account).where(Account.code == "1110")).scalars().first()
    assert cash is not None
    today = date.today()
    month = f"{today.year:04d}-{today.month:02d}"
    limit = BudgetLimit(month=month, category=exp.name, limit_amount=1_000_000)
    db.add(limit)

    txn = Transaction(date=today, description="budget test spend",
                      reference=f"BUD-{uuid.uuid4().hex[:6]}")
    db.add(txn)
    db.flush()
    db.add_all([
        TransactionLine(transaction_id=txn.id, account_id=exp.id, debit=900_000, credit=0),
        TransactionLine(transaction_id=txn.id, account_id=cash.id, debit=0, credit=900_000),
    ])
    db.commit()

    feed = auth_client.get("/notifications/feed").json()
    hit = next((i for i in feed if i["kind"] == "budget" and exp.name in i["title"]), None)
    assert hit is not None
    assert hit["level"] == "warning"

    txn2 = Transaction(date=today, description="budget test spend 2",
                       reference=f"BUD-{uuid.uuid4().hex[:6]}")
    db.add(txn2)
    db.flush()
    db.add_all([
        TransactionLine(transaction_id=txn2.id, account_id=exp.id, debit=200_000, credit=0),
        TransactionLine(transaction_id=txn2.id, account_id=cash.id, debit=0, credit=200_000),
    ])
    db.commit()

    feed = auth_client.get("/notifications/feed").json()
    hit = next((i for i in feed if i["kind"] == "budget" and exp.name in i["title"]), None)
    assert hit is not None
    assert hit["level"] == "high"

    # raising the limit clears the alert on the next refresh
    limit.limit_amount = 100_000_000
    db.commit()
    feed = auth_client.get("/notifications/feed").json()
    assert not any(i["kind"] == "budget" and exp.name in i["title"] for i in feed)
