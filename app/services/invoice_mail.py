"""Invoice e-mail and automatic overdue reminders (roadmap §4.2).

* ``send_invoice_email`` e-mails one sales invoice to the customer with the
  branded PDF attached and a "how to pay" block, and logs the attempt.
* ``run_reminders`` is the daily scheduler job: for every open, overdue
  sales invoice it sends the latest reminder stage the invoice has reached
  and not yet received. Stages are day offsets from the due date (default
  3, 10 and 20 days overdue — three reminders, like Xero's default); a
  negative offset reminds before the due date.

Customer-facing mail is off until the company turns reminders on, and
nothing is sent when the server has no SMTP configured. Every attempt,
successful or not, is kept in ``invoice_emails``.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.company_profile import CompanyProfile
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.invoice_email import InvoiceEmail

SETTINGS_KEY = "invoice_mail"
DEFAULT_REMINDER_DAYS = (3, 10, 20)
MAX_STAGES = 5
MAX_RECIPIENTS = 3
MAX_REMINDERS_PER_RUN = 100
SENDABLE_STATUSES = ("issued", "partially_paid", "paid")
REMINDER_STATUSES = ("issued", "partially_paid")

_EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']{2,}$")


class InvoiceMailError(ValueError):
    """A request the caller can fix (bad address, wrong invoice state)."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Settings (per company, app_settings key "invoice_mail")
# ---------------------------------------------------------------------------

def _defaults() -> dict:
    return {
        "reminders_enabled": False,
        "reminder_days": list(DEFAULT_REMINDER_DAYS),
        "payment_instructions": "",
        "payment_link": "",
    }


def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).scalars().first()


def get_settings(db: Session) -> dict:
    out = _defaults()
    row = _row(db)
    if row and row.value:
        try:
            saved = json.loads(row.value)
        except ValueError:
            saved = {}
        if isinstance(saved, dict):
            out.update({k: saved[k] for k in out if k in saved})
    return out


def normalize_days(days) -> list[int]:
    try:
        vals = sorted({int(d) for d in (days or [])})
    except (TypeError, ValueError) as e:
        raise InvoiceMailError("Reminder days must be whole numbers.") from e
    if not vals:
        raise InvoiceMailError("Give at least one reminder day.")
    if len(vals) > MAX_STAGES:
        raise InvoiceMailError(f"At most {MAX_STAGES} reminders.")
    if vals[0] < -30 or vals[-1] > 365:
        raise InvoiceMailError("Reminder days must be between -30 (before due) and 365.")
    return vals


def save_settings(db: Session, **changes) -> dict:
    cur = get_settings(db)
    if changes.get("reminder_days") is not None:
        cur["reminder_days"] = normalize_days(changes["reminder_days"])
    if changes.get("reminders_enabled") is not None:
        cur["reminders_enabled"] = bool(changes["reminders_enabled"])
    if changes.get("payment_instructions") is not None:
        cur["payment_instructions"] = str(changes["payment_instructions"]).strip()[:2000]
    if changes.get("payment_link") is not None:
        link = str(changes["payment_link"]).strip()
        if link and not re.match(r"^https?://[^\s<>\"']+$", link):
            raise InvoiceMailError("The payment link must be an http(s) URL.")
        cur["payment_link"] = link[:500]
    row = _row(db)
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=json.dumps(cur)))
    else:
        row.value = json.dumps(cur)
    db.flush()
    return cur


# ---------------------------------------------------------------------------
# Recipients and content
# ---------------------------------------------------------------------------

def parse_recipients(raw: str | None) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,;]", raw or "") if p.strip()]
    if not parts:
        raise InvoiceMailError("No e-mail address: add one to the customer or type it in.")
    if len(parts) > MAX_RECIPIENTS:
        raise InvoiceMailError(f"At most {MAX_RECIPIENTS} addresses.")
    for p in parts:
        if not _EMAIL_RE.match(p):
            raise InvoiceMailError(f"'{p}' is not a valid e-mail address.")
    return parts


@dataclass
class Composed:
    subject: str
    text: str
    html: str


def _company_bits(db: Session) -> dict:
    from app.services.documents.branding import build_brand
    brand = build_brand(db)
    prof = db.execute(select(CompanyProfile)).scalars().first()
    reply_to = (prof.email or "").strip() if prof else ""
    return {
        "name": brand["issuer"]["name"],
        "locale": brand["locale"],
        "bank_details": brand["issuer"].get("bank_details") or "",
        "reply_to": reply_to if _EMAIL_RE.match(reply_to or "") else None,
    }


