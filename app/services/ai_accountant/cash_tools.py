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


def register_cash_tools(registry) -> None:
    registry.register(GetCashPosition())
