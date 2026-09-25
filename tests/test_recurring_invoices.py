"""Recurring sales invoices (roadmap §4.2, part 3): anchored schedules
(incl. Jalali months), the daily generator with catch-up, end conditions,
closed-period retry, auto-send, and the HTTP surface."""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.services import mail_service
from app.services import recurring_invoice_service as svc
from app.services.recurring_invoice_service import occurrence_date


# ─── 1. Schedule maths ─────────────────────────────────────────────────

def test_monthly_is_anchored_on_the_start_day():
    start = date(2026, 1, 31)
    got = [occurrence_date(start, "monthly", "gregorian", n) for n in range(4)]
    assert got == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert occurrence_date(date(2028, 1, 31), "monthly", "gregorian", 1) == date(2028, 2, 29)  # leap year


def test_weekly_quarterly_yearly():
    s = date(2026, 5, 15)
    assert occurrence_date(s, "weekly", "gregorian", 3) == date(2026, 6, 5)
    assert occurrence_date(s, "quarterly", "gregorian", 2) == date(2026, 11, 15)
    assert occurrence_date(date(2028, 2, 29), "yearly", "gregorian", 1) == date(2029, 2, 28)


def test_jalali_months_follow_the_persian_calendar():
    import jdatetime
    first_mehr = jdatetime.date(1405, 7, 1).togregorian()           # 1 Mehr 1405
    got = [jdatetime.date.fromgregorian(date=occurrence_date(first_mehr, "monthly", "jalali", n)) for n in range(3)]
    assert [(g.year, g.month, g.day) for g in got] == [(1405, 7, 1), (1405, 8, 1), (1405, 9, 1)]
    # 31 Shahrivar → 30 Mehr (Mehr has 30 days), then 30 Aban, …, Esfand clamps to 29/30.
    end_shahrivar = jdatetime.date(1405, 6, 31).togregorian()
    mehr = jdatetime.date.fromgregorian(date=occurrence_date(end_shahrivar, "monthly", "jalali", 1))
    assert (mehr.month, mehr.day) == (7, 30)
    esfand = jdatetime.date.fromgregorian(date=occurrence_date(end_shahrivar, "monthly", "jalali", 6))
    assert esfand.month == 12 and esfand.day in (29, 30)
    # And it is genuinely different from Gregorian stepping.
    assert occurrence_date(first_mehr, "monthly", "jalali", 2) != occurrence_date(first_mehr, "monthly", "gregorian", 2)


# ─── 2. The generator (inside a private company) ───────────────────────

@pytest.fixture()
def company(db):
    from app.db.tenant import use_company
    from app.models.company import Company
    from tests.test_admin_audit import _purge_company
    made = []

    def _make(locale="uk"):
        c = Company(id=uuid.uuid4(), name="Recurring", slug=f"rin-{uuid.uuid4().hex[:6]}", locale=locale,
                    base_currency="GBP" if locale == "uk" else "IRR", status="active", token_version=0)
        db.add(c)
        db.commit()
        made.append(str(c.id))
        return use_company(str(c.id))

    yield _make
    for cid in made:
        _purge_company(db, cid)


def _template(db, *, start, frequency="monthly", calendar="gregorian", email="acct@cust.example",
              auto_send=False, issue_status="issued", end_date=None, max_occurrences=None, items=None,
              amount=0, terms=14):
    from app.models.entity import Entity
    from app.models.recurring_invoice import RecurringInvoice
    ent = Entity(type="client", name=f"Retainer {uuid.uuid4().hex[:4]}", email=email)
    db.add(ent)
    db.flush()
    t = RecurringInvoice(
        name="Monthly retainer", entity_id=ent.id, currency="GBP", amount=amount,
        items=json.dumps(items if items is not None else [
            {"product_name": "Retainer", "quantity": 1, "unit_price": 1000, "tax_rate": 20}]),
        frequency=frequency, calendar=calendar, start_date=start, next_run_date=start, end_date=end_date,
        max_occurrences=max_occurrences, terms_days=terms, issue_status=issue_status, auto_send=auto_send,
        status="active", occurrences=0,
    )
    db.add(t)
    db.commit()
    return t


def _invoices(db, t):
    from app.models.invoice import Invoice
    return db.execute(select(Invoice).where(Invoice.recurring_invoice_id == t.id).order_by(Invoice.issue_date)).scalars().all()


