"""Invoice tools for the AI accountant (roadmap 2026-09 §5.1 — no tool
touched invoices before, so "which invoices are overdue?" and "record Acme's
payment of invoice 1042" were invisible to the assistant).

Read: list_invoices, get_invoice. Proposals (confirm-gated, executed in
invoice_execute.py through the same invoice code the UI uses):
propose_record_invoice_payment, propose_create_invoice.
"""
from __future__ import annotations

import uuid
from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _find_entity, _register


def _totals(ctx: ToolContext, inv: Invoice) -> tuple[int, int, int, int]:
    from app.api.invoices import _invoice_totals, _overpayment
    paid, credited, balance = _invoice_totals(ctx.db, inv)
    return paid, credited, balance, _overpayment(ctx.db, inv)


def _read(ctx: ToolContext, inv: Invoice, today: _date) -> dict[str, Any]:
    paid, credited, balance, overpaid = _totals(ctx, inv)
    ent = ctx.db.get(Entity, inv.entity_id) if inv.entity_id else None
    return {
        "id": str(inv.id), "number": inv.number, "kind": inv.kind, "status": inv.status,
        "party": ent.name if ent else None, "issue_date": inv.issue_date.isoformat(),
        "due_date": inv.due_date.isoformat(), "amount": int(inv.amount or 0), "currency": inv.currency,
        "amount_paid": paid, "credited": credited, "balance_due": balance, "overpaid": overpaid,
        "overdue_days": max(0, (today - inv.due_date).days) if balance > 0 and inv.status not in ("draft", "voided", "canceled") else 0,
    }


def find_invoice(ctx: ToolContext, ref: str) -> Invoice:
    """Resolve an invoice by id or number (exact, then contains)."""
    ref = (ref or "").strip()
    if not ref:
        raise ToolError("invoice reference is empty", code="invoice_not_found")
    try:
        row = ctx.db.get(Invoice, uuid.UUID(ref))
        if row:
            return row
    except ValueError:
        pass
    rows = ctx.db.execute(select(Invoice).where(Invoice.number.ilike(ref))).scalars().all()
    if not rows:
        rows = ctx.db.execute(select(Invoice).where(Invoice.number.ilike(f"%{ref}%"))).scalars().all()
    if not rows:
        raise ToolError(f"No invoice matches {ref!r}", code="invoice_not_found")
    if len(rows) > 1:
        raise ToolError(f"{len(rows)} invoices match {ref!r}: " + ", ".join(r.number for r in rows[:6]) + " — ask which one.",
                        code="invoice_ambiguous")
    return rows[0]


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

class ListInvoicesInput(BaseModel):
    kind: str | None = Field(None, description="sales (what customers owe us) or purchase (what we owe suppliers). Omit for both.")
    open_only: bool = Field(True, description="Only invoices with a balance still due (default). False lists paid/voided too.")
    overdue_only: bool = Field(False, description="Only invoices past their due date with a balance.")
    party: str | None = Field(None, description="Customer/supplier name to filter by.")
    limit: int = Field(25, ge=1, le=200)


