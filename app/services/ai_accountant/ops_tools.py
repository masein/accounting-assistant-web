"""The last modules the chat couldn't reach (roadmap 2026-09 §5.1): purchase
orders, recurring rules, the cap table, exchange rates, petty cash, the audit
trail — read — and two confirm-gated actions: lock the books through a date
(owner only, as on the Settings page) and set up a recurring payment or
receipt. Executed in ops_execute.py through the same route code the pages use.
"""
from __future__ import annotations

import uuid
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _find_entity, _register


def user_role(ctx_db, user_id: str | None) -> str | None:
    """The chat user's role, read from their account (the tool context carries none)."""
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    try:
        uid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return None
    with tenant_bypass():
        return ctx_db.execute(select(User.role).where(User.id == uid)).scalar()


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

class ListPurchaseOrdersInput(BaseModel):
    status: str | None = Field(None, description="draft | issued | partially_received | received | closed | cancelled")
    supplier: str | None = Field(None, description="Supplier name.")
    open_only: bool = Field(False, description="Only orders still waiting for goods or a bill.")
    limit: int = Field(20, ge=1, le=100)


class ListPurchaseOrders(BaseTool):
    name = "list_purchase_orders"
    category = "read"
    description = (
        "Purchase orders: number, supplier, dates, status, total, and how much is received and billed. Use for "
        "'what have we ordered from Delta?', 'which orders are still open?', 'has PO-12 been billed?'. Pure read."
    )
    InputSchema = ListPurchaseOrdersInput

    async def run(self, ctx: ToolContext, args: ListPurchaseOrdersInput) -> dict[str, Any]:
        from app.api.purchase_orders import _po_read
        from app.models.purchase_order import PurchaseOrder
        q = select(PurchaseOrder).order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.created_at.desc())
        if args.status:
            q = q.where(PurchaseOrder.status == args.status.strip().lower())
        if args.open_only:
            q = q.where(PurchaseOrder.status.notin_(("closed", "cancelled")))
        if args.supplier:
            ent = _find_entity(ctx, args.supplier, ("supplier",))
            if ent is None:
                raise ToolError(f"No supplier named {args.supplier!r}", code="entity_not_found")
            q = q.where(PurchaseOrder.entity_id == ent.id)
        rows = [_po_read(po, ctx.db) for po in ctx.db.execute(q.limit(args.limit)).scalars()]
        for r in rows:
            r["lines"] = [{k: ln.get(k) for k in ("description", "ordered_qty", "received_qty", "billed_qty", "unit_price")}
                          for ln in r["lines"]]
        return {"purchase_orders": rows, "count": len(rows)}


class ListRecurringRulesInput(BaseModel):
    active_only: bool = Field(False, description="Only active rules.")


class NoInput(BaseModel):
    pass


class ListRecurringRules(BaseTool):
    name = "list_recurring_rules"
    category = "read"
    description = (
        "Recurring payments and receipts set up to post on a schedule: name, payment or receipt, how often, amount, "
        "next date, active or paused. Use for 'what's set to go out every month?', 'when is rent next posted?'. Pure read."
    )
    InputSchema = ListRecurringRulesInput

    async def run(self, ctx: ToolContext, args: ListRecurringRulesInput) -> dict[str, Any]:
        from app.models.recurring import RecurringRule
        q = select(RecurringRule).order_by(RecurringRule.next_run_date)
        if args.active_only:
            q = q.where(RecurringRule.status == "active")
        rules = [{"id": str(r.id), "name": r.name, "direction": r.direction, "frequency": r.frequency,
                  "amount": int(r.amount) if r.amount is not None else None,
                  "next_run_date": r.next_run_date.isoformat() if r.next_run_date else None,
                  "end_date": r.end_date.isoformat() if r.end_date else None, "status": r.status,
                  "counter_account_code": r.counter_account_code, "bank_account_code": r.bank_account_code,
                  "auto_post": bool(r.auto_post)} for r in ctx.db.execute(q).scalars()]
        return {"rules": rules, "count": len(rules)}


class GetCapTable(BaseTool):
    name = "get_cap_table"
    category = "read"
    description = (
        "The cap table: each shareholder's shares and %, capital paid in, dividends declared, paid and still owed, "
        "and the registered capital. Use for 'who owns the company?', 'what dividends do we still owe Ali?'. Pure read."
    )
    InputSchema = NoInput

    async def run(self, ctx: ToolContext, args: NoInput) -> dict[str, Any]:
        from app.api.equity import cap_table
        return cap_table(ctx.db).model_dump(mode="json")


class GetExchangeRatesInput(BaseModel):
    currency: str | None = Field(None, description="Only this currency (e.g. USD).")


