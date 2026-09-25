"""Cheque and installment tools for the AI accountant (roadmap 2026-09 §5.1).

Read: list_commitments. Proposals (confirm-gated): propose_settle_commitment
(an installment paid / a cheque cleared), propose_bounce_cheque,
propose_create_cheque. Executed in invoice_execute.py through the same
commitment service the UI uses.
"""
from __future__ import annotations

import uuid
from datetime import date as _date, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.commitment import Commitment

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register


def _row(c: Commitment, today: _date) -> dict[str, Any]:
    return {
        "id": str(c.id), "kind": c.kind, "direction": c.direction, "title": c.title, "amount": int(c.amount or 0),
        "due_date": c.due_date.isoformat(), "status": c.status, "reference": c.reference, "bank_name": c.bank_name,
        "counterparty": c.counterparty, "sequence": c.sequence, "plan_total": c.plan_total,
        "days_until_due": (c.due_date - today).days, "overdue": c.status in ("pending", "bounced") and c.due_date < today,
    }


def find_commitment(ctx: ToolContext, ref: str) -> Commitment:
    ref = (ref or "").strip()
    try:
        row = ctx.db.get(Commitment, uuid.UUID(ref))
        if row:
            return row
    except ValueError:
        pass
    rows = ctx.db.execute(select(Commitment).where(
        (Commitment.reference.ilike(ref)) | (Commitment.title.ilike(f"%{ref}%")))
    ).scalars().all()
    open_rows = [r for r in rows if r.status in ("pending", "bounced")] or rows
    if not open_rows:
        raise ToolError(f"No cheque or installment matches {ref!r}", code="commitment_not_found")
    if len(open_rows) > 1:
        raise ToolError(f"{len(open_rows)} items match {ref!r}: " + "; ".join(
            f"{r.title} ({r.amount:,}, due {r.due_date.isoformat()}, id {str(r.id)[:8]})" for r in open_rows[:5]) + " — ask which one.",
            code="commitment_ambiguous")
    return open_rows[0]


class ListCommitmentsInput(BaseModel):
    kind: str | None = Field(None, description="cheque or installment; omit for both.")
    direction: str | None = Field(None, description="pay (we owe) or receive (owed to us); omit for both.")
    days_ahead: int = Field(30, ge=0, le=3650, description="Show items due within this many days (plus everything overdue).")
    include_settled: bool = Field(False)


class ListCommitments(BaseTool):
    name = "list_commitments"
    category = "read"
    description = (
        "Cheques and installments (اقساط / چک): what is due when, overdue items, bounced cheques, totals payable "
        "and receivable. Use for 'which cheques are due this month?', 'do I have an installment this week?', "
        "'what's outstanding on the loan?'. Pure read."
    )
    InputSchema = ListCommitmentsInput

    async def run(self, ctx: ToolContext, args: ListCommitmentsInput) -> dict[str, Any]:
        today = _date.today()
        q = select(Commitment)
        if args.kind:
            q = q.where(Commitment.kind == args.kind.strip().lower())
        if args.direction:
            q = q.where(Commitment.direction == args.direction.strip().lower())
        if not args.include_settled:
            q = q.where(Commitment.status.in_(["pending", "bounced"]))
        rows = ctx.db.execute(q.order_by(Commitment.due_date)).scalars().all()
        horizon = today + timedelta(days=args.days_ahead)
        items = [_row(c, today) for c in rows if c.status not in ("pending", "bounced") or c.due_date <= horizon]
        payable = sum(i["amount"] for i in items if i["direction"] == "pay" and i["status"] in ("pending", "bounced"))
        receivable = sum(i["amount"] for i in items if i["direction"] == "receive" and i["status"] in ("pending", "bounced"))
        return {"items": items, "count": len(items), "payable": payable, "receivable": receivable,
                "overdue_count": sum(1 for i in items if i["overdue"]), "bounced_count": sum(1 for i in items if i["status"] == "bounced"),
                "today": today.isoformat(), "horizon": horizon.isoformat()}


class ProposeSettleCommitmentInput(BaseModel):
    commitment: str = Field(..., description="Cheque/installment id, cheque reference number, or a distinctive part of its title.")
    date: _date | None = Field(None, description="Settlement date (defaults to today).")