class ListInvoices(BaseTool):
    name = "list_invoices"
    category = "read"
    description = (
        "Invoices with their balances: open, overdue (days late), paid, per customer or supplier. "
        "Use for 'which invoices are overdue?', 'what does Acme owe us?', 'how much do we owe suppliers?', "
        "'show invoice 1042'. Amounts are in each invoice's currency; totals are per currency, never mixed. Pure read."
    )
    InputSchema = ListInvoicesInput

    async def run(self, ctx: ToolContext, args: ListInvoicesInput) -> dict[str, Any]:
        today = _date.today()
        q = select(Invoice)
        if args.kind:
            q = q.where(Invoice.kind == args.kind.strip().lower())
        if args.party:
            ent = _find_entity(ctx, args.party, ("client", "supplier", "customer"))
            if ent is None:
                raise ToolError(f"No customer or supplier named {args.party!r}", code="entity_not_found")
            q = q.where(Invoice.entity_id == ent.id)
        rows = ctx.db.execute(q.order_by(Invoice.due_date)).scalars().all()
        out = []
        for inv in rows:
            r = _read(ctx, inv, today)
            if args.open_only and (r["balance_due"] <= 0 or inv.status in ("draft", "voided", "canceled")):
                continue
            if args.overdue_only and r["overdue_days"] <= 0:
                continue
            out.append(r)
        totals: dict[str, dict[str, int]] = {}
        for r in out:
            t = totals.setdefault(r["currency"], {"balance_due": 0, "count": 0, "overdue_balance": 0})
            t["balance_due"] += r["balance_due"]; t["count"] += 1
            if r["overdue_days"]:
                t["overdue_balance"] += r["balance_due"]
        return {"invoices": out[: args.limit], "count": len(out), "totals_by_currency": totals, "today": today.isoformat()}


class GetInvoiceInput(BaseModel):
    invoice: str = Field(..., description="Invoice number or id.")


class GetInvoice(BaseTool):
    name = "get_invoice"
    category = "read"
    description = "One invoice in full: party, dates, amount, payments so far, balance due, over-payment credit. Pure read."
    InputSchema = GetInvoiceInput

    async def run(self, ctx: ToolContext, args: GetInvoiceInput) -> dict[str, Any]:
        from app.models.payment import Payment
        inv = find_invoice(ctx, args.invoice)
        r = _read(ctx, inv, _date.today())
        pays = ctx.db.execute(select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.date)).scalars().all()
        r["payments"] = [{"id": str(p.id), "date": p.date.isoformat(), "amount": int(p.amount or 0),
                          "method": getattr(p, "method", None), "reference": getattr(p, "reference", None)} for p in pays]
        return r


# ---------------------------------------------------------------------------
# PROPOSALS
# ---------------------------------------------------------------------------

class ProposeRecordInvoicePaymentInput(BaseModel):
    invoice: str = Field(..., description="Invoice number or id being paid.")
    amount: int = Field(..., gt=0, description="Whole currency units in the invoice's currency.")
    date: _date | None = Field(None, description="Payment date (defaults to today).")
    method: str = Field("bank", description="bank | cash | transfer")
    bank_account_code: str | None = Field(None, description="Cash/bank account code the money moved through (optional; the default bank is used).")
    reference: str | None = None


class ProposeRecordInvoicePayment(BaseTool):
    name = "propose_record_invoice_payment"
    category = "proposal"
    description = (
        "Record a payment against an EXISTING invoice — a customer paying our sales invoice or us paying a "
        "supplier's purchase invoice. Use this (not propose_create_transaction) whenever the user names an "
        "invoice; it clears the receivable/payable and books any excess as customer credit / supplier advance. "
        "Returns a confirm card; nothing is posted until the user confirms."
    )
    InputSchema = ProposeRecordInvoicePaymentInput

    async def run(self, ctx: ToolContext, args: ProposeRecordInvoicePaymentInput) -> dict[str, Any]:
        inv = find_invoice(ctx, args.invoice)
        if inv.status in ("voided", "canceled", "draft"):
            raise ToolError(f"Invoice {inv.number} is {inv.status}; it cannot take a payment.", code="invoice_not_payable")
        _paid, _cred, balance, _over = _totals(ctx, inv)
        when = args.date or _date.today()
        excess = max(0, args.amount - balance)
        payload = {"invoice_id": str(inv.id), "invoice_number": inv.number, "amount": args.amount,
                   "date": when.isoformat(), "method": args.method, "bank_account_code": args.bank_account_code,
                   "reference": args.reference, "currency": inv.currency}
        token = _register(ctx, self.name, payload)
        direction = "received from" if inv.kind == "sales" else "paid to"
        ent = ctx.db.get(Entity, inv.entity_id) if inv.entity_id else None
        summary = (f"Payment of {args.amount:,} {inv.currency} {direction} {ent.name if ent else 'the party'} "
                   f"for invoice {inv.number} on {when.isoformat()} ({args.method}). Balance before: {balance:,}; after: {max(0, balance - args.amount):,}.")
        if excess:
            summary += f" The {excess:,} above the balance is booked as {'customer credit' if inv.kind == 'sales' else 'a supplier advance'}."
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "preview": {**payload, "balance_before": balance, "balance_after": max(0, balance - args.amount), "excess_to_credit": excess}}