class GetExchangeRates(BaseTool):
    name = "get_exchange_rates"
    category = "read"
    description = (
        "The latest exchange rate of each currency pair on file (this company's and the shared feed's), with its "
        "date, and how many entries are still waiting for a rate. Use for 'what dollar rate are we using?', 'is the "
        "euro rate up to date?'. Pure read."
    )
    InputSchema = GetExchangeRatesInput

    async def run(self, ctx: ToolContext, args: GetExchangeRatesInput) -> dict[str, Any]:
        from app.api.fx import list_rates
        from app.services.fx_base import base_currency, pending_summary
        ccy = args.currency.strip().upper() if args.currency else None
        rows = list_rates(from_currency=ccy, to_currency=None, latest=True, limit=200, db=ctx.db)
        return {"base_currency": base_currency(ctx.db),
                "rates": [{"from": r.from_currency, "to": r.to_currency, "rate": float(r.rate),
                           "date": r.effective_date.isoformat(), "source": getattr(r, "source", None)} for r in rows],
                "waiting_for_a_rate": pending_summary(ctx.db)}


class GetPettyCash(BaseTool):
    name = "get_petty_cash"
    category = "read"
    description = (
        "Petty cash floats: who holds each, the balance, and how many expenses are waiting for approval. Use for "
        "'how much is left in Sara's petty cash?', 'any petty cash expenses to approve?'. Pure read."
    )
    InputSchema = NoInput

    async def run(self, ctx: ToolContext, args: NoInput) -> dict[str, Any]:
        from app.api.petty_cash import _account_read
        from app.models.petty_cash import PettyCashAccount
        rows = [_account_read(ctx.db, a) for a in
                ctx.db.execute(select(PettyCashAccount).order_by(PettyCashAccount.created_at)).scalars()]
        return {"accounts": [{k: r[k] for k in ("holder_name", "status", "balance", "pending_expenses")} for r in rows],
                "total_balance": sum(r["balance"] for r in rows),
                "pending_expenses": sum(r["pending_expenses"] for r in rows)}


class GetAuditTrailInput(BaseModel):
    entity_type: str | None = Field(None, description="e.g. transaction, invoice, pay_run, entity, budget, user")
    action: str | None = Field(None, description="e.g. create, update, delete, approve, login")
    username: str | None = Field(None, description="Who did it.")
    days: int = Field(30, ge=1, le=366, description="How far back.")
    limit: int = Field(25, ge=1, le=100)


class GetAuditTrail(BaseTool):
    name = "get_audit_trail"
    category = "read"
    description = (
        "The audit trail: who did what and when — created, changed, deleted or approved entries, invoices, pay "
        "runs, parties, settings — including what the assistant did (source ai-assistant). Use for 'who deleted "
        "that invoice?', 'what did Reza change yesterday?', 'what has the assistant posted this week?'. Pure read."
    )
    InputSchema = GetAuditTrailInput

    async def run(self, ctx: ToolContext, args: GetAuditTrailInput) -> dict[str, Any]:
        from app.models.audit_log import AuditLog
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        q = select(AuditLog).where(AuditLog.timestamp >= since).order_by(AuditLog.timestamp.desc())
        if args.entity_type:
            q = q.where(AuditLog.entity_type == args.entity_type.strip().lower())
        if args.action:
            q = q.where(AuditLog.action == args.action.strip().lower())
        if args.username:
            q = q.where(AuditLog.username.ilike(args.username.strip()))
        rows = ctx.db.execute(q.limit(args.limit)).scalars().all()
        return {"events": [{"at": r.timestamp.isoformat() if r.timestamp else None, "who": r.username,
                            "role": getattr(r, "actor_role", None), "source": getattr(r, "actor_source", None),
                            "action": r.action, "what": r.entity_type, "id": r.entity_id,
                            "detail": (r.detail or "")[:300]} for r in rows],
                "count": len(rows), "since": since.date().isoformat()}


# ---------------------------------------------------------------------------
# PROPOSALS
# ---------------------------------------------------------------------------

class ProposeLockPeriodInput(BaseModel):
    through: _date = Field(..., description="Lock every date up to and including this one (YYYY-MM-DD).")