_T = {
    "en": {
        "subject_invoice": "Invoice {number} from {company}",
        "subject_reminder": "Reminder: invoice {number} is {days} day(s) overdue",
        "subject_before": "Reminder: invoice {number} is due on {due}",
        "greeting": "Dear {party},",
        "intro_invoice": "Please find attached invoice {number} for {amount}, due on {due}.",
        "intro_reminder": "This is a reminder that invoice {number} for {amount} was due on {due} and is now {days} day(s) overdue.",
        "intro_before": "A friendly reminder that invoice {number} for {amount} is due on {due}.",
        "balance": "Amount outstanding: {balance}",
        "how_to_pay": "How to pay",
        "pay_link": "Pay online: {link}",
        "ignore": "If you have already paid, thank you, and please ignore this message.",
        "signoff": "Kind regards,\n{company}",
        "customer": "customer",
    },
    "fa": {
        "subject_invoice": "صورتحساب {number} از {company}",
        "subject_reminder": "یادآوری: {days} روز از سررسید صورتحساب {number} گذشته است",
        "subject_before": "یادآوری: سررسید صورتحساب {number} در تاریخ {due}",
        "greeting": "{party} گرامی،",
        "intro_invoice": "صورتحساب شمارهٔ {number} به مبلغ {amount} با سررسید {due} به پیوست تقدیم می‌شود.",
        "intro_reminder": "یادآوری می‌کنیم سررسید صورتحساب شمارهٔ {number} به مبلغ {amount} در تاریخ {due} بوده و اکنون {days} روز از آن گذشته است.",
        "intro_before": "یادآوری می‌کنیم سررسید صورتحساب شمارهٔ {number} به مبلغ {amount} در تاریخ {due} است.",
        "balance": "مانده قابل پرداخت: {balance}",
        "how_to_pay": "نحوه پرداخت",
        "pay_link": "پرداخت آنلاین: {link}",
        "ignore": "اگر پرداخت را انجام داده‌اید، سپاسگزاریم؛ لطفاً این پیام را نادیده بگیرید.",
        "signoff": "با احترام،\n{company}",
        "customer": "مشتری",
    },
}


def compose(db: Session, inv: Invoice, *, kind: str, stage: int | None = None,
            message: str | None = None, today: date | None = None) -> tuple[Composed, dict]:
    """Subject, plain text and HTML in the company's document language
    (Persian for Iranian companies, English otherwise)."""
    from app.api.invoices import _invoice_totals
    from app.services.documents.formatting import fmt_date, fmt_money, to_persian_digits

    today = today or date.today()
    bits = _company_bits(db)
    lang = "fa" if (bits["locale"] or "").lower() == "ir" else "en"
    T = _T[lang]
    party = db.get(Entity, inv.entity_id) if inv.entity_id else None
    _paid, _credited, balance = _invoice_totals(db, inv)
    loc = bits["locale"]
    overdue_days = (today - inv.due_date).days
    days_txt = to_persian_digits(str(overdue_days)) if lang == "fa" else str(overdue_days)
    number = to_persian_digits(inv.number) if lang == "fa" else inv.number
    vals = {
        "number": number, "company": bits["name"], "party": (party.name if party else T["customer"]),
        "amount": fmt_money(inv.amount, inv.currency, loc), "due": fmt_date(inv.due_date, loc),
        "balance": fmt_money(balance, inv.currency, loc), "days": days_txt,
    }
    if kind == "invoice":
        subject, intro = T["subject_invoice"], T["intro_invoice"]
    elif overdue_days > 0:
        subject, intro = T["subject_reminder"], T["intro_reminder"]
    else:
        subject, intro = T["subject_before"], T["intro_before"]

    conf = get_settings(db)
    how = (conf.get("payment_instructions") or "").strip() or bits["bank_details"]
    link = (conf.get("payment_link") or "").strip()
    paras: list[str] = [T["greeting"].format(**vals), intro.format(**vals)]
    if balance != int(inv.amount or 0):
        paras.append(T["balance"].format(**vals))
    note = (message or "").strip()
    if note:
        paras.append(note[:2000])
    if how:
        paras.append(f"{T['how_to_pay']}:\n{how}")
    if link:
        paras.append(T["pay_link"].format(link=link))
    if kind == "reminder":
        paras.append(T["ignore"])
    paras.append(T["signoff"].format(**vals))
    text = "\n\n".join(paras)

    direction = "rtl" if lang == "fa" else "ltr"
    body = "".join(
        f"<p style='margin:0 0 12px'>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras
    )
    if link:
        safe = html.escape(link, quote=True)
        body = body.replace(html.escape(link), f"<a href='{safe}'>{html.escape(link)}</a>")
    html_doc = (f"<div dir='{direction}' style='font-family:Tahoma,Arial,sans-serif;font-size:14px;"
                f"line-height:1.6;color:#1f2937'>{body}</div>")
    return Composed(subject=subject.format(**vals), text=text, html=html_doc), bits


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def default_recipient(db: Session, inv: Invoice) -> str | None:
    party = db.get(Entity, inv.entity_id) if inv.entity_id else None
    return (party.email or "").strip() or None if party else None


