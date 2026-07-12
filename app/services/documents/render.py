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
from app.services.documents.labels import labels_for


def _ctx(db: Session) -> tuple[dict, dict, str]:
    brand = build_brand(db)
    L = labels_for(brand["locale"])
    return brand, L, brand["locale"]


def _issuer_party(brand: dict, L: dict) -> dict:
    iss = brand["issuer"]
    return {
        "label": L["from"], "name": iss["name"], "secondary": None,
        "lines": [iss.get("address"),
                  (f"{L['tax_id']}: {iss['tax_id']}" if iss.get("tax_id") else None),
                  iss.get("phone"), iss.get("email")],
    }


def _recipient_party(rec: dict | None, L: dict, label: str) -> dict:
    if not rec:
        return {"label": label, "name": "—", "secondary": None, "lines": []}
    return {
        "label": label, "name": rec["name"], "secondary": rec.get("secondary"),
        "lines": [rec.get("address"),
                  (f"{L['tax_id']}: {rec['tax_id']}" if rec.get("tax_id") else None),
                  rec.get("contact_person"), rec.get("phone"), rec.get("email")],
    }


def _signatures(brand: dict, L: dict) -> list[dict]:
    return [
        {"label": f"{L['signature']} — {brand['issuer']['name']}", "image": brand["issuer"].get("signature")},
        {"label": L["stamp"], "image": None},
    ]


