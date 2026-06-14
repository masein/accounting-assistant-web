"""Read-only tools exposed to Claude.

These never write to the ledger — they answer questions and surface
the data Claude needs to draft a proposal. Each tool validates its
input via Pydantic before touching the DB and returns a small,
structured dict that fits comfortably in Claude's context.

Tools in this file:

* ``find_entity`` — fuzzy + exact lookup with a ``confidence`` score
  per match. Returns up to 10 candidates so Claude can disambiguate.
* ``list_entities`` — paginated browse by type / filter.
* ``query_ledger`` — sums and lists over a date range / account /
  entity.
* ``get_account_balance`` — single account balance as of a date.
* ``get_company_defaults`` — currency, fiscal year, reporting locale.
"""
from __future__ import annotations

import difflib
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.app_setting import AppSetting
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.locale_service import get_display_calendar, get_reporting_locale
from app.services.reporting.common import (
    ASSET,
    EQUITY,
    EXPENSE,
    LIABILITY,
    REVENUE,
    balance_from_turnovers,
    classify_account_code,
)

from .base import BaseTool, ToolContext, ToolError


# ---------------------------------------------------------------------------
# find_entity
# ---------------------------------------------------------------------------


class FindEntityInput(BaseModel):
    query: str = Field(
        ...,
        description="The name (or partial name) to search for. Case-insensitive.",
        min_length=1,
    )
    type: Literal["client", "bank", "employee", "supplier"] | None = Field(
        None,
        description=(
            "Optional role filter. Omit to search across all entity types "
            "(useful when the user says 'Kim' without specifying whether "
            "Kim is the client or the employee)."
        ),
    )
    limit: int = Field(10, ge=1, le=25, description="Max candidates to return.")


class FindEntity(BaseTool):
    name = "find_entity"
    category = "read"
    description = (
        "Find clients, employees, suppliers or banks by name. Returns a ranked list "
        "of candidates with a confidence score in [0, 1]. Call this ONCE per name, "
        "then converge: if the top result has confidence ≥ 0.80 (or is the only "
        "match) use it; if several are plausible, list 2–3 and ask the user to pick; "
        "if the best is < 0.50, propose with NO entity link (entity links are "
        "optional). Do NOT call this tool again for the same name — re-searching "
        "wastes the turn budget and dead-ends the request."
    )
    InputSchema = FindEntityInput

    async def run(self, ctx: ToolContext, args: FindEntityInput) -> dict[str, Any]:
        q = args.query.strip()
        if not q:
            raise ToolError("query must be non-empty")

        stmt = select(Entity)
        if args.type:
            stmt = stmt.where(Entity.type == args.type)

        # Pull all candidates the user could plausibly mean, then score
        # client-side. SQLite (used in tests) doesn't have trigram support;
        # difflib is portable and fast for small entity tables.
        all_rows = ctx.db.execute(stmt).scalars().all()
        scored: list[tuple[float, Entity]] = []
        q_lower = q.lower()
        for row in all_rows:
            ratio = difflib.SequenceMatcher(None, q_lower, row.name.lower()).ratio()
            # Boost prefix matches — typing "Kim" should rank "Kim Nguyen"
            # above "Joachim".
            if row.name.lower().startswith(q_lower):
                ratio = min(1.0, ratio + 0.15)
            if q_lower in row.name.lower():
                ratio = min(1.0, ratio + 0.05)
            scored.append((ratio, row))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[: args.limit]

        return {
            "query": q,
            "matches": [
                {
                    "entity_id": str(e.id),
                    "name": e.name,
                    "type": e.type,
                    "code": e.code,
                    "confidence": round(score, 3),
                }
                for (score, e) in top
                if score >= 0.30  # filter pure-noise matches
            ],
        }


# ---------------------------------------------------------------------------
# list_entities
# ---------------------------------------------------------------------------