class InvoiceLineIn(BaseModel):
    description: str = Field(..., min_length=1)
    quantity: float = Field(1, gt=0)
    unit_price: int = Field(..., ge=0)
    tax_rate: float = Field(0, ge=0, le=100)


class ProposeCreateInvoiceInput(BaseModel):
    kind: str = Field("sales", description="sales (we bill a customer) or purchase (a supplier bills us).")
    party: str = Field(..., description="Customer (sales) or supplier (purchase) name — must already exist.")
    lines: list[InvoiceLineIn] = Field(..., min_length=1)
    issue_date: _date | None = None
    due_date: _date | None = Field(None, description="Defaults to issue date + 30 days.")
    currency: str | None = None
    number: str | None = Field(None, description="Invoice number; generated when omitted.")
    description: str | None = None


class ProposeCreateInvoice(BaseTool):
    name = "propose_create_invoice"
    category = "proposal"
    description = (
        "Draft a sales or purchase invoice with line items for an existing customer/supplier and issue it "
        "(the receivable/payable is recognised on confirm). Use for 'invoice Acme 3 million for consulting', "
        "'record the supplier's bill of 500 for paper'. Returns a confirm card."
    )
    InputSchema = ProposeCreateInvoiceInput

    async def run(self, ctx: ToolContext, args: ProposeCreateInvoiceInput) -> dict[str, Any]:
        kind = args.kind.strip().lower()
        if kind not in ("sales", "purchase"):
            raise ToolError("kind must be sales or purchase", code="bad_kind")
        ent = _find_entity(ctx, args.party, ("client", "customer") if kind == "sales" else ("supplier",))
        if ent is None:
            raise ToolError(f"No {'customer' if kind == 'sales' else 'supplier'} named {args.party!r} — create the party first.", code="entity_not_found")
        from datetime import timedelta
        from app.services.fx_service import get_reporting_currency
        issue = args.issue_date or _date.today()
        due = args.due_date or issue + timedelta(days=30)
        currency = (args.currency or get_reporting_currency(ctx.db) or "IRR").upper()
        number = (args.number or f"{'INV' if kind == 'sales' else 'BILL'}-{issue.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}").strip()
        lines = [l.model_dump() for l in args.lines]
        subtotal = sum(int(round(l["quantity"] * l["unit_price"])) for l in lines)
        tax = sum(int(round(l["quantity"] * l["unit_price"] * l["tax_rate"] / 100)) for l in lines)
        payload = {"kind": kind, "entity_id": str(ent.id), "party": ent.name, "number": number,
                   "issue_date": issue.isoformat(), "due_date": due.isoformat(), "currency": currency,
                   "lines": lines, "description": args.description}
        token = _register(ctx, self.name, payload)
        summary = (f"{'Sales invoice' if kind == 'sales' else 'Purchase invoice'} {number} for {ent.name}: "
                   f"{subtotal:,} {currency}" + (f" + tax {tax:,}" if tax else "") + f", issued {issue.isoformat()}, due {due.isoformat()}; "
                   + "; ".join(f"{l['description']} × {l['quantity']:g} @ {l['unit_price']:,}" for l in lines))
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "preview": {**payload, "subtotal": subtotal, "tax": tax, "total": subtotal + tax}}


def register_invoice_tools(registry) -> None:
    registry.register(ListInvoices())
    registry.register(GetInvoice())
    registry.register(ProposeRecordInvoicePayment())
    registry.register(ProposeCreateInvoice())
