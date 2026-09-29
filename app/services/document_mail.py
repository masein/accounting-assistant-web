"""Statements of account and payslips by e-mail (roadmap §4.9).

* ``send_statement`` e-mails a client or supplier their statement of account
  (the branded PDF the Entities page already prints) for a period, with the
  closing balance in the message.
* ``send_payslips`` e-mails each employee on a posted or paid pay run their
  own payslip PDF — only their own, to the address on their employee record.
  Employees without one are reported, not guessed at.

Every attempt is logged in ``document_emails``. Nothing is sent when the
server has no SMTP configured (503), and the text is in the company's
document language, as invoice e-mails are.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_email import DocumentEmail
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.services.invoice_mail import InvoiceMailError, _company_bits, parse_recipients

PAYSLIP_STATUSES = ("posted", "paid")

_T = {
    "en": {
        "statement_subject": "Statement of account from {company}, {start} to {end}",
        "statement_intro": "Please find attached your statement of account with {company} for {start} to {end}.",
        "statement_balance": "Balance at {end}: {balance}",
        "payslip_subject": "Your payslip for {start} to {end} — {company}",
        "payslip_intro": "Please find attached your payslip for {start} to {end}, paid on {paid}.",
        "payslip_net": "Net pay: {net}",
        "greeting": "Dear {party},",
        "questions": "If anything looks wrong, just reply to this e-mail.",
        "signoff": "Kind regards,\n{company}",
    },
    "fa": {
        "statement_subject": "صورت‌حساب حساب شما نزد {company} از {start} تا {end}",
        "statement_intro": "صورت‌حساب حساب شما نزد {company} برای دوره {start} تا {end} به پیوست تقدیم می‌شود.",
        "statement_balance": "مانده در {end}: {balance}",
        "payslip_subject": "فیش حقوقی {start} تا {end} — {company}",
        "payslip_intro": "فیش حقوقی شما برای دوره {start} تا {end} که در تاریخ {paid} پرداخت شد به پیوست است.",
        "payslip_net": "خالص پرداختی: {net}",
        "greeting": "{party} گرامی،",
        "questions": "اگر موردی نادرست به نظر می‌رسد، به همین نامه پاسخ دهید.",
        "signoff": "با احترام،\n{company}",
    },
}


@dataclass
class _Mail:
    subject: str
    text: str
    html: str


def _compose(bits: dict, subject: str, paras: list[str]) -> _Mail:
    lang_rtl = (bits["locale"] or "").lower() == "ir"
    body = "".join(f"<p style='margin:0 0 12px'>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)
    return _Mail(subject=subject, text="\n\n".join(paras),
                 html=(f"<div dir='{'rtl' if lang_rtl else 'ltr'}' style='font-family:Tahoma,Arial,sans-serif;"
                       f"font-size:14px;line-height:1.6;color:#1f2937'>{body}</div>"))


def _lang(bits: dict) -> str:
    return "fa" if (bits["locale"] or "").lower() == "ir" else "en"


def _require_mail() -> None:
    from app.services.mail_service import mail_configured
    if not mail_configured():
        raise InvoiceMailError("Outgoing mail is not configured on this server (SMTP settings).", 503)


# --- statements --------------------------------------------------------------------------------------------------

def statement_events(db: Session, entity: Entity, start: date, end: date) -> tuple[list[dict], str]:
    """A party's invoices (debit) and payments in (credit) between two dates, in date order."""
    events: list[dict] = []
    ccy = entity.currency
    invoices = db.execute(select(Invoice).where(Invoice.entity_id == entity.id).order_by(Invoice.issue_date)).scalars().all()
    for inv in invoices:
        if inv.issue_date and start <= inv.issue_date <= end and (inv.status or "") not in ("voided", "canceled"):
            events.append({"date": inv.issue_date, "description": f"Invoice {inv.number}",
                           "debit": int(inv.amount or 0), "credit": 0})
            ccy = ccy or inv.currency
        for pay in db.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars().all():
            if pay.date and start <= pay.date <= end and pay.direction == "in":
                events.append({"date": pay.date, "description": f"Payment — {inv.number}",
                               "debit": 0, "credit": int(pay.amount or 0)})
    events.sort(key=lambda e: e["date"])
    return events, ccy or ""


def statement_pdf(db: Session, entity: Entity, start: date, end: date) -> tuple[bytes, list[dict], str]:
    from app.services.documents import render_statement_pdf
    events, ccy = statement_events(db, entity, start, end)
    return render_statement_pdf(db, entity, events, (start, end), ccy), events, ccy