def _money(n, ccy, loc):
    return fmt_money(n, ccy, loc)


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def render_invoice_pdf(db: Session, inv, party) -> bytes:
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
        amt = int(inv.amount or 0)
        rows.append({"cells": [
            {"value": inv.description or "Service / Product"},
            {"value": fmt_digits("1", loc), "num": True},
            {"value": _money(amt, ccy, loc), "num": True},
            {"value": fmt_digits("0%", loc), "num": True},
            {"value": _money(amt, ccy, loc), "num": True},
        ]})
        subtotal = amt
    for it in items:
        lt = int(it.line_total or 0)
        subtotal += lt
        qty = f"{float(it.quantity or 1):g}"
        taxr = f"{float(getattr(it, 'tax_rate', 0) or 0):g}%"
        rows.append({"cells": [
            {"value": it.product_name or "Item"},
            {"value": fmt_digits(qty, loc), "num": True},
            {"value": _money(it.unit_price or 0, ccy, loc), "num": True},
            {"value": fmt_digits(taxr, loc), "num": True},
            {"value": _money(lt, ccy, loc), "num": True},
        ]})
    total = int(inv.amount or subtotal)
    tax_total = max(0, total - subtotal)
    totals = [{"label": L["subtotal"], "value": _money(subtotal, ccy, loc)}]
    if tax_total:
        totals.append({"label": L["tax"], "value": _money(tax_total, ccy, loc)})
    ctx = {
        **brand,
        "title": L["invoice"],
        "labels": L,
        "meta": [
            {"label": L["number"], "value": fmt_digits(inv.number, loc)},
            {"label": L["issue_date"], "value": fmt_date(inv.issue_date, loc)},
            {"label": L["due_date"], "value": fmt_date(inv.due_date, loc)},
            {"label": L["status"], "value": (inv.status or "issued")},
        ],
        "parties": [_issuer_party(brand, L), _recipient_party(build_recipient(party), L, L["bill_to"])],
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
        amt = int(g["amount"])
        total += amt
        rows.append({"cells": [
            {"value": g["worker"]},
            {"value": fmt_digits(f"{float(g['hours']):g}", loc), "num": True},
            {"value": fmt_digits(f"{float(g['rate']):g}", loc), "num": True},
            {"value": _money(amt, ccy, loc), "num": True},
        ]})
    total = int(inv.amount or total)
    meta = [
        {"label": L["number"], "value": fmt_digits(inv.number, loc)},
        {"label": L["issue_date"], "value": fmt_date(inv.issue_date, loc)},
        {"label": L["due_date"], "value": fmt_date(inv.due_date, loc)},
    ]
    if period:
        meta.append({"label": L["period"], "value": f"{fmt_date(period[0], loc)} – {fmt_date(period[1], loc)}"})
    ctx = {
        **brand, "title": L["time_invoice"], "labels": L, "meta": meta,
        "parties": [_issuer_party(brand, L), _recipient_party(build_recipient(party), L, L["bill_to"])],
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
    amt = int(payment.amount or 0)
    desc = f"{L['invoice']} {inv.number}" if inv else L["received_with_thanks"]
    ctx = {
        **brand, "title": L["receipt"], "labels": L,
        "meta": [
            {"label": L["number"], "value": fmt_digits(str(payment.id)[:8].upper(), loc)},
            {"label": L["date"], "value": fmt_date(payment.date, loc)},
            {"label": L["payment_terms"], "value": (payment.method or "bank")},
        ],
        "parties": [_issuer_party(brand, L), _recipient_party(build_recipient(party), L, L["bill_to"])],
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
        balance += int(e.get("debit", 0)) - int(e.get("credit", 0))
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
        "parties": [_issuer_party(brand, L), _recipient_party(build_recipient(party), L, L["bill_to"])],
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
        lt = int(ln.line_total or 0)
        total += lt
        rows.append({"cells": [
            {"value": ln.description or "Item"},
            {"value": fmt_digits(f"{float(ln.ordered_qty or 0):g}", loc), "num": True},
            {"value": _money(ln.unit_price or 0, ccy, loc), "num": True},
            {"value": _money(lt, ccy, loc), "num": True},
        ]})
    ctx = {
        **brand, "title": L["purchase_order"], "labels": L,
        "meta": [
            {"label": L["number"], "value": fmt_digits(po.number, loc)},
            {"label": L["date"], "value": fmt_date(po.order_date, loc)},
            {"label": L["status"], "value": (po.status or "draft")},
        ],
        "parties": [_issuer_party(brand, L), _recipient_party(build_recipient(supplier), L, L["supplier"])],
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
def render_payslip_pdf(db: Session, run, line, employee) -> bytes:
    brand, L, loc = _ctx(db)
    ccy = run.currency or brand["base_currency"]
    columns = [{"label": L["description"]}, {"label": L["amount"], "num": True}]
    rows = []
    # Hours breakdown (hourly staff): regular / overtime / paid leave.
    _hrs = [
        ("regular_hours", "Regular hours", float(line.hours or 0)),
        ("overtime_hours", "Overtime hours", float(line.overtime_hours or 0)),
        ("leave_hours", "Paid leave hours", float(getattr(line, "leave_hours", 0) or 0)),
    ]
    for key, fallback, h in _hrs:
        if h > 0:
            rows.append({"cells": [
                {"value": f"{L.get(key, fallback)}: {h:g} h"}, {"value": "", "num": True},
            ]})
    rows.append(
        {"cells": [{"value": L["gross_pay"]}, {"value": _money(line.gross or 0, ccy, loc), "num": True}]},
    )
    deductions = [
        ("income_tax", line.income_tax), ("social_security", line.social_security),
        ("deductions", line.pre_tax_deductions),
    ]
    total_ded = 0
    for key, val in deductions:
        v = int(val or 0)
        if v:
            total_ded += v
            rows.append({"cells": [{"value": L.get(key, key)}, {"value": "-" + _money(v, ccy, loc), "num": True}]})
    net = int(line.net_pay or 0)
    ctx = {
        **brand, "title": L["payslip"], "labels": L,
        "meta": [
            {"label": L["pay_period"], "value": f"{fmt_date(run.period_start, loc)} – {fmt_date(run.period_end, loc)}"},
            {"label": L["date"], "value": fmt_date(run.pay_date, loc)},
        ],
        "parties": [_issuer_party(brand, L),
                    {"label": L["employee"], "name": line.employee_name or (employee.name if employee else "—"),
                     "secondary": None, "lines": []}],
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