class ListEntitiesInput(BaseModel):
    type: Literal["client", "bank", "employee", "supplier"] | None = None
    limit: int = Field(50, ge=1, le=200)
    name_contains: str | None = Field(
        None,
        description="Optional case-insensitive substring filter.",
    )


class ListEntities(BaseTool):
    name = "list_entities"
    category = "read"
    description = (
        "List entities (clients, banks, employees, suppliers) for browsing. Use this "
        "when the user wants an overview (\"show me all our clients\", \"who do we "
        "owe money to\"). For looking up a specific entity by name, prefer "
        "``find_entity``."
    )
    InputSchema = ListEntitiesInput

    async def run(self, ctx: ToolContext, args: ListEntitiesInput) -> dict[str, Any]:
        stmt = select(Entity).order_by(Entity.name).limit(args.limit)
        if args.type:
            stmt = stmt.where(Entity.type == args.type)
        if args.name_contains:
            needle = f"%{args.name_contains.lower()}%"
            stmt = stmt.where(func.lower(Entity.name).like(needle))
        rows = ctx.db.execute(stmt).scalars().all()
        return {
            "count": len(rows),
            "entities": [
                {
                    "entity_id": str(e.id),
                    "name": e.name,
                    "type": e.type,
                    "code": e.code,
                }
                for e in rows
            ],
        }


# ---------------------------------------------------------------------------
# query_ledger
# ---------------------------------------------------------------------------


class QueryLedgerInput(BaseModel):
    from_date: date | None = Field(
        None, description="Inclusive lower bound. Defaults to the start of the current year."
    )
    to_date: date | None = Field(
        None, description="Inclusive upper bound. Defaults to today."
    )
    account_code: str | None = Field(
        None,
        description=(
            "Account code (or code prefix — e.g. '11' for all current assets, '61' "
            "for all SG&A). Omit to query every account."
        ),
    )
    entity_id: str | None = Field(
        None, description="Restrict to transactions linked to this entity."
    )
    description_contains: str | None = Field(
        None,
        description="Filter by case-insensitive substring of the transaction description.",
    )
    group_by: Literal["none", "month", "account"] = Field(
        "none",
        description=(
            "Aggregation. 'none' returns up to 50 individual transactions; 'month' "
            "returns sums per calendar month; 'account' returns sums per account."
        ),
    )
    limit: int = Field(50, ge=1, le=200)


