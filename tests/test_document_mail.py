"""Statements of account and payslips by e-mail (roadmap §4.9)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.document_email import DocumentEmail
from app.services import mail_service
from tests.test_invoice_mail import _FakeSMTP
from tests.test_payroll_lifecycle_http import _employee, _run, co  # noqa: F401 — fixture


@pytest.fixture()
def mailbox(monkeypatch):
    sent: list = []
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    return sent


def _attachments(msg):
    return [(p.get_filename(), p.get_content_type()) for p in msg.iter_attachments()]


def _log(db, cid, **filt):
    with use_company(cid):
        q = select(DocumentEmail)
        for k, v in filt.items():
            q = q.where(getattr(DocumentEmail, k) == v)
        return db.execute(q.order_by(DocumentEmail.created_at)).scalars().all()


# --- statements ----------------------------------------------------------------------------------------------------

def _client_with_history(api, email="accounts@aria.example"):
    ent = api.post("/entities", json={"type": "client", "name": "Aria Trading", "email": email}).json()
    inv = api.post("/invoices", json={"number": f"ST-{uuid.uuid4().hex[:5]}", "kind": "sales", "status": "issued",
                                      "issue_date": "2026-03-01", "due_date": "2026-03-31", "amount": 5_000_000,
                                      "currency": "IRR", "entity_id": ent["id"]})
    assert inv.status_code == 201, inv.text
    pay = api.post(f"/invoices/{inv.json()['id']}/payments", json={"amount": 2_000_000, "date": "2026-03-15"})
    assert pay.status_code in (200, 201), pay.text
    return ent


def test_a_statement_is_emailed_with_its_pdf_and_balance(co, db, mailbox):
    session, cid = co
    api = session()
    ent = _client_with_history(api)
    r = api.post(f"/entities/{ent['id']}/statement/email", json={"date_from": "2026-01-01", "date_to": "2026-06-30",
                                                               "message": "Thanks for your business."})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["to"]) == ("sent", "accounts@aria.example")
    msg = mailbox[0]
    assert msg["To"] == "accounts@aria.example"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    plain_digits = body.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    assert "3,000,000" in plain_digits                                            # the balance: 5m invoiced − 2m paid
    assert "Thanks for your business." in body
    (name, ctype), = _attachments(msg)
    assert ctype == "application/pdf" and name.startswith("statement-Aria_Trading-2026-01-01-2026-06-30")
    row, = _log(db, cid, kind="statement")
    assert (row.status, str(row.entity_id), row.to_address) == ("sent", ent["id"], "accounts@aria.example")
    assert [x["status"] for x in api.get(f"/entities/{ent['id']}/emails").json()] == ["sent"]


def test_a_statement_goes_to_the_address_typed_or_explains_why_not(co, monkeypatch, mailbox):
    session, _cid = co
    api = session()
    ent = _client_with_history(api, email=None)
    r = api.post(f"/entities/{ent['id']}/statement/email", json={})
    assert r.status_code == 422 and "No e-mail address" in r.json()["detail"]     # nothing on file
    assert api.post(f"/entities/{ent['id']}/statement/email", json={"to": "not-an-address"}).status_code == 422
    r = api.post(f"/entities/{ent['id']}/statement/email", json={"to": "cfo@aria.example"})
    assert r.json()["status"] == "sent" and mailbox[-1]["To"] == "cfo@aria.example"
    bad = api.post(f"/entities/{ent['id']}/statement/email", json={"to": "a@b.example", "date_from": "2026-06-01",
                                                                 "date_to": "2026-01-01"})
    assert bad.status_code == 422                                                 # backwards
    assert api.post(f"/entities/{uuid.uuid4()}/statement/email", json={}).status_code == 404
    monkeypatch.setattr(mail_service, "mail_configured", lambda: False)
    assert api.post(f"/entities/{ent['id']}/statement/email", json={"to": "a@b.example"}).status_code == 503


def test_a_persian_company_writes_in_persian(co, db, mailbox):
    session, _cid = co                                                            # the payroll fixture is an Iranian company
    api = session()
    ent = _client_with_history(api)
    api.post(f"/entities/{ent['id']}/statement/email", json={})
    assert "صورت‌حساب" in mailbox[0]["Subject"] and "گرامی" in mailbox[0].get_body(preferencelist=("plain",)).get_content()


# --- payslips ------------------------------------------------------------------------------------------------------

def test_each_employee_gets_only_their_own_payslip(co, db, mailbox):
    session, cid = co
    api = session()
    a = _employee(api, "Sara Ahmadi")
    b = _employee(api, "Reza Karimi")
    c = _employee(api, "No Mail")
    api.patch(f"/entities/{a['id']}", json={"email": "sara@staff.example"})
    api.patch(f"/entities/{b['id']}", json={"email": "reza@staff.example"})
    run = _run(api).json()
    r = api.post(f"/payroll/runs/{run['id']}/payslips/email", json={})
    assert r.status_code == 409 and "Post the pay run" in r.json()["detail"]      # a draft isn't final
    assert api.post(f"/payroll/runs/{run['id']}/post").status_code == 200
    r = api.post(f"/payroll/runs/{run['id']}/payslips/email", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(x["name"] for x in out["sent"]) == ["Reza Karimi", "Sara Ahmadi"]
    assert [(x["name"], x["reason"]) for x in out["skipped"]] == [("No Mail", "no e-mail on the employee record")]
    by_to = {m["To"]: m for m in mailbox}
    assert set(by_to) == {"sara@staff.example", "reza@staff.example"}
    for to, who in (("sara@staff.example", "Sara_Ahmadi"), ("reza@staff.example", "Reza_Karimi")):
        (name, ctype), = _attachments(by_to[to])                                  # one payslip each…
        assert ctype == "application/pdf" and who in name                          # …their own
    assert len(_log(db, cid, kind="payslip")) == 2
    assert len(api.get(f"/payroll/runs/{run['id']}/emails").json()) == 2
    # just one employee, on request; someone not on the run is refused
    r = api.post(f"/payroll/runs/{run['id']}/payslips/email", json={"entity_ids": [a["id"]]})
    assert [x["name"] for x in r.json()["sent"]] == ["Sara Ahmadi"]
    assert api.post(f"/payroll/runs/{run['id']}/payslips/email", json={"entity_ids": [str(uuid.uuid4())]}).status_code == 422
    assert api.post(f"/payroll/runs/{uuid.uuid4()}/payslips/email", json={}).status_code == 404


def test_a_failed_delivery_is_logged_not_raised(co, db, monkeypatch):
    session, cid = co
    api = session()
    a = _employee(api, "Sara Ahmadi")
    api.patch(f"/entities/{a['id']}", json={"email": "sara@staff.example"})
    run = _run(api).json()
    api.post(f"/payroll/runs/{run['id']}/post")
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP([], fail=True))
    out = api.post(f"/payroll/runs/{run['id']}/payslips/email", json={}).json()
    assert out["sent"] == [] and out["failed"][0]["name"] == "Sara Ahmadi" and out["failed"][0]["error"]
    row, = _log(db, cid, kind="payslip")
    assert row.status == "failed" and row.error


def test_who_may_send(co, mailbox):
    session, _cid = co
    owner = session()
    ent = _client_with_history(owner)
    _employee(owner, "Sara Ahmadi")
    run = _run(owner).json()
    # accountants run payroll, so they may send (a draft run is refused on its own terms)
    for role, statement, payslips in (("accountant", 200, 409), ("employee", 403, 403), ("viewer", 403, 403)):
        api = session(role)
        assert api.post(f"/entities/{ent['id']}/statement/email", json={}).status_code == statement, role
        assert api.post(f"/payroll/runs/{run['id']}/payslips/email", json={}).status_code == payslips, role


def test_the_buttons_are_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    assert 'id="pr-email-btn"' in html
    ent = open("app/static/js/08-entities-invoices.js", encoding="utf-8").read()
    assert "entity-statement-email" in ent and "/statement/email'" in ent and "/statement.pdf" in ent
    pay = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    assert "'/payslips/email'" in pay
    text = i18n_text()
    for k in ("payrollEmailPayslips", "payrollEmailConfirm", "payrollEmailFailed", "payrollEmailResult",
              "payrollEmailNoAddress", "entStatementPdf", "entStatementEmail", "entStatementEmailPrompt",
              "entStatementEmailFailed", "entStatementEmailSent", "entStatementEmailNotSent"):
        assert text.count(f"{k}:") == 4, k