def send_invoice_email(db: Session, inv: Invoice, *, to: str | None = None, kind: str = "invoice",
                       stage: int | None = None, message: str | None = None, actor: str | None = None,
                       today: date | None = None) -> InvoiceEmail:
    """Compose, attach the PDF, send and log one e-mail. Raises
    ``InvoiceMailError`` for requests the caller must fix; a delivery failure
    is logged (status ``failed``) and returned, not raised."""
    from app.services.documents.render import render_invoice_pdf
    from app.services.mail_service import mail_configured, send_email_detailed

    if inv.kind != "sales":
        raise InvoiceMailError("Only sales invoices are e-mailed to customers.", 409)
    allowed = REMINDER_STATUSES if kind == "reminder" else SENDABLE_STATUSES
    if inv.status not in allowed:
        raise InvoiceMailError(
            f"A {inv.status} invoice can't be e-mailed" + ("; issue it first." if inv.status == "draft" else "."),
            409,
        )
    if not mail_configured():
        raise InvoiceMailError("Outgoing mail is not configured on this server (SMTP settings).", 503)
    recipients = parse_recipients(to or default_recipient(db, inv))
    composed, bits = compose(db, inv, kind=kind, stage=stage, message=message, today=today)
    party = db.get(Entity, inv.entity_id) if inv.entity_id else None
    pdf = render_invoice_pdf(db, inv, party)
    ok, err = send_email_detailed(
        to=recipients, subject=composed.subject, text=composed.text, html=composed.html,
        attachments=[(f"invoice-{inv.number}.pdf", pdf, "application/pdf")],
        reply_to=bits["reply_to"], from_name=bits["name"],
    )
    row = InvoiceEmail(
        invoice_id=inv.id, kind=kind, stage=stage, to_address=", ".join(recipients)[:512],
        subject=composed.subject[:256], status="sent" if ok else "failed", error=err, actor=actor,
    )
    db.add(row)
    db.flush()
    return row


def last_reminder_stage(db: Session, invoice_id) -> int | None:
    return db.execute(
        select(func.max(InvoiceEmail.stage)).where(
            InvoiceEmail.invoice_id == invoice_id, InvoiceEmail.kind == "reminder",
            InvoiceEmail.status == "sent",
        )
    ).scalar()


def due_stage(offset_days: int, days: list[int]) -> int | None:
    """The latest configured stage the invoice has reached (offset = days
    past due; negative before due), or None."""
    reached = [d for d in days if offset_days >= d]
    return max(reached) if reached else None


def run_reminders(db: Session, *, today: date | None = None) -> dict:
    """Daily job body. One reminder per invoice per run: if several stages
    were missed (reminders just switched on, server was down) only the latest
    is sent, so a customer never gets a burst."""
    from app.api.invoices import _invoice_totals
    from app.services.mail_service import mail_configured

    today = today or date.today()
    conf = get_settings(db)
    result = {"enabled": bool(conf["reminders_enabled"]), "mail": mail_configured(),
              "sent": 0, "failed": 0, "no_email": 0}
    if not result["enabled"] or not result["mail"]:
        return result
    days = normalize_days(conf["reminder_days"])
    earliest = min(days)
    candidates = db.execute(
        select(Invoice).where(
            Invoice.kind == "sales", Invoice.status.in_(REMINDER_STATUSES), Invoice.due_date.is_not(None),
        ).order_by(Invoice.due_date)
    ).scalars().all()
    attempts = 0
    for inv in candidates:
        if attempts >= MAX_REMINDERS_PER_RUN:
            result["capped"] = True
            break
        offset = (today - inv.due_date).days
        if offset < earliest:
            continue
        stage = due_stage(offset, days)
        if stage is None:
            continue
        last = last_reminder_stage(db, inv.id)
        if last is not None and last >= stage:
            continue
        if _invoice_totals(db, inv)[2] <= 0:
            continue
        if not default_recipient(db, inv):
            result["no_email"] += 1
            continue
        try:
            row = send_invoice_email(db, inv, kind="reminder", stage=stage, actor="scheduler", today=today)
        except InvoiceMailError:
            result["no_email"] += 1  # an unusable address on the customer
            continue
        attempts += 1
        result["sent" if row.status == "sent" else "failed"] += 1
    db.commit()
    return result


def email_log(db: Session, invoice_id) -> list[InvoiceEmail]:
    return db.execute(
        select(InvoiceEmail).where(InvoiceEmail.invoice_id == invoice_id).order_by(InvoiceEmail.created_at)
    ).scalars().all()