class QueryLedger(BaseTool):
    name = "query_ledger"
    category = "read"
    description = (
        "Query the general ledger. Answers \"how much did we spend on X?\", \"what's "
        "outstanding from Client Y?\", \"show me last 10 transactions on account 6110\". "
        "Returns sums + a small sample of underlying rows. Pure read — never modifies "
        "the books."
    )
    InputSchema = QueryLedgerInput

    async def run(self, ctx: ToolContext, args: QueryLedgerInput) -> dict[str, Any]:
        today = date.today()
        from_d = args.from_date or date(today.year, 1, 1)
        to_d = args.to_date or today

        # Build the line-level query joined to its transaction.
        stmt = (
            select(Transaction, TransactionLine, Account)
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Transaction.deleted_at.is_(None),
                Transaction.date >= from_d,
                Transaction.date <= to_d,
            )
            .order_by(Transaction.date.desc(), Transaction.id)
        )

        if args.account_code:
            stmt = stmt.where(Account.code.like(f"{args.account_code}%"))

        if args.entity_id:
            stmt = stmt.where(
                Transaction.id.in_(
                    select(TransactionEntity.transaction_id).where(
                        TransactionEntity.entity_id == args.entity_id
                    )
                )
            )

        if args.description_contains:
            stmt = stmt.where(
                func.lower(Transaction.description).like(
                    f"%{args.description_contains.lower()}%"
                )
            )

        rows = ctx.db.execute(stmt).all()

        total_debit = sum(int(line.debit or 0) for (_t, line, _a) in rows)
        total_credit = sum(int(line.credit or 0) for (_t, line, _a) in rows)
        # Net by account-nature: for a single asset/expense query this is
        # the natural "amount spent / paid"; for revenue / liability it's
        # the natural "amount received / owed".
        signed_net = 0
        if args.account_code:
            sample = classify_account_code(args.account_code)
            if sample in (ASSET, EXPENSE):
                signed_net = total_debit - total_credit
            elif sample in (LIABILITY, EQUITY, REVENUE):
                signed_net = total_credit - total_debit

        result: dict[str, Any] = {
            "period": {"from": from_d.isoformat(), "to": to_d.isoformat()},
            "total_debit": total_debit,
            "total_credit": total_credit,
            "signed_net": signed_net,
            "row_count": len(rows),
        }

        if args.group_by == "month":
            buckets: dict[str, dict[str, int]] = {}
            for txn, line, _acc in rows:
                key = txn.date.strftime("%Y-%m")
                slot = buckets.setdefault(key, {"debit": 0, "credit": 0, "count": 0})
                slot["debit"] += int(line.debit or 0)
                slot["credit"] += int(line.credit or 0)
                slot["count"] += 1
            result["by_month"] = [
                {"month": m, **buckets[m]}
                for m in sorted(buckets)
            ]
        elif args.group_by == "account":
            buckets = {}
            for _txn, line, acc in rows:
                key = acc.code
                slot = buckets.setdefault(
                    key,
                    {"name": acc.name, "debit": 0, "credit": 0, "count": 0},
                )
                slot["debit"] += int(line.debit or 0)
                slot["credit"] += int(line.credit or 0)
                slot["count"] += 1
            result["by_account"] = [
                {"account_code": code, **buckets[code]}
                for code in sorted(buckets)
            ]
        else:
            result["sample"] = [
                {
                    "transaction_id": str(txn.id),
                    "date": txn.date.isoformat(),
                    "account_code": acc.code,
                    "account_name": acc.name,
                    "debit": int(line.debit or 0),
                    "credit": int(line.credit or 0),
                    "description": txn.description,
                }
                for (txn, line, acc) in rows[: args.limit]
            ]
        return result


# ---------------------------------------------------------------------------
# get_account_balance
# ---------------------------------------------------------------------------


class GetAccountBalanceInput(BaseModel):
    account_code: str = Field(..., description="Exact account code (e.g. '1110').")
    as_of: date | None = Field(
        None, description="Snapshot date. Defaults to today."
    )


class GetAccountBalance(BaseTool):
    name = "get_account_balance"
    category = "read"
    description = (
        "Return the balance of one account as of a date — useful for cash-on-hand "
        "queries, checking AR/AP, or confirming a counterparty balance before "
        "proposing a transaction."
    )
    InputSchema = GetAccountBalanceInput

    async def run(self, ctx: ToolContext, args: GetAccountBalanceInput) -> dict[str, Any]:
        code = args.account_code.strip()
        as_of = args.as_of or date.today()

        acc = ctx.db.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
        if acc is None:
            raise ToolError(f"Account {code!r} not found", code="account_not_found")

        # Sum the lines for this account up to as_of.
        stmt = (
            select(
                func.coalesce(func.sum(TransactionLine.debit), 0),
                func.coalesce(func.sum(TransactionLine.credit), 0),
            )
            .select_from(TransactionLine)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(
                TransactionLine.account_id == acc.id,
                Transaction.deleted_at.is_(None),
                Transaction.date <= as_of,
            )
        )
        debit_sum, credit_sum = ctx.db.execute(stmt).one()
        acc_type = classify_account_code(code)
        balance = balance_from_turnovers(acc_type, int(debit_sum or 0), int(credit_sum or 0))
        return {
            "account_code": code,
            "account_name": acc.name,
            "account_type": acc_type,
            "as_of": as_of.isoformat(),
            "debit_total": int(debit_sum or 0),
            "credit_total": int(credit_sum or 0),
            "balance": int(balance),
        }