class ProposeLockPeriod(BaseTool):
    name = "propose_lock_period"
    category = "proposal"
    description = (
        "Lock the books through a date so nothing can be posted, changed or deleted on or before it — month-end "
        "close. Owner only, as on the Settings page. Use for 'close the books for August', 'lock Shahrivar'. Check "
        "get_close_checklist first and say what's still open. Returns a confirm card."
    )
    InputSchema = ProposeLockPeriodInput

    async def run(self, ctx: ToolContext, args: ProposeLockPeriodInput) -> dict[str, Any]:
        from app.core.permissions import Perm, role_can
        from app.services.period_service import get_closed_period
        if not role_can(user_role(ctx.db, ctx.user_id), Perm.SETTINGS_WRITE):
            raise ToolError("Only the owner can lock the books — ask them, or they can do it in Settings.",
                            code="not_allowed")
        current = get_closed_period(ctx.db)
        if current and args.through <= current:
            raise ToolError(f"The books are already locked through {current.isoformat()}.", code="already_locked")
        if args.through >= _date.today():
            raise ToolError("Lock a date that has passed — today's and future entries would be refused.",
                            code="lock_in_future")
        payload = {"through": args.through.isoformat(), "was": current.isoformat() if current else None}
        token = _register(ctx, self.name, payload)
        summary = (f"Lock the books through {args.through.isoformat()}"
                   + (f" (now locked through {current.isoformat()})" if current else " (nothing is locked yet)")
                   + ". After this nothing dated on or before it can be posted, edited or deleted until an owner "
                   "reopens it.")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


class ProposeCreateRecurringRuleInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    direction: str = Field("payment", description="payment (money out) or receipt (money in)")
    frequency: str = Field("monthly", description="daily | weekly | monthly | quarterly | yearly")
    amount: int = Field(..., gt=0)
    start_date: _date = Field(..., description="The first date it posts.")
    end_date: _date | None = None
    account_code: str = Field(..., description="The expense (payment) or income (receipt) account.")
    bank_account_code: str | None = Field(None, description="The cash/bank account; the default bank when omitted.")
    party: str | None = Field(None, description="Supplier / customer name, if any.")


class ProposeCreateRecurringRule(BaseTool):
    name = "propose_create_recurring_rule"
    category = "proposal"
    description = (
        "Set up a payment or receipt that posts on a schedule — rent every month, a yearly insurance premium, a "
        "retainer received each quarter. Use for 'pay rent of 80 million on the 1st of every month', 'we get 5,000 "
        "from Acme every quarter'. Resolve the account with search_accounts first. Returns a confirm card."
    )
    InputSchema = ProposeCreateRecurringRuleInput

    async def run(self, ctx: ToolContext, args: ProposeCreateRecurringRuleInput) -> dict[str, Any]:
        from app.models.account import Account
        from app.services.account_resolver import resolve_account_code
        direction = args.direction.strip().lower()
        frequency = args.frequency.strip().lower()
        if direction not in ("payment", "receipt"):
            raise ToolError("direction is payment or receipt", code="bad_direction")
        if frequency not in ("daily", "weekly", "monthly", "quarterly", "yearly"):
            raise ToolError("frequency is daily, weekly, monthly, quarterly or yearly", code="bad_frequency")
        if args.end_date and args.end_date < args.start_date:
            raise ToolError("It ends before it starts.", code="bad_dates")
        codes = {args.account_code} | ({args.bank_account_code} if args.bank_account_code else set())
        have = {c for (c,) in ctx.db.execute(select(Account.code).where(Account.code.in_(codes))).all()}
        missing = sorted(codes - have)
        if missing:
            raise ToolError(f"No account {', '.join(missing)} — use search_accounts.", code="account_not_found")
        ent = None
        if args.party:
            ent = _find_entity(ctx, args.party, ("supplier", "client", "customer", "employee", "bank"))
            if ent is None:
                raise ToolError(f"No party named {args.party!r}", code="entity_not_found")
        bank = args.bank_account_code or resolve_account_code(ctx.db, "bank")
        payload = {"name": args.name.strip(), "direction": direction, "frequency": frequency, "amount": args.amount,
                   "start_date": args.start_date.isoformat(), "end_date": args.end_date.isoformat() if args.end_date else None,
                   "counter_account_code": args.account_code, "bank_account_code": bank,
                   "entity_id": str(ent.id) if ent else None, "party": ent.name if ent else None}
        token = _register(ctx, self.name, payload)
        verb = "Pay" if direction == "payment" else "Receive"
        summary = (f"{verb} {args.amount:,} {frequency} from {args.start_date.isoformat()}"
                   + (f" until {args.end_date.isoformat()}" if args.end_date else "")
                   + f" — {args.name.strip()}: account {args.account_code}, "
                   + ("from" if direction == "payment" else "into") + f" {bank}"
                   + (f", {ent.name}" if ent else "") + ". Each one posts on its date.")
        return {"confirmation_token": str(token), "status": "pending", "summary": summary, "preview": payload}


def register_ops_tools(registry) -> None:
    for tool in (ListPurchaseOrders(), ListRecurringRules(), GetCapTable(), GetExchangeRates(), GetPettyCash(),
                 GetAuditTrail(), ProposeLockPeriod(), ProposeCreateRecurringRule()):
        registry.register(tool)