def send_statement(db: Session, entity: Entity, *, start: date, end: date, to: str | None = None,
                   message: str | None = None, actor: str | None = None) -> DocumentEmail:
    from app.services.documents.formatting import fmt_date, fmt_money
    from app.services.mail_service import send_email_detailed

    if start > end:
        raise InvoiceMailError("The statement starts after it ends.")
    _require_mail()
    recipients = parse_recipients(to or (entity.email or None))
    bits = _company_bits(db)
    T, loc = _T[_lang(bits)], bits["locale"]
    pdf, events, ccy = statement_pdf(db, entity, start, end)
    balance = sum(e["debit"] - e["credit"] for e in events)
    vals = {"company": bits["name"], "party": entity.name, "start": fmt_date(start, loc), "end": fmt_date(end, loc),
            "balance": fmt_money(balance, ccy, loc)}
    paras = [T["greeting"].format(**vals), T["statement_intro"].format(**vals), T["statement_balance"].format(**vals)]
    if (message or "").strip():
        paras.append(message.strip()[:2000])
    paras += [T["questions"], T["signoff"].format(**vals)]
    mail = _compose(bits, T["statement_subject"].format(**vals), paras)
    ok, err = send_email_detailed(
        to=recipients, subject=mail.subject, text=mail.text, html=mail.html,
        attachments=[(f"statement-{entity.name[:40].replace(' ', '_')}-{start}-{end}.pdf", pdf, "application/pdf")],
        reply_to=bits["reply_to"], from_name=bits["name"])
    row = DocumentEmail(kind="statement", entity_id=entity.id, to_address=", ".join(recipients)[:512],
                        subject=mail.subject[:256], status="sent" if ok else "failed", error=err, actor=actor)
    db.add(row)
    db.flush()
    return row


# --- payslips ----------------------------------------------------------------------------------------------------

@dataclass
class PayslipSend:
    sent: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sent": self.sent, "failed": self.failed, "skipped": self.skipped}


def send_payslips(db: Session, run, *, entity_ids: list | None = None, actor: str | None = None) -> PayslipSend:
    """Each chosen employee on the run gets their own payslip, at their own address."""
    from app.models.pay_run import PayRunLine
    from app.services.documents import render_payslip_pdf
    from app.services.documents.formatting import fmt_date, fmt_money
    from app.services.mail_service import send_email_detailed

    if run.status not in PAYSLIP_STATUSES:
        raise InvoiceMailError("Post the pay run before e-mailing its payslips.", 409)
    _require_mail()
    wanted = {str(i) for i in entity_ids} if entity_ids else None
    lines = db.execute(select(PayRunLine).where(PayRunLine.run_id == run.id)
                       .order_by(PayRunLine.employee_name)).scalars().all()
    if wanted is not None:
        unknown = wanted - {str(ln.entity_id) for ln in lines}
        if unknown:
            raise InvoiceMailError("Some of those employees aren't on this pay run.")
        lines = [ln for ln in lines if str(ln.entity_id) in wanted]
    bits = _company_bits(db)
    T, loc = _T[_lang(bits)], bits["locale"]
    out = PayslipSend()
    for ln in lines:
        employee = db.get(Entity, ln.entity_id)
        who = {"entity_id": str(ln.entity_id), "name": ln.employee_name}
        address = ((employee.email if employee else None) or "").strip()
        if not address:
            out.skipped.append({**who, "reason": "no e-mail on the employee record"})
            continue
        try:
            recipients = parse_recipients(address)
        except InvoiceMailError as e:
            out.skipped.append({**who, "reason": str(e)})
            continue
        vals = {"company": bits["name"], "party": ln.employee_name, "start": fmt_date(run.period_start, loc),
                "end": fmt_date(run.period_end, loc), "paid": fmt_date(run.pay_date, loc),
                "net": fmt_money(int(ln.net_pay or 0), run.currency, loc)}
        mail = _compose(bits, T["payslip_subject"].format(**vals), [
            T["greeting"].format(**vals), T["payslip_intro"].format(**vals), T["payslip_net"].format(**vals),
            T["questions"], T["signoff"].format(**vals)])
        pdf = render_payslip_pdf(db, run, ln, employee)
        ok, err = send_email_detailed(
            to=recipients, subject=mail.subject, text=mail.text, html=mail.html,
            attachments=[(f"payslip-{run.period_end}-{ln.employee_name[:40].replace(' ', '_')}.pdf", pdf, "application/pdf")],
            reply_to=bits["reply_to"], from_name=bits["name"])
        db.add(DocumentEmail(kind="payslip", entity_id=ln.entity_id, pay_run_id=run.id,
                             to_address=", ".join(recipients)[:512], subject=mail.subject[:256],
                             status="sent" if ok else "failed", error=err, actor=actor))
        (out.sent if ok else out.failed).append({**who, "to": ", ".join(recipients), **({} if ok else {"error": err})})
    db.flush()
    return out


def email_log(db: Session, *, entity_id=None, pay_run_id=None) -> list[dict]:
    q = select(DocumentEmail).order_by(DocumentEmail.created_at.desc())
    if entity_id is not None:
        q = q.where(DocumentEmail.entity_id == entity_id)
    if pay_run_id is not None:
        q = q.where(DocumentEmail.pay_run_id == pay_run_id)
    return [{"id": str(r.id), "kind": r.kind, "to": r.to_address, "subject": r.subject, "status": r.status,
             "error": r.error, "actor": r.actor, "at": r.created_at.isoformat() if r.created_at else None,
             "entity_id": str(r.entity_id) if r.entity_id else None}
            for r in db.execute(q.limit(50)).scalars()]