# ---------------------------------------------------------------------------
# search_accounts
# ---------------------------------------------------------------------------


# Locale-aware category → (synonyms, candidate account codes). The synonyms
# let a plain-English request ("office supplies", "cash") match a chart whose
# accounts are worded differently or use opaque nominal codes; the candidate
# codes pin the resolution to the right account when it exists. Only codes
# present in the seeded chart are ever returned (verified at query time).
_ACCOUNT_ALIASES: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "uk": {
        "cash / bank": (["cash", "petty cash", "bank", "current account", "in hand"], ["1200", "1220", "1210"]),
        "office supplies": (["office supplies", "stationery", "printing", "office expenses", "telephone", "phone"], ["7600"]),
        "rent": (["rent", "rates"], ["7200"]),
        "utilities": (["utilities", "light", "heat", "power", "electricity", "gas", "water"], ["7300"]),
        "motor / travel": (["motor", "vehicle", "fuel", "travel", "entertainment", "mileage"], ["7400", "7500"]),
        "wages / salary": (["wages", "salary", "salaries", "payroll", "staff"], ["7100", "7000"]),
        "sales / revenue": (["sales", "revenue", "turnover", "income"], ["4000", "4200"]),
        "purchases / cogs": (["purchases", "cogs", "cost of sales", "cost of goods", "stock", "materials"], ["5000"]),
        "trade debtors": (["debtors", "receivable", "accounts receivable", "ar", "owed to us"], ["1100"]),
        "trade creditors": (["creditors", "payable", "accounts payable", "ap", "we owe", "suppliers"], ["2100"]),
        "vat": (["vat", "sales tax", "tax payable"], ["2200", "1400"]),
        "bank charges / fees": (["bank charges", "bank fees", "commission", "fee"], ["8000"]),
        "professional fees": (["professional fees", "accountant", "legal", "consultant", "audit"], ["7800"]),
        "repairs": (["repairs", "maintenance"], ["7700"]),
    },
    "ir": {
        "cash / bank": (["cash", "bank", "petty cash", "موجودی", "نقد", "بانک", "صندوق"], ["1110"]),
        "operating expense": (["office supplies", "stationery", "rent", "utilities", "expense", "هزینه", "اجاره", "ملزومات", "قبض"], ["6112"]),
        "wages / salary": (["wages", "salary", "payroll", "حقوق", "دستمزد"], ["6110"]),
        "sales / revenue": (["sales", "revenue", "income", "فروش", "درآمد"], ["4110"]),
        "receivable": (["receivable", "debtors", "ar", "دریافتنی"], ["1112"]),
        "payable": (["payable", "creditors", "ap", "پرداختنی"], ["2110"]),
        "financial expense": (["interest", "finance", "bank charge", "مالی", "بهره", "کارمزد"], ["6210"]),
        "capital / equity": (["capital", "equity", "owner", "سرمایه"], ["3110"]),
    },
    "default": {
        "cash / bank": (["cash", "bank", "petty cash"], ["1110", "1200"]),
        "expense": (["expense", "supplies", "rent", "utilities"], ["6112", "7600"]),
        "wages / salary": (["wages", "salary", "payroll"], ["6110", "7100"]),
        "sales / revenue": (["sales", "revenue", "income"], ["4110", "4000"]),
    },
}


def _normal_balance(acc_type: str) -> str:
    return "debit" if acc_type in (ASSET, EXPENSE) else "credit"


class SearchAccountsInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Plain-language category or account name to resolve to a code, e.g. "
            "'office supplies', 'cash', 'rent', 'sales', 'bank charges'. "
            "Matches the chart by name AND code, with a synonym map so common "
            "categories resolve even when the chart wording differs."
        ),
        min_length=1,
    )
    account_type: Literal["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"] | None = Field(
        None, description="Optional filter by statement nature."
    )
    limit: int = Field(8, ge=1, le=25, description="Max accounts to return.")