class ProposeSettleCommitment(BaseTool):
    name = "propose_settle_commitment"
    category = "proposal"
    description = (
        "Mark an installment as paid or a cheque as cleared and post the ledger entry (bank ↔ the item's counter "
        "account). Use for 'the June installment was paid', 'cheque 1234 cleared'. Returns a confirm card."
    )
    InputSchema = ProposeSettleCommitmentInput

    async def run(self, ctx: ToolContext, args: ProposeSettleCommitmentInput) -> dict[str, Any]:
        c = find_commitment(ctx, args.commitment)
        if c.status == "settled":
            raise ToolError(f"{c.title} was already settled on {c.settled_on}", code="already_settled")
        when = args.date or _date.today()
        payload = {"commitment_id": str(c.id), "title": c.title, "amount": int(c.amount or 0), "date": when.isoformat(),
                   "direction": c.direction, "kind": c.kind}
        token = _register(ctx, self.name, payload)
        verb = "paid" if c.direction == "pay" else "received"
        summary = f"{c.kind.capitalize()} '{c.title}' ({c.amount:,}, due {c.due_date.isoformat()}) {verb} on {when.isoformat()}"
        if not c.counter_account_code:
            summary += " — no counter account on the item, so it is marked settled without a ledger entry."
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


class ProposeBounceChequeInput(BaseModel):
    cheque: str = Field(..., description="Cheque id, reference number, or a distinctive part of its title.")


class ProposeBounceCheque(BaseTool):
    name = "propose_bounce_cheque"
    category = "proposal"
    description = "Mark a cheque as bounced (برگشت خورد): it stays outstanding, never settled. Returns a confirm card."
    InputSchema = ProposeBounceChequeInput

    async def run(self, ctx: ToolContext, args: ProposeBounceChequeInput) -> dict[str, Any]:
        c = find_commitment(ctx, args.cheque)
        if c.kind != "cheque":
            raise ToolError(f"'{c.title}' is an installment, not a cheque.", code="not_a_cheque")
        payload = {"commitment_id": str(c.id), "title": c.title, "amount": int(c.amount or 0)}
        token = _register(ctx, self.name, payload)
        return {"confirmation_token": str(token), "status": "pending",
                "summary": f"Cheque '{c.title}' ({c.amount:,}, due {c.due_date.isoformat()}) marked bounced — still owed.", "preview": payload}


class ProposeCreateChequeInput(BaseModel):
    title: str = Field(..., min_length=1, description="What the cheque is for / who it is with.")
    amount: int = Field(..., gt=0)
    due_date: _date
    direction: str = Field("pay", description="pay = a cheque we issued; receive = a cheque we hold from a customer.")
    reference: str | None = Field(None, description="Cheque number / Sayad id.")
    bank_name: str | None = None
    counterparty: str | None = None
    counter_account_code: str | None = Field(None, description="Expense/receivable account it settles against (optional).")


class ProposeCreateCheque(BaseTool):
    name = "propose_create_cheque"
    category = "proposal"
    description = "Register a post-dated cheque (issued or received) so it shows in the due list and reminders. Returns a confirm card."
    InputSchema = ProposeCreateChequeInput

    async def run(self, ctx: ToolContext, args: ProposeCreateChequeInput) -> dict[str, Any]:
        d = args.direction.strip().lower()
        if d not in ("pay", "receive"):
            raise ToolError("direction must be pay or receive", code="bad_direction")
        payload = args.model_dump()
        payload["due_date"] = args.due_date.isoformat(); payload["direction"] = d
        token = _register(ctx, self.name, payload)
        who = "issued" if d == "pay" else "received"
        return {"confirmation_token": str(token), "status": "pending",
                "summary": f"Cheque {who}: '{args.title}' {args.amount:,} due {args.due_date.isoformat()}" + (f", no. {args.reference}" if args.reference else ""),
                "preview": payload}


def register_commitment_tools(registry, *, personal: bool = False) -> None:
    registry.register(ListCommitments())
    registry.register(ProposeSettleCommitment())
    if not personal:
        registry.register(ProposeBounceCheque())
        registry.register(ProposeCreateCheque())
