"""Per-document context builders → branded PDF bytes via the shared engine."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.documents.branding import build_brand, build_recipient
from app.services.documents.engine import render_pdf
from app.services.documents.formatting import (
    amount_in_words,
    fmt_date,
    fmt_digits,
    fmt_money,
)
from app.services.documents.labels import labels_for, method_label, status_label
from app.core.money import plain


def _ctx(db: Session) -> tuple[dict, dict, str]:
    brand = build_brand(db)
    L = labels_for(brand["locale"])
    return brand, L, brand["locale"]


def _identity_lines(p: dict, L: dict) -> list:
    """The official-invoice identity block (فاکتور رسمی) for a party card:
    economic code (شماره اقتصادی), national ID (شناسه ملی), province + city,
    postal code — printed only when set, so non-Iranian documents stay clean."""
    locality = " — ".join(x for x in (p.get("province"), p.get("city")) if x) or None
    return [
        (f"{L['economic_code']}: {p['economic_code']}" if p.get("economic_code") else None),
        (f"{L['national_id']}: {p['national_id']}" if p.get("national_id") else None),
        locality,
        (f"{L['postal_code']}: {p['postal_code']}" if p.get("postal_code") else None),
    ]


# Each party box is named by its role on the document: an Iranian invoice says
# «فروشنده / خریدار», a receipt «دریافت‌کننده / پرداخت‌کننده»; one «صادرکننده /
# خریدار» pair on six documents named a statement's customer a buyer (Persian
# copy review, 2026-10-31).
ROLES = {
    "invoice": ("seller", "buyer"),
    "receipt": ("received_by", "payer"),
    "statement": ("from", "account_of"),
    "purchase_order": ("buyer", "supplier"),
    "payslip": ("employer", "employee"),
}


def _issuer_party(brand: dict, L: dict, label: str) -> dict:
    iss = brand["issuer"]
    return {
        "label": label, "name": iss["name"], "secondary": None,
        "lines": [iss.get("address"),
                  (f"{L['tax_id']}: {iss['tax_id']}" if iss.get("tax_id") else None),
                  *_identity_lines(iss, L),
                  iss.get("phone"), iss.get("email")],
    }


def _recipient_party(rec: dict | None, L: dict, label: str) -> dict:
    if not rec:
        return {"label": label, "name": "—", "secondary": None, "lines": []}
    return {
        "label": label, "name": rec["name"], "secondary": rec.get("secondary"),
        "lines": [rec.get("address"),
                  (f"{L['tax_id']}: {rec['tax_id']}" if rec.get("tax_id") else None),
                  *_identity_lines(rec, L),
                  rec.get("contact_person"), rec.get("phone"), rec.get("email")],
    }


def _signatures(brand: dict, L: dict) -> list[dict]:
    return [
        {"label": f"{L['signature']} — {brand['issuer']['name']}", "image": brand["issuer"].get("signature")},
        {"label": L["stamp"], "image": None},
    ]


def _money(n, ccy, loc):
    return fmt_money(n, ccy, loc)


def _parties(brand: dict, L: dict, doc: str, recipient: dict | None) -> list[dict]:
    """The issuer's box and the other party's, each labelled by its role on ``doc``."""
    ours, theirs = ROLES[doc]
    return [_issuer_party(brand, L, L[ours]), _recipient_party(recipient, L, L[theirs])]


def _percent(rate: str, loc) -> str:
    """A tax rate in the cell: «۹٪», «۹٫۵٪» on a Persian document («۹%», «۹.۵%» it printed)."""
    if loc != "ir":
        return rate
    from app.core.money import persian_figures
    return persian_figures(rate).replace("%", "٪")


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def render_invoice_pdf(db: Session, inv, party) -> bytes:
    return _render_invoice_like(db, inv, party)


def render_quote_pdf(db: Session, quote, party) -> bytes:
    """A quote (پیش‌فاکتور) uses the invoice layout with its own title, a
    "valid until" date instead of the due date and no payable block wording
    change — the total is what the customer will be invoiced."""
    return _render_invoice_like(db, quote, party, kind="quote")


def _render_invoice_like(db: Session, inv, party, *, kind: str = "invoice") -> bytes:
    brand, L, loc = _ctx(db)
    ccy = inv.currency or brand["base_currency"]
    items = list(inv.items or [])
    columns = [
        {"label": L["description"]}, {"label": L["qty"], "num": True},
        {"label": L["unit_price"], "num": True}, {"label": L["tax"], "num": True},
        {"label": L["amount"], "num": True},
    ]
    rows, subtotal = [], 0
    if not items:
        amt = plain(inv.amount or 0)
        rows.append({"cells": [
            {"value": inv.description or L["goods_services"]},
            {"value": fmt_digits("1", loc), "num": True},
            {"value": _money(amt, ccy, loc), "num": True},
            {"value": _percent("0%", loc), "num": True},
            {"value": _money(amt, ccy, loc), "num": True},
        ]})
        subtotal = amt
    for it in items:
        lt = plain(it.line_total or 0)
        subtotal += lt
        qty = f"{float(it.quantity or 1):g}"
        taxr = f"{float(getattr(it, 'tax_rate', 0) or 0):g}%"
        rows.append({"cells": [
            {"value": it.product_name or L["item"]},
            {"value": fmt_digits(qty, loc), "num": True},
            {"value": _money(it.unit_price or 0, ccy, loc), "num": True},
            {"value": _percent(taxr, loc), "num": True},
            {"value": _money(lt, ccy, loc), "num": True},
        ]})
    total = plain(inv.amount or subtotal)
    tax_total = max(0, total - subtotal)
    totals = [{"label": L["subtotal"], "value": _money(subtotal, ccy, loc)}]
    if tax_total:
        totals.append({"label": L["tax"], "value": _money(tax_total, ccy, loc)})
    is_quote = kind == "quote"
    ctx = {
        **brand,
        "title": L["quote"] if is_quote else L["invoice"],
        "labels": L,
        "meta": [
            # a document's number as it was typed: «INV-۱۲» here, «INV-12» on the statement and the phone
            {"label": L["number"], "value": inv.number},
            {"label": L["issue_date"], "value": fmt_date(inv.issue_date, loc)},
            ({"label": L["valid_until"], "value": fmt_date(inv.valid_until, loc)} if is_quote
             else {"label": L["due_date"], "value": fmt_date(inv.due_date, loc)}),
            {"label": L["status"], "value": status_label(inv.status or "issued", loc)},
        ],
        "parties": _parties(brand, L, "invoice", build_recipient(party)),
        "columns": columns, "rows": rows, "totals": totals,
        "payable": {"label": L["amount_payable"], "value": _money(total, ccy, loc)},
        "amount_words": amount_in_words(total, ccy, loc),
        "notes": inv.description if items else None,
        "signatures": _signatures(brand, L),
        "footer": brand["issuer"].get("footer"),
    }
    return render_pdf(ctx)


# ---------------------------------------------------------------------------
# Time invoice (grouped by project → worker)
# ---------------------------------------------------------------------------
def render_time_invoice_pdf(db: Session, inv, party, groups: list[dict], period: tuple | None) -> bytes:
    """groups: [{project, worker, hours, rate, amount}] ordered by project."""
    brand, L, loc = _ctx(db)
    ccy = inv.currency or brand["base_currency"]
    columns = [
        {"label": L["description"]}, {"label": L["hours"], "num": True},
        {"label": L["rate"], "num": True}, {"label": L["amount"], "num": True},
    ]
    rows, last_proj, total = [], None, 0
    for g in groups:
        if g["project"] != last_proj:
            rows.append({"group": g["project"]})
            last_proj = g["project"]
        amt = plain(g["amount"])
        total += amt
        rows.append({"cells": [
            {"value": g["worker"]},
            {"value": fmt_digits(f"{float(g['hours']):g}", loc), "num": True},
            {"value": _money(g["rate"], ccy, loc), "num": True},           # money, like the amount beside it
            {"value": _money(amt, ccy, loc), "num": True},
        ]})
    total = plain(inv.amount or total)
    meta = [
        {"label": L["number"], "value": inv.number},
        {"label": L["issue_date"], "value": fmt_date(inv.issue_date, loc)},
        {"label": L["due_date"], "value": fmt_date(inv.due_date, loc)},
    ]
    if period:
        meta.append({"label": L["period"], "value": f"{fmt_date(period[0], loc)} – {fmt_date(period[1], loc)}"})
    ctx = {
        **brand, "title": L["time_invoice"], "labels": L, "meta": meta,
        "parties": _parties(brand, L, "invoice", build_recipient(party)),
        "columns": columns, "rows": rows,
        "totals": [{"label": L["subtotal"], "value": _money(total, ccy, loc)}],
        "payable": {"label": L["amount_payable"], "value": _money(total, ccy, loc)},
        "amount_words": amount_in_words(total, ccy, loc),
        "signatures": _signatures(brand, L), "footer": brand["issuer"].get("footer"),
    }
    return render_pdf(ctx)


# ---------------------------------------------------------------------------
# Receipt (payment confirmation)
# ---------------------------------------------------------------------------
def render_receipt_pdf(db: Session, payment, inv, party, balance_after: int) -> bytes:
    brand, L, loc = _ctx(db)
    ccy = payment.currency or brand["base_currency"]
    amt = plain(payment.amount or 0)
    desc = f"{L['invoice']} {inv.number}" if inv else L["received_with_thanks"]
    ctx = {
        **brand, "title": L["receipt"], "labels": L,
        "meta": [
            {"label": L["number"], "value": str(payment.id)[:8].upper()},      # a code: hex letters and digits as they are
            {"label": L["date"], "value": fmt_date(payment.date, loc)},
            {"label": L["paid_by"], "value": method_label(payment.method, loc)},
        ],
        "parties": _parties(brand, L, "receipt", build_recipient(party)),
        "columns": [{"label": L["description"]}, {"label": L["amount"], "num": True}],
        "rows": [{"cells": [{"value": desc}, {"value": _money(amt, ccy, loc), "num": True}]}],
        "totals": [{"label": L["balance_due"], "value": _money(max(0, balance_after), ccy, loc)}],
        "payable": {"label": L["amount_paid"], "value": _money(amt, ccy, loc)},
        "amount_words": amount_in_words(amt, ccy, loc),
        "signatures": _signatures(brand, L),
        "footer": brand["issuer"].get("footer") or L["received_with_thanks"],
    }
    return render_pdf(ctx)


# ---------------------------------------------------------------------------
# Account statement (a client's invoices & payments with running balance)
# ---------------------------------------------------------------------------
def render_statement_pdf(db: Session, party, events: list[dict], period: tuple, ccy: str) -> bytes:
    """events: [{date, description, debit, credit}] in chronological order."""
    brand, L, loc = _ctx(db)
    ccy = ccy or brand["base_currency"]
    columns = [
        {"label": L["date"]}, {"label": L["description"]},
        {"label": L["debit"], "num": True}, {"label": L["credit"], "num": True},
        {"label": L["balance"], "num": True},
    ]
    rows, balance = [], 0
    for e in events:
        balance += plain(e.get("debit", 0)) - plain(e.get("credit", 0))
        rows.append({"cells": [
            {"value": fmt_date(e["date"], loc)},
            {"value": e.get("description", "")},
            {"value": _money(e.get("debit", 0), ccy, loc) if e.get("debit") else "", "num": True},
            {"value": _money(e.get("credit", 0), ccy, loc) if e.get("credit") else "", "num": True},
            {"value": _money(balance, ccy, loc), "num": True},
        ]})
    ctx = {
        **brand, "title": L["statement"], "labels": L,
        "meta": [{"label": L["period"], "value": f"{fmt_date(period[0], loc)} – {fmt_date(period[1], loc)}"}],
        "parties": _parties(brand, L, "statement", build_recipient(party)),
        "columns": columns, "rows": rows,
        "totals": [{"label": L["closing_balance"], "value": _money(balance, ccy, loc), "strong": True}],
        "payable": {"label": L["balance_due"], "value": _money(max(0, balance), ccy, loc)},
        "amount_words": amount_in_words(max(0, balance), ccy, loc),
        "signatures": _signatures(brand, L), "footer": brand["issuer"].get("footer"),
    }
    return render_pdf(ctx)


# ---------------------------------------------------------------------------
# Purchase order
# ---------------------------------------------------------------------------
def render_purchase_order_pdf(db: Session, po, supplier) -> bytes:
    brand, L, loc = _ctx(db)
    ccy = po.currency or brand["base_currency"]
    columns = [
        {"label": L["description"]}, {"label": L["qty"], "num": True},
        {"label": L["unit_price"], "num": True}, {"label": L["amount"], "num": True},
    ]
    rows, total = [], 0
    for ln in (po.lines or []):
        lt = plain(ln.line_total or 0)
        total += lt
        rows.append({"cells": [
            {"value": ln.description or L["item"]},
            {"value": fmt_digits(f"{float(ln.ordered_qty or 0):g}", loc), "num": True},
            {"value": _money(ln.unit_price or 0, ccy, loc), "num": True},
            {"value": _money(lt, ccy, loc), "num": True},
        ]})
    ctx = {
        **brand, "title": L["purchase_order"], "labels": L,
        "meta": [
            {"label": L["number"], "value": po.number},
            {"label": L["date"], "value": fmt_date(po.order_date, loc)},
            {"label": L["status"], "value": status_label(po.status or "draft", loc)},
        ],
        "parties": _parties(brand, L, "purchase_order", build_recipient(supplier)),
        "columns": columns, "rows": rows,
        "totals": [{"label": L["subtotal"], "value": _money(total, ccy, loc)}],
        "payable": {"label": L["total"], "value": _money(total, ccy, loc)},
        "amount_words": amount_in_words(total, ccy, loc),
        "notes": po.description, "signatures": _signatures(brand, L),
        "footer": brand["issuer"].get("footer"),
    }
    return render_pdf(ctx)


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------
def _paid_to_line(line, employee, L: dict) -> str | None:
    """'Paid to: Bank · IBAN/account' for the payslip. Uses the pay-time
    snapshot (line.paid_to) when present, else the employee's current bank
    fields; None when neither exists (the row is simply omitted)."""
    dest = (getattr(line, "paid_to", None) or "").strip()
    if not dest and employee is not None:
        parts = [p for p in (
            getattr(employee, "bank_name", None),
            (getattr(employee, "iban", None) or "").strip() or (getattr(employee, "account_number", None) or "").strip() or None,
        ) if p]
        dest = " · ".join(parts)
    return f"{L['paid_to']}: {dest}" if dest else None


def render_payslip_pdf(db: Session, run, line, employee) -> bytes:
    brand, L, loc = _ctx(db)
    ccy = run.currency or brand["base_currency"]
    columns = [{"label": L["description"]}, {"label": L["amount"], "num": True}]
    rows = []
    # Hours breakdown (hourly staff): regular / overtime / paid leave.
    _hrs = [
        ("regular_hours", float(line.hours or 0)),
        ("overtime_hours", float(line.overtime_hours or 0)),
        ("leave_hours", float(getattr(line, "leave_hours", 0) or 0)),
    ]
    for key, h in _hrs:
        if h > 0:
            rows.append({"cells": [
                {"value": f"{L[key]}: {fmt_digits(f'{h:g}', loc)} {L['hours_unit']}"}, {"value": "", "num": True},
            ]})
    year_end = (getattr(run, "kind", None) or "regular") == "year_end"
    if year_end:                                    # عیدی and سنوات, then the gross they make (§3.3)
        from app.services.documents.formatting import to_persian_digits
        days = str(int(getattr(line, "days_worked", 0) or 0))
        rows.append({"cells": [{"value": f"{L['days_worked']}: {to_persian_digits(days) if loc == 'ir' else days}"},
                               {"value": "", "num": True}]})
        for key in ("eidi", "sanavat"):
            if plain(getattr(line, key, 0) or 0):
                rows.append({"cells": [{"value": L[key]}, {"value": _money(getattr(line, key), ccy, loc), "num": True}]})
    rows.append(
        {"cells": [{"value": L["gross_pay"]}, {"value": _money(line.gross or 0, ccy, loc), "num": True}]},
    )
    deductions = [
        ("income_tax", line.income_tax), ("social_security", line.social_security),
        ("deductions", line.pre_tax_deductions),
    ]
    total_ded = 0
    for key, val in deductions:
        v = plain(val or 0)
        if v:
            total_ded += v
            rows.append({"cells": [{"value": L.get(key, key)}, {"value": "-" + _money(v, ccy, loc), "num": True}]})
    net = plain(line.net_pay or 0)
    ctx = {
        **brand, "title": L["year_end_payslip"] if year_end else L["payslip"], "labels": L,
        "meta": [
            {"label": L["pay_period"], "value": f"{fmt_date(run.period_start, loc)} – {fmt_date(run.period_end, loc)}"},
            {"label": L["date"], "value": fmt_date(run.pay_date, loc)},
        ],
        "parties": [_issuer_party(brand, L, L[ROLES["payslip"][0]]),
                    {"label": L[ROLES["payslip"][1]], "name": line.employee_name or (employee.name if employee else "—"),
                     "secondary": None,
                     # Disbursement destination: the snapshot taken at pay time
                     # wins; before payment fall back to the employee's current
                     # bank fields so a draft payslip still previews it.
                     "lines": [_paid_to_line(line, employee, L)]}],
        "columns": columns, "rows": rows,
        "totals": [
            {"label": L["gross_pay"], "value": _money(line.gross or 0, ccy, loc)},
            {"label": L["deductions"], "value": _money(total_ded, ccy, loc)},
        ],
        "payable": {"label": L["net_pay"], "value": _money(net, ccy, loc)},
        "amount_words": amount_in_words(net, ccy, loc),
        "signatures": _signatures(brand, L), "footer": brand["issuer"].get("footer"),
    }
    return render_pdf(ctx)