class SearchAccounts(BaseTool):
    name = "search_accounts"
    category = "read"
    description = (
        "Resolve a plain-language category (e.g. 'office supplies', 'cash', "
        "'rent', 'sales') to actual chart-of-accounts codes. Returns the best "
        "matching accounts as {code, name, type, normal_balance}. ALWAYS use "
        "this to find the account_code for each leg before "
        "propose_create_transaction — do NOT guess code prefixes. One search "
        "per category is enough."
    )
    InputSchema = SearchAccountsInput

    async def run(self, ctx: ToolContext, args: SearchAccountsInput) -> dict[str, Any]:
        q = args.query.strip().lower()
        if not q:
            raise ToolError("query must be non-empty")

        locale = get_reporting_locale(ctx.db)
        aliases = _ACCOUNT_ALIASES.get(locale, _ACCOUNT_ALIASES["default"])

        # An alias whose synonym appears IN the query (e.g. query "office
        # supplies" contains synonym "office supplies") contributes its
        # preferred codes — the category→code mapping. We only use aliases for
        # this boost, NOT to expand the fuzzy terms: dumping every synonym into
        # the name match cross-contaminates (e.g. "bank charges" would pull in
        # "petty cash"). Match is one-directional (synonym ⊂ query) so "sales"
        # doesn't fire the "cost of sales" synonym.
        preferred_codes: set[str] = set()
        for _label, (synonyms, codes) in aliases.items():
            if any(s in q for s in synonyms):
                preferred_codes.update(codes)

        from app.models.account import AccountLevel

        rows = ctx.db.execute(select(Account)).scalars().all()
        scored: list[tuple[float, Account, str]] = []
        for acc in rows:
            # Only postable (leaf) accounts — group/header accounts can't take
            # a journal line, so they'd be useless to propose against.
            if getattr(acc, "level", None) == AccountLevel.GROUP:
                continue
            name_l = (acc.name or "").lower()
            code_l = (acc.code or "").lower()
            acc_type = classify_account_code(acc.code or "")
            if args.account_type and acc_type != args.account_type:
                continue
            # Direct fuzzy/substring score of the raw query against the name.
            score = difflib.SequenceMatcher(None, q, name_l).ratio()
            if q in name_l:
                score = max(score, 0.85)
            if q == code_l or q in code_l:
                score = max(score, 0.9)
            # Strong boost when the chart actually has an alias's preferred code.
            if (acc.code or "") in preferred_codes:
                score = max(score, 0.97)
            if score >= 0.45:
                scored.append((score, acc, acc_type))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[: args.limit]
        return {
            "query": args.query,
            "reporting_locale": locale,
            "matches": [
                {
                    "code": acc.code,
                    "name": acc.name,
                    "type": acc_type,
                    "normal_balance": _normal_balance(acc_type),
                    "confidence": round(score, 3),
                }
                for (score, acc, acc_type) in top
            ],
        }


# ---------------------------------------------------------------------------
# get_financial_statement
# ---------------------------------------------------------------------------


_INCEPTION = date(1900, 1, 1)


class GetFinancialStatementInput(BaseModel):
    statement: Literal["balance_sheet", "income_statement", "trial_balance", "cash_flow"] = Field(
        ..., description="Which statement to build."
    )
    from_date: date | None = Field(
        None, description="Period start (income_statement / cash_flow). Defaults to the start of the current year."
    )
    to_date: date | None = Field(None, description="As-of / period end. Defaults to today.")
    currency: str | None = Field(None, description="Filter by currency; defaults to the reporting currency.")