def test_generates_the_due_occurrence_as_a_recognised_invoice(db, company):
    from app.models.transaction import TransactionLine
    with company():
        t = _template(db, start=date(2026, 9, 1))
        out = svc.generate_due(db, today=date(2026, 9, 1))
        assert out["issued"] == 1
        [inv] = _invoices(db, t)
        assert inv.status == "issued" and inv.issue_date == date(2026, 9, 1)
        assert inv.due_date == date(2026, 9, 15)                       # 14-day terms
        assert inv.amount == 1200 and inv.entity_id == t.entity_id    # 1000 + 20 % VAT
        dr = db.execute(select(func.sum(TransactionLine.debit)).where(TransactionLine.transaction_id == inv.transaction_id)).scalar()
        assert int(dr) == 1200
        db.refresh(t)
        assert t.occurrences == 1 and t.next_run_date == date(2026, 10, 1) and t.last_invoice_id == inv.id
        # Idempotent: running again the same day issues nothing.
        assert svc.generate_due(db, today=date(2026, 9, 1))["issued"] == 0


def test_catch_up_is_capped_and_numbers_are_sequential(db, company):
    with company():
        t = _template(db, start=date(2024, 1, 1))
        out = svc.generate_due(db, today=date(2026, 9, 1))
        assert out["issued"] == svc.MAX_CATCHUP
        invs = _invoices(db, t)
        nums = [int(i.number.split("-")[-1]) for i in invs]
        assert nums == sorted(nums) and len(set(nums)) == len(nums)
        assert [i.issue_date for i in invs][:3] == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]
        # the rest follows on later runs
        assert svc.generate_due(db, today=date(2026, 9, 1))["issued"] == svc.MAX_CATCHUP


def test_end_date_and_max_occurrences_end_the_schedule(db, company):
    from app.models.recurring_invoice import RecurringInvoice
    with company():
        a = _template(db, start=date(2026, 6, 1), end_date=date(2026, 7, 15))
        b = _template(db, start=date(2026, 6, 1), max_occurrences=2)
        svc.generate_due(db, today=date(2026, 12, 1))
        assert len(_invoices(db, a)) == 2 and db.get(RecurringInvoice, a.id).status == "ended"
        assert len(_invoices(db, b)) == 2 and db.get(RecurringInvoice, b.id).status == "ended"


def test_paused_and_draft_templates(db, company):
    from app.models.recurring_invoice import RecurringInvoice
    with company():
        p = _template(db, start=date(2026, 9, 1))
        p.status = "paused"
        d = _template(db, start=date(2026, 9, 1), issue_status="draft", auto_send=True)
        db.commit()
        svc.generate_due(db, today=date(2026, 9, 1))
        assert _invoices(db, p) == []
        [draft] = _invoices(db, d)
        assert draft.status == "draft" and draft.transaction_id is None
        assert db.get(RecurringInvoice, d.id).last_error is None   # drafts are never e-mailed


def test_closed_period_is_retried_not_skipped(db, company):
    from app.models.recurring_invoice import RecurringInvoice
    from app.services.period_service import set_closed_period
    with company():
        t = _template(db, start=date(2026, 8, 1))
        set_closed_period(db, date(2026, 8, 31))
        db.commit()
        out = svc.generate_due(db, today=date(2026, 8, 2))
        assert out["failed"] == 1 and _invoices(db, t) == []
        t = db.get(RecurringInvoice, t.id)
        assert t.next_run_date == date(2026, 8, 1) and t.occurrences == 0 and "2026-08-01" in t.last_error
        set_closed_period(db, None)
        db.commit()
        assert svc.generate_due(db, today=date(2026, 8, 2))["issued"] == 1
        assert db.get(RecurringInvoice, t.id).last_error is None


def test_auto_send_emails_each_issued_invoice(db, company, monkeypatch):
    from app.models.recurring_invoice import RecurringInvoice
    from app.services.documents import render as render_mod
    from app.services.invoice_mail import email_log
    from tests.test_invoice_mail import _FakeSMTP
    sent: list = []
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: b"%PDF-1.4 test")
    with company():
        t = _template(db, start=date(2026, 8, 1), auto_send=True)
        no_mail = _template(db, start=date(2026, 8, 1), auto_send=True, email=None)
        out = svc.generate_due(db, today=date(2026, 9, 1))
        assert out["issued"] == 4 and out["emailed"] == 2
        assert len(sent) == 2 and all(m["To"] == "acct@cust.example" for m in sent)
        for inv in _invoices(db, t):
            assert [e.status for e in email_log(db, inv.id)] == ["sent"]
        # The customer without an address still gets invoiced; the template says why nothing was sent.
        assert len(_invoices(db, no_mail)) == 2
        assert "not e-mailed" in db.get(RecurringInvoice, no_mail.id).last_error


def test_jalali_template_for_an_iranian_company(db, company):
    import jdatetime
    with company(locale="ir"):
        start = jdatetime.date(1405, 7, 1).togregorian()
        t = _template(db, start=start, calendar="jalali")
        svc.generate_due(db, today=start + timedelta(days=70))
        days = [jdatetime.date.fromgregorian(date=i.issue_date) for i in _invoices(db, t)]
        assert [(d.month, d.day) for d in days] == [(7, 1), (8, 1), (9, 1)]


