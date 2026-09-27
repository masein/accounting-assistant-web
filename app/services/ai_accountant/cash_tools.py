"""Read tool: the cash position — every cash and bank account together.

QA 2026-09-24 5.9: "how much cash do we have?" was answered from account 1110
alone (−44 M, "an overdraft") while the bank account 1111 held +4 M. The
model must not pick one account for that question; this tool lists them all
and adds them up.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import BaseTool, ToolContext
from app.services.cash_service import cash_account_predicate
from app.services.locale_service import get_reporting_locale


def cash_account_codes(db, *, locale: str | None, mode: str = "default") -> list[str]:
    """Codes of every account that holds money: the locale's cash/bank
    accounts, each bank entity's own GL account, and in personal mode the
    bank/card (1110) and cash-on-hand (1120) accounts."""
    is_cash = cash_account_predicate(locale)
    codes: set[str] = set()
    for acc in db.execute(select(Account)).scalars().all():
        code = acc.code or ""
        if is_cash(code):
            codes.add(code)
        if mode == "personal" and code in ("1110", "1120"):
            codes.add(code)
    for ent in db.execute(select(Entity).where(Entity.type == "bank")).scalars().all():
        if ent.code:
            codes.add(ent.code.strip())
    return sorted(codes)


class GetCashPositionInput(BaseModel):
    as_of: date | None = Field(None, description="Snapshot date. Defaults to today.")


class GetCashPosition(BaseTool):
    name = "get_cash_position"
    category = "read"
    description = (
        "How much money there is right now: the balance of EVERY cash and bank account "
        "(cash box, each bank account, card) as of a date, plus their total. Use it for "
        "'how much cash/money do we have', 'چقدر پول داریم', 'what's in the bank' — never "
        "answer those from a single account. Pure read."
    )
    InputSchema = GetCashPositionInput

    async def run(self, ctx: ToolContext, args: GetCashPositionInput) -> dict[str, Any]:
        from app.services.fx_service import get_reporting_currency

        as_of = args.as_of or date.today()
        locale = get_reporting_locale(ctx.db)
        codes = cash_account_codes(ctx.db, locale=locale, mode=getattr(ctx, "mode", "default"))
        rows = ctx.db.execute(
            select(Account.code, Account.name,
                   func.coalesce(func.sum(TransactionLine.debit), 0),
                   func.coalesce(func.sum(TransactionLine.credit), 0))
            .join(TransactionLine, TransactionLine.account_id == Account.id)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(Account.code.in_(codes), Transaction.deleted_at.is_(None), Transaction.date <= as_of)
            .group_by(Account.code, Account.name)
        ).all()
        by_code = {code: (name, int(d or 0) - int(c or 0)) for code, name, d, c in rows}
        names = {a.code: a.name for a in ctx.db.execute(select(Account).where(Account.code.in_(codes))).scalars().all()}
        accounts = [
            {"account_code": code, "account_name": by_code.get(code, (names.get(code, ""), 0))[0] or names.get(code, ""),
             "balance": by_code.get(code, ("", 0))[1]}
            for code in codes
        ]
        total = sum(a["balance"] for a in accounts)
        out: dict[str, Any] = {
            "as_of": as_of.isoformat(),
            "currency": get_reporting_currency(ctx.db),
            "accounts": accounts,
            "total": total,
            "account_count": len(accounts),
        }
        negatives = [a for a in accounts if a["balance"] < 0]
        if negatives:
            out["note"] = ("Some accounts are negative (" + ", ".join(f"{a['account_code']} {a['account_name']}" for a in negatives)
                           + ") — likely spending recorded before the money that funded it; the total above nets them.")
        return out


class ForecastDelay(BaseModel):
    entity_id: str | None = Field(None, description="Customer/supplier id (from find_entity).")
    entity_name: str | None = Field(None, description="Or the name; matched loosely.")
    days: int = Field(..., ge=-90, le=365, description="Extra days they take to pay (or get paid).")


class ForecastOneOff(BaseModel):
    on: date = Field(..., description="Date of the one-off flow.")
    amount: int = Field(..., description="Positive = money in, negative = money out.")
    label: str | None = Field(None, max_length=120)


class GetCashForecastInput(BaseModel):
    weeks: int = Field(13, ge=1, le=26, description="How many weeks ahead (default 13).")
    currency: str | None = Field(None, description="Currency to forecast. Defaults to the reporting currency.")
    bounce_commitment_ids: list[str] = Field(default_factory=list, max_length=100,
                                             description="What if these cheques/installments never settle (ids from list_commitments).")
    bounce_matching: str | None = Field(None, max_length=120, description=(
        "Or match pending cheques/installments by bank, counterparty, title or cheque number — "
        "e.g. 'Mellat' for 'what if the Mellat cheque bounces'."))
    skip_invoice_ids: list[str] = Field(default_factory=list, max_length=100,
                                        description="What if these invoices are never paid (ids from list_invoices).")
    delays: list[ForecastDelay] = Field(default_factory=list, max_length=50,
                                        description="What if a customer pays (or we pay a supplier) N days later.")
    one_offs: list[ForecastOneOff] = Field(default_factory=list, max_length=50,
                                           description="What if a one-off receipt/payment happens (a loan, a big purchase).")


def _compact_weeks(weeks: list[dict], per_week: int = 4) -> list[dict]:
    out = []
    for w in weeks:
        items = sorted(w["items"], key=lambda i: -abs(i["amount"]))
        out.append({k: w[k] for k in ("week_start", "inflow", "outflow", "closing", "risk")} | {
            "main_items": [{k: i.get(k) for k in ("date", "label", "amount", "entity_name", "note", "overdue")}
                           for i in items[:per_week]],
            "other_items": max(0, len(items) - per_week),
        })
    return out


def _summary(f: dict) -> dict:
    return {"opening_cash": f["opening_cash"], "closing_cash": f["closing_cash"], "lowest": f["lowest"],
            "first_negative_week": f["first_negative_week"]}


class GetCashForecast(BaseTool):
    name = "get_cash_forecast"
    category = "read"
    description = (
        "The 13-week cash forecast: today's cash, then week by week the expected money in and out — "
        "open sales invoices on the date THAT customer usually pays (learned from their paid invoices), "
        "bills, pending cheques and installments, payroll, recurring payments and invoices, plus the "
        "usual unscheduled week. Returns the lowest balance, the first week cash goes negative and the "
        "main items each week. Pass a scenario to ask 'what if': a cheque bounces (bounce_matching='Mellat' "
        "or ids), an invoice isn't paid, a customer pays 30 days late, a one-off loan or purchase — the "
        "result then compares base vs scenario. Use it for 'will we have enough cash', 'when do we run "
        "short', 'پیش‌بینی نقدینگی', 'اگه چک ملت برگشت بخوره'. Pure read, changes nothing."
    )
    InputSchema = GetCashForecastInput

    async def run(self, ctx: ToolContext, args: GetCashForecastInput) -> dict[str, Any]:
        from app.services.cash_forecast import compare, forecast, resolve_scenario

        scenario, notes = resolve_scenario(
            ctx.db, bounce_ids=args.bounce_commitment_ids, bounce_matching=args.bounce_matching,
            skip_invoice_ids=args.skip_invoice_ids,
            delays=[d.model_dump() for d in args.delays],
            one_offs=[{"date": o.on.isoformat(), "amount": o.amount, "label": o.label} for o in args.one_offs],
        )
        kw = {"weeks": args.weeks, "currency": args.currency}
        if scenario.empty:
            f = forecast(ctx.db, **kw)
            out = {"currency": f["currency"], "as_of": f["as_of"], **_summary(f),
                   "weeks": _compact_weeks(f["weeks"]), "learned": f["learned"], "baseline": f["baseline"],
                   "doubtful": {k: f["doubtful"][k] for k in ("count", "total", "after_days")}
                   | {"invoices": f["doubtful"]["invoices"][:5]},
                   "notes": f["notes"]}
        else:
            c = compare(ctx.db, scenario, **kw)
            out = {"currency": c["base"]["currency"], "as_of": c["base"]["as_of"],
                   "base": _summary(c["base"]), "scenario": _summary(c["scenario"]),
                   "scenario_applied": c["scenario"]["scenario"],
                   "closing_difference": c["closing_difference"], "lowest_difference": c["lowest_difference"],
                   "scenario_weeks": _compact_weeks(c["scenario"]["weeks"], per_week=3),
                   "notes": c["base"]["notes"]}
        if notes:
            out["scenario_notes"] = notes
        return out


def register_cash_tools(registry) -> None:
    registry.register(GetCashPosition())
    registry.register(GetCashForecast())