class GetFinancialStatement(BaseTool):
    name = "get_financial_statement"
    category = "read"
    description = (
        "Build a complete financial statement deterministically from the "
        "ledger: balance_sheet (Assets = Liabilities + Equity), "
        "income_statement (P&L), trial_balance (total debits == total "
        "credits), or cash_flow. ALWAYS use this for 'balance sheet', 'P&L', "
        "'trial balance' or 'cash flow' questions — never hand-sum individual "
        "account balances. Relay the returned totals; the figures already "
        "balance."
    )
    InputSchema = GetFinancialStatementInput

    async def run(self, ctx: ToolContext, args: GetFinancialStatementInput) -> dict[str, Any]:
        from app.services.reporting.repository import trial_balance_rows

        to_date = args.to_date or date.today()
        currency = args.currency  # None → all currencies (repository handles)

        def _balances(from_date: date) -> list[tuple[str, str, str, int]]:
            """(code, name, type, signed_balance) over [from_date, to_date]."""
            out = []
            for code, name, d, c in trial_balance_rows(ctx.db, from_date, to_date, currency=currency):
                t = classify_account_code(code)
                out.append((code, name, t, balance_from_turnovers(t, d, c)))
            return out

        if args.statement == "trial_balance":
            rows = _balances(_INCEPTION)
            lines, tot_dr, tot_cr = [], 0, 0
            for code, name, t, bal in rows:
                if bal == 0:
                    continue
                dr = bal if t in (ASSET, EXPENSE) else 0
                cr = bal if t not in (ASSET, EXPENSE) else 0
                # A negative natural balance flips the column.
                if bal < 0:
                    dr, cr = (0, -bal) if t in (ASSET, EXPENSE) else (-bal, 0)
                tot_dr += dr
                tot_cr += cr
                lines.append({"code": code, "name": name, "debit": dr, "credit": cr})
            return {
                "statement": "trial_balance", "as_of": to_date.isoformat(),
                "lines": lines, "total_debit": tot_dr, "total_credit": tot_cr,
                "balanced": tot_dr == tot_cr,
            }

        if args.statement in ("income_statement", "cash_flow"):
            from_date = args.from_date or date(to_date.year, 1, 1)

        if args.statement == "income_statement":
            rows = _balances(from_date)
            revenue = [(c, n, b) for c, n, t, b in rows if t == REVENUE and b]
            expense = [(c, n, b) for c, n, t, b in rows if t == EXPENSE and b]
            rev_total = sum(b for _, _, b in revenue)
            exp_total = sum(b for _, _, b in expense)
            return {
                "statement": "income_statement",
                "period": {"from": from_date.isoformat(), "to": to_date.isoformat()},
                "revenue": [{"code": c, "name": n, "amount": b} for c, n, b in revenue],
                "expenses": [{"code": c, "name": n, "amount": b} for c, n, b in expense],
                "revenue_total": rev_total, "expense_total": exp_total,
                "net_income": rev_total - exp_total,
            }

        if args.statement == "cash_flow":
            from app.services.cash_service import cash_on_hand
            opening = cash_on_hand(ctx.db, locale=get_reporting_locale(ctx.db),
                                   currency=currency, as_of=from_date - timedelta(days=1))
            closing = cash_on_hand(ctx.db, locale=get_reporting_locale(ctx.db),
                                   currency=currency, as_of=to_date)
            return {
                "statement": "cash_flow",
                "period": {"from": from_date.isoformat(), "to": to_date.isoformat()},
                "opening_cash": opening, "closing_cash": closing,
                "net_change_in_cash": closing - opening,
            }

        # balance_sheet — cumulative balances from inception.
        rows = _balances(_INCEPTION)
        assets = [(c, n, b) for c, n, t, b in rows if t == ASSET and b]
        liabilities = [(c, n, b) for c, n, t, b in rows if t == LIABILITY and b]
        equity_posted = [(c, n, b) for c, n, t, b in rows if t == EQUITY and b]
        rev_total = sum(b for _, _, t, b in rows if t == REVENUE)
        exp_total = sum(b for _, _, t, b in rows if t == EXPENSE)
        net_income = rev_total - exp_total  # retained earnings, folded into equity
        assets_total = sum(b for _, _, b in assets)
        liab_total = sum(b for _, _, b in liabilities)
        equity_total = sum(b for _, _, b in equity_posted) + net_income
        return {
            "statement": "balance_sheet", "as_of": to_date.isoformat(),
            "assets": [{"code": c, "name": n, "amount": b} for c, n, b in assets],
            "liabilities": [{"code": c, "name": n, "amount": b} for c, n, b in liabilities],
            "equity": [{"code": c, "name": n, "amount": b} for c, n, b in equity_posted],
            "retained_earnings": net_income,
            "assets_total": assets_total,
            "liabilities_total": liab_total,
            "equity_total": equity_total,
            "balanced": assets_total == liab_total + equity_total,
        }