# ─── 3. HTTP ───────────────────────────────────────────────────────────

@pytest.fixture()
def customer(auth_client, db):
    ent = auth_client.post("/entities", json={"type": "client", "name": f"Sub Co {uuid.uuid4().hex[:6]}",
                                              "email": "ap@subco.example"}).json()
    yield ent
    from app.models.invoice import Invoice
    from app.models.recurring_invoice import RecurringInvoice
    db.rollback()
    eid = uuid.UUID(ent["id"])
    for t in db.execute(select(RecurringInvoice).where(RecurringInvoice.entity_id == eid)).scalars().all():
        t.last_invoice_id = None
    db.flush()
    for inv in db.execute(select(Invoice).where(Invoice.entity_id == eid)).scalars().all():
        db.delete(inv)
    db.flush()
    for t in db.execute(select(RecurringInvoice).where(RecurringInvoice.entity_id == eid)).scalars().all():
        db.delete(t)
    db.commit()


def _body(customer, **over):
    return {"entity_id": customer["id"], "currency": "IRR", "start_date": date.today().isoformat(),
            "frequency": "monthly", "calendar": "gregorian", "terms_days": 10,
            "items": [{"product_name": "Hosting", "quantity": 1, "unit_price": 5_000_000}], **over}


def test_create_generates_the_first_invoice_now(auth_client, customer):
    r = auth_client.post("/recurring-invoices", json=_body(customer))
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["generated"]["issued"] == 1 and t["occurrences"] == 1 and t["last_invoice_number"]
    assert t["total"] == 5_000_000 and t["name"].startswith(customer["name"])
    hist = auth_client.get(f"/recurring-invoices/{t['id']}/invoices").json()
    assert len(hist) == 1 and hist[0]["issue_date"] == date.today().isoformat()
    assert hist[0]["due_date"] == (date.today() + timedelta(days=10)).isoformat()
    listed = [x for x in auth_client.get("/recurring-invoices").json() if x["id"] == t["id"]]
    assert listed and listed[0]["entity_email"] == "ap@subco.example"


def test_future_start_generates_nothing_yet(auth_client, customer):
    future = (date.today() + timedelta(days=20)).isoformat()
    t = auth_client.post("/recurring-invoices", json=_body(customer, start_date=future)).json()
    assert t["generated"] is None and t["occurrences"] == 0 and t["next_run_date"] == future


def test_create_validation(auth_client, customer):
    assert auth_client.post("/recurring-invoices", json=_body(customer, frequency="daily")).status_code == 422
    assert auth_client.post("/recurring-invoices", json=_body(customer, calendar="lunar")).status_code == 422
    assert auth_client.post("/recurring-invoices", json=_body(customer, items=[], amount=0)).status_code == 422
    assert auth_client.post("/recurring-invoices", json=_body(customer, entity_id=str(uuid.uuid4()))).status_code == 422
    past = (date.today() - timedelta(days=1)).isoformat()
    assert auth_client.post("/recurring-invoices", json=_body(customer, end_date=past)).status_code == 422


def test_schedule_is_locked_after_the_first_invoice_and_pause_resume(auth_client, customer):
    t = auth_client.post("/recurring-invoices", json=_body(customer)).json()
    assert auth_client.patch(f"/recurring-invoices/{t['id']}", json={"frequency": "weekly"}).status_code == 409
    upd = auth_client.patch(f"/recurring-invoices/{t['id']}", json={"terms_days": 30, "auto_send": True,
                                                                    "status": "paused"}).json()
    assert upd["terms_days"] == 30 and upd["auto_send"] is True and upd["status"] == "paused"
    res = auth_client.patch(f"/recurring-invoices/{t['id']}", json={"status": "active"}).json()
    assert res["status"] == "active" and res["next_run_date"] >= date.today().isoformat()
    assert auth_client.patch(f"/recurring-invoices/{t['id']}", json={"status": "ended"}).status_code == 422


def test_delete_keeps_the_issued_invoices(auth_client, db, customer):
    from app.models.invoice import Invoice
    t = auth_client.post("/recurring-invoices", json=_body(customer)).json()
    inv_id = uuid.UUID(t["last_invoice_id"])
    assert auth_client.delete(f"/recurring-invoices/{t['id']}").status_code == 204
    assert auth_client.get(f"/recurring-invoices/{t['id']}").status_code == 404
    db.expire_all()
    inv = db.get(Invoice, inv_id)
    assert inv is not None and inv.recurring_invoice_id is None and inv.status == "issued"
