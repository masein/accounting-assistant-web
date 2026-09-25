"""Invoice e-mail + automatic overdue reminders (roadmap §4.2).

SMTP is faked at ``mail_service._connect``: every message the app hands to
the server is captured, so the tests assert on the real MIME message (To,
Reply-To, subject, attachment) without a network.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.services import invoice_mail, mail_service


class _FakeSMTP:
    def __init__(self, sent, fail=False):
        self.sent, self.fail = sent, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, *_a):
        return None

    def send_message(self, msg):
        if self.fail:
            raise OSError("relay refused")
        self.sent.append(msg)


@pytest.fixture()
def mailbox(monkeypatch):
    sent: list = []
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    # Real PDF rendering is covered elsewhere; keep these tests fast.
    from app.services.documents import render as render_mod
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: b"%PDF-1.4 test")
    return sent


@pytest.fixture()
def customer(auth_client, db):
    ent = auth_client.post("/entities", json={
        "type": "client", "name": f"Mail Co {uuid.uuid4().hex[:6]}", "email": "billing@mailco.example",
    }).json()
    yield ent
    from app.models.invoice import Invoice
    db.rollback()
    for inv in db.execute(select(Invoice).where(Invoice.entity_id == uuid.UUID(ent["id"]))).scalars().all():
        db.delete(inv)
    db.commit()


def _invoice(auth_client, customer, *, status="issued", kind="sales", due="2026-09-30", amount=4_000_000):
    r = auth_client.post("/invoices", json={
        "number": f"ML-{uuid.uuid4().hex[:6]}", "kind": kind, "status": status, "issue_date": "2026-09-01",
        "due_date": due, "amount": amount, "currency": "IRR", "entity_id": customer["id"],
    })
    assert r.status_code == 201, r.text
    return r.json()


# ─── 1. Message building ───────────────────────────────────────────────

def test_build_message_attachment_reply_to_and_no_header_injection():
    msg = mail_service.build_message(
        to=["a@x.example"], subject="Hi\r\nBcc: evil@x.example", text="body",
        attachments=[("invoice-1.pdf", b"%PDF", "application/pdf")],
        reply_to="books@co.example\nBcc: evil@x.example", from_name="Co\nX-Evil: 1",
    )
    assert "\n" not in msg["Subject"] and "Bcc" not in msg.keys()
    assert "X-Evil" not in msg.keys()
    assert msg["Reply-To"].startswith("books@co.example")
    parts = [p for p in msg.iter_attachments()]
    assert len(parts) == 1 and parts[0].get_filename() == "invoice-1.pdf"
    assert parts[0].get_content_type() == "application/pdf" and parts[0].get_content() == b"%PDF"


def test_parse_recipients():
    assert invoice_mail.parse_recipients("a@x.io, b@y.io") == ["a@x.io", "b@y.io"]
    for bad in ("", "nope", "a@x.io,b@y.io,c@z.io,d@w.io", "a@x", "<a@x.io>"):
        with pytest.raises(invoice_mail.InvoiceMailError):
            invoice_mail.parse_recipients(bad)


# ─── 2. Sending one invoice ────────────────────────────────────────────

def test_send_invoice_to_the_customer_with_pdf(auth_client, db, customer, mailbox):
    inv = _invoice(auth_client, customer)
    r = auth_client.post(f"/invoices/{inv['id']}/send", json={"message": "Thanks for your order."})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "sent" and row["kind"] == "invoice" and row["to_address"] == "billing@mailco.example"
    assert len(mailbox) == 1
    msg = mailbox[0]
    assert msg["To"] == "billing@mailco.example"
    assert inv["number"] in msg["Subject"]
    att = list(msg.iter_attachments())
    assert att[0].get_filename() == f"invoice-{inv['number']}.pdf"
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Thanks for your order." in body and customer["name"] in body

    log = auth_client.get(f"/invoices/{inv['id']}/emails").json()
    assert [e["status"] for e in log] == ["sent"]
    events = auth_client.get(f"/invoices/{inv['id']}/timeline").json()
    assert any(e["event"] == "email" for e in events)
    from app.models.audit_log import AuditLog
    assert db.execute(select(AuditLog).where(AuditLog.entity_type == "invoice", AuditLog.action == "email",
                                             AuditLog.entity_id == inv["id"])).scalars().first() is not None


def test_send_to_an_explicit_address_and_validation(auth_client, customer, mailbox):
    inv = _invoice(auth_client, customer)
    r = auth_client.post(f"/invoices/{inv['id']}/send", json={"to": "cfo@mailco.example; ap@mailco.example"})
    assert r.status_code == 201 and r.json()["to_address"] == "cfo@mailco.example, ap@mailco.example"
    assert auth_client.post(f"/invoices/{inv['id']}/send", json={"to": "not-an-address"}).status_code == 422
    assert auth_client.post(f"/invoices/{uuid.uuid4()}/send", json={}).status_code == 404


def test_send_refuses_wrong_states(auth_client, customer, mailbox):
    draft = _invoice(auth_client, customer, status="draft")
    assert auth_client.post(f"/invoices/{draft['id']}/send", json={}).status_code == 409
    bill = _invoice(auth_client, customer, kind="purchase")
    assert auth_client.post(f"/invoices/{bill['id']}/send", json={}).status_code == 409
    voided = _invoice(auth_client, customer)
    assert auth_client.post(f"/invoices/{voided['id']}/void").status_code == 200
    assert auth_client.post(f"/invoices/{voided['id']}/send", json={}).status_code == 409
    assert mailbox == []


def test_customer_without_email_needs_an_address(auth_client, db, mailbox):
    ent = auth_client.post("/entities", json={"type": "client", "name": f"No Mail {uuid.uuid4().hex[:6]}"}).json()
    try:
        inv = _invoice(auth_client, ent)
        r = auth_client.post(f"/invoices/{inv['id']}/send", json={})
        assert r.status_code == 422 and "e-mail" in r.json()["detail"]
    finally:
        from app.models.invoice import Invoice
        db.rollback()
        for row in db.execute(select(Invoice).where(Invoice.entity_id == uuid.UUID(ent["id"]))).scalars().all():
            db.delete(row)
        db.commit()


def test_mail_not_configured_is_503_and_logs_nothing(auth_client, customer, monkeypatch):
    monkeypatch.setattr(mail_service, "mail_configured", lambda: False)
    inv = _invoice(auth_client, customer)
    r = auth_client.post(f"/invoices/{inv['id']}/send", json={})
    assert r.status_code == 503
    assert auth_client.get(f"/invoices/{inv['id']}/emails").json() == []


def test_delivery_failure_is_logged_and_reported(auth_client, customer, monkeypatch):
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP([], fail=True))
    from app.services.documents import render as render_mod
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: b"%PDF-1.4 test")
    inv = _invoice(auth_client, customer)
    r = auth_client.post(f"/invoices/{inv['id']}/send", json={})
    assert r.status_code == 502 and "relay refused" in r.json()["detail"]
    log = auth_client.get(f"/invoices/{inv['id']}/emails").json()
    assert log[0]["status"] == "failed" and "relay refused" in log[0]["error"]
    assert any(e["event"] == "email_failed" for e in auth_client.get(f"/invoices/{inv['id']}/timeline").json())


# ─── 3. Settings ───────────────────────────────────────────────────────

def test_reminder_settings_defaults_validation_and_owner_only(auth_client, client, db):
    from app.models.app_setting import AppSetting
    try:
        got = auth_client.get("/invoices/reminder-settings").json()
        assert got["reminders_enabled"] is False and got["reminder_days"] == [3, 10, 20]
        assert "mail_configured" in got
        for bad in ({"reminder_days": []}, {"reminder_days": [1, 2, 3, 4, 5, 6]}, {"reminder_days": [400]},
                    {"reminder_days": [-60]}, {"payment_link": "javascript:alert(1)"}):
            assert auth_client.put("/invoices/reminder-settings", json=bad).status_code == 422, bad
        r = auth_client.put("/invoices/reminder-settings", json={
            "reminders_enabled": True, "reminder_days": [20, 3, 3, -2],
            "payment_instructions": "Card 6037-9971-0000-0000 (Bank Melli)", "payment_link": "https://pay.example/inv",
        })
        assert r.status_code == 200, r.text
        assert r.json()["reminder_days"] == [-2, 3, 20] and r.json()["reminders_enabled"] is True

        from tests.test_rbac_permissions import _role_client
        client.cookies.clear()
        acc = _role_client(client, "accountant")
        assert acc.put("/invoices/reminder-settings", json={"reminders_enabled": False}).status_code == 403
        assert acc.get("/invoices/reminder-settings").status_code == 200
    finally:
        db.rollback()
        for row in db.execute(select(AppSetting).where(AppSetting.key == invoice_mail.SETTINGS_KEY)).scalars().all():
            db.delete(row)
        db.commit()


# ─── 4. The reminder job (inside a private company) ────────────────────

@pytest.fixture()
def tenant(db):
    """A private company so the job only sees the invoices this test makes."""
    from app.db.tenant import use_company
    from app.models.company import Company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Reminders", slug=f"rem-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        yield db
    _purge_company(db, cid)


TODAY = date(2026, 10, 1)


def _mk(db, *, due_offset: int, email: str | None = "pay@cust.example", status="issued", kind="sales",
        amount=1_000):
    from app.models.entity import Entity
    from app.models.invoice import Invoice
    ent = Entity(type="client", name=f"Cust {uuid.uuid4().hex[:4]}", email=email)
    db.add(ent)
    db.flush()
    inv = Invoice(number=f"R-{uuid.uuid4().hex[:6]}", kind=kind, status=status, issue_date=TODAY - timedelta(days=60),
                  due_date=TODAY - timedelta(days=due_offset), amount=amount, currency="GBP", entity_id=ent.id)
    db.add(inv)
    db.commit()
    return inv


def _enable(db, days=(3, 10, 20)):
    invoice_mail.save_settings(db, reminders_enabled=True, reminder_days=list(days))
    db.commit()


def test_reminders_off_by_default_send_nothing(tenant, mailbox):
    _mk(tenant, due_offset=5)
    out = invoice_mail.run_reminders(tenant, today=TODAY)
    assert out["enabled"] is False and out["sent"] == 0 and mailbox == []


def test_reminder_stages_are_sent_once_and_in_order(tenant, mailbox):
    _enable(tenant)
    inv = _mk(tenant, due_offset=5)                       # 5 days overdue → stage 3
    out = invoice_mail.run_reminders(tenant, today=TODAY)
    assert out["sent"] == 1 and invoice_mail.last_reminder_stage(tenant, inv.id) == 3
    assert "overdue" in mailbox[0]["Subject"] and inv.number in mailbox[0]["Subject"]
    body = mailbox[0].get_body(preferencelist=("plain",)).get_content()
    assert "already paid" in body
    # Same stage again (next day, still stage 3): nothing.
    assert invoice_mail.run_reminders(tenant, today=TODAY + timedelta(days=1))["sent"] == 0
    # A week later it has reached stage 10.
    assert invoice_mail.run_reminders(tenant, today=TODAY + timedelta(days=6))["sent"] == 1
    assert invoice_mail.last_reminder_stage(tenant, inv.id) == 10
    assert len(mailbox) == 2


def test_missed_stages_send_only_the_latest(tenant, mailbox):
    _enable(tenant)
    inv = _mk(tenant, due_offset=45)
    assert invoice_mail.run_reminders(tenant, today=TODAY)["sent"] == 1
    assert invoice_mail.last_reminder_stage(tenant, inv.id) == 20
    assert invoice_mail.run_reminders(tenant, today=TODAY + timedelta(days=30))["sent"] == 0
    assert len(mailbox) == 1


def test_who_does_not_get_a_reminder(tenant, mailbox):
    _enable(tenant)
    _mk(tenant, due_offset=5, status="paid")                 # settled
    _mk(tenant, due_offset=-5)                               # not due yet
    _mk(tenant, due_offset=5, kind="purchase")               # a supplier's bill
    _mk(tenant, due_offset=5, status="draft")
    _mk(tenant, due_offset=5, status="voided")
    no_mail = _mk(tenant, due_offset=5, email=None)
    out = invoice_mail.run_reminders(tenant, today=TODAY)
    assert out["sent"] == 0 and out["no_email"] == 1 and mailbox == []
    assert invoice_mail.last_reminder_stage(tenant, no_mail.id) is None


def test_before_due_reminder(tenant, mailbox):
    _enable(tenant, days=(-3,))
    inv = _mk(tenant, due_offset=-2)                          # due in 2 days
    assert invoice_mail.run_reminders(tenant, today=TODAY)["sent"] == 1
    assert "is due on" in mailbox[0]["Subject"]
    assert invoice_mail.last_reminder_stage(tenant, inv.id) == -3


def test_failed_reminder_is_retried_next_run(tenant, monkeypatch):
    _enable(tenant)
    inv = _mk(tenant, due_offset=5)
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    from app.services.documents import render as render_mod
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: b"%PDF-1.4 test")
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP([], fail=True))
    assert invoice_mail.run_reminders(tenant, today=TODAY)["failed"] == 1
    assert invoice_mail.last_reminder_stage(tenant, inv.id) is None
    sent: list = []
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    assert invoice_mail.run_reminders(tenant, today=TODAY + timedelta(days=1))["sent"] == 1
    assert [e.status for e in invoice_mail.email_log(tenant, inv.id)] == ["failed", "sent"]


def test_run_is_capped(tenant, mailbox, monkeypatch):
    _enable(tenant)
    _mk(tenant, due_offset=5)
    _mk(tenant, due_offset=6)
    monkeypatch.setattr(invoice_mail, "MAX_REMINDERS_PER_RUN", 1)
    out = invoice_mail.run_reminders(tenant, today=TODAY)
    assert out["sent"] == 1 and out.get("capped") is True


def test_no_smtp_means_the_job_sends_nothing(tenant, monkeypatch):
    _enable(tenant)
    _mk(tenant, due_offset=5)
    monkeypatch.setattr(mail_service, "mail_configured", lambda: False)
    out = invoice_mail.run_reminders(tenant, today=TODAY)
    assert out == {"enabled": True, "mail": False, "sent": 0, "failed": 0, "no_email": 0}


# ─── 5. Content: Persian for Iranian companies, escaped HTML ───────────

def test_persian_reminder_and_payment_details(tenant, monkeypatch):
    monkeypatch.setattr(invoice_mail, "_company_bits", lambda db: {
        "name": "شرکت نمونه", "locale": "ir", "bank_details": "شبا: IR000000000000000000000000", "reply_to": None})
    _enable(tenant)
    invoice_mail.save_settings(tenant, payment_link="https://pay.example/x")
    inv = _mk(tenant, due_offset=12)
    composed, _ = invoice_mail.compose(tenant, inv, kind="reminder", stage=10, today=TODAY)
    assert composed.subject.startswith("یادآوری") and "۱۲" in composed.subject
    assert "شرکت نمونه" in composed.text and "شبا" in composed.text and "https://pay.example/x" in composed.text
    assert "dir='rtl'" in composed.html and "<a href='https://pay.example/x'>" in composed.html


def test_html_is_escaped(tenant):
    from app.models.entity import Entity
    inv = _mk(tenant, due_offset=5)
    ent = tenant.get(Entity, inv.entity_id)
    ent.name = "<script>alert(1)</script>"
    tenant.commit()
    composed, _ = invoice_mail.compose(tenant, inv, kind="invoice", message="<b>hi</b>", today=TODAY)
    assert "<script>" not in composed.html and "&lt;script&gt;" in composed.html
    assert "<b>hi</b>" not in composed.html