# ---------------------------------------------------------------------------
# get_company_defaults
# ---------------------------------------------------------------------------


class GetCompanyDefaultsInput(BaseModel):
    pass


class GetCompanyDefaults(BaseTool):
    name = "get_company_defaults"
    category = "read"
    description = (
        "Return the company defaults: reporting locale (ir / uk / default), display "
        "calendar (gregorian / jalali), default currency, today's date. Call this "
        "once at the start of a conversation to anchor every subsequent proposal."
    )
    InputSchema = GetCompanyDefaultsInput

    async def run(self, ctx: ToolContext, args: GetCompanyDefaultsInput) -> dict[str, Any]:
        locale = get_reporting_locale(ctx.db)
        calendar = get_display_calendar(ctx.db)
        # Default currency by locale: IRR for Iran, GBP for UK, IRR otherwise
        # (matches the existing transaction model default).
        currency = {"ir": "IRR", "uk": "GBP"}.get(locale, "IRR")

        # Pull any explicit default-currency override from app_settings.
        row = ctx.db.execute(
            select(AppSetting).where(AppSetting.key == "default_currency")
        ).scalar_one_or_none()
        if row and (row.value or "").strip():
            currency = row.value.strip()

        return {
            "reporting_locale": locale,
            "display_calendar": calendar,
            "default_currency": currency,
            "today": date.today().isoformat(),
        }


# ---------------------------------------------------------------------------
# get_tax_summary
# ---------------------------------------------------------------------------


class GetTaxSummaryInput(BaseModel):
    from_date: date | None = Field(None, description="Period start (defaults to the current quarter start).")
    to_date: date | None = Field(None, description="Period end (defaults to today).")
    currency: str | None = Field(None, description="Filter by currency; defaults to all.")


class GetTaxSummary(BaseTool):
    name = "get_tax_summary"
    category = "read"
    description = (
        "Estimate VAT / sales tax for a period: output tax (on sales), input "
        "tax (on purchases) and net tax = output − input, computed from the "
        "tax rates recorded on invoices. Use this for 'how much tax/VAT do I "
        "owe' questions. The result includes 'assumptions' (the rates used) and "
        "a 'caveat'. ALWAYS relay the caveat to the user verbatim — even if they "
        "say 'just give me the number' — and state the assumptions. Do NOT "
        "invent tax law, rates for other jurisdictions, or filing deadlines; if "
        "asked, say the current rules must be verified with a tax professional."
    )
    InputSchema = GetTaxSummaryInput

    async def run(self, ctx: ToolContext, args: GetTaxSummaryInput) -> dict[str, Any]:
        from app.services.tax_service import compute_tax_summary

        today = date.today()
        to_date = args.to_date or today
        from_date = args.from_date
        if from_date is None:
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            from_date = date(today.year, q_start_month, 1)
        return compute_tax_summary(ctx.db, from_date, to_date, currency=args.currency)


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


def register_read_tools(registry) -> None:
    registry.register(FindEntity())
    registry.register(ListEntities())
    registry.register(QueryLedger())
    registry.register(GetAccountBalance())
    registry.register(SearchAccounts())
    registry.register(GetFinancialStatement())
    registry.register(GetTaxSummary())
    registry.register(GetCompanyDefaults())
